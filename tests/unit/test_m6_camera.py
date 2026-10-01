"""M6 — camera moves that match the shot, and pans that don't shiver."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from agents.video_agent import animator, camera


def test_a_move_is_chosen_for_what_the_shot_is_doing():
    """It used to be a round-robin over a fixed list, so the move had nothing
    to do with what was on screen."""
    # A face delivering a line: let the performance carry it.
    assert camera.move_for("lip_sync", "tense", 0) == "static_hold"
    # An establishing wide opens the space up.
    assert camera.move_for("establishing", "calm", 0) in ("pull_out", "pan_right",
                                                          "drift", "tilt_up")
    # Tension pushes in; hope lifts.
    assert camera.move_for("detail", "tense", 0) == "push_in"
    assert camera.move_for("detail", "hopeful", 0) == "tilt_up"
    assert camera.move_for("detail", "melancholic", 0) == "pull_out"


def test_every_tone_the_story_layer_uses_has_camera_moves():
    """The two modules had different tone vocabularies, so a real render's
    'melancholic' and 'joyful' both fell through to a generic move."""
    from agents.story_agent.visual_style import TONE_HINTS
    missing = [t for t in TONE_HINTS if not camera.canonical_tone(t)]
    assert missing == [], f"no camera move for: {missing}"


def test_a_writers_own_words_map_onto_a_tone_with_moves():
    """The model writes what it likes; near-misses should not be generic."""
    assert camera.canonical_tone("wistful") == "melancholic"
    assert camera.canonical_tone("suspenseful") == "tense"
    assert camera.canonical_tone("epic") == "wonder"
    assert camera.canonical_tone("Joyful") == "joyful"      # case folded
    assert camera.canonical_tone("flibbertigibbet") == ""   # honestly unknown


def test_adjacent_shots_do_not_repeat_the_same_move():
    moves = [camera.move_for("detail", "calm", i) for i in range(4)]
    assert moves[0] != moves[1] and moves[1] != moves[2]


def test_an_unknown_tone_still_produces_a_real_move():
    move = camera.move_for("detail", "nonsense-tone", 0)
    assert move in camera.MOVES


# ---- the filter ---------------------------------------------------------------

def test_the_move_is_computed_large_and_scaled_back_down():
    """This is the line that makes pans usable: a whole-pixel error upstream
    becomes a fraction of an output pixel."""
    chain = camera.motion_filter("pan_right", frames=48, width=1280, height=720,
                                 fps=24, factor=3.0)
    assert "scale=w=3840:h=2160" in chain          # 3x the output
    assert "s=3840x2160" in chain                  # zoompan works at that size
    assert chain.rstrip().endswith("scale=1280:720:flags=lanczos")


def test_the_supersample_factor_is_configurable_and_clamped(monkeypatch):
    monkeypatch.setenv("SUPERSAMPLE", "2")
    assert camera.supersample() == 2.0
    monkeypatch.setenv("SUPERSAMPLE", "99")        # 99x would exhaust memory
    assert camera.supersample() == 4.0
    monkeypatch.setenv("SUPERSAMPLE", "nonsense")
    assert camera.supersample() == camera.DEFAULT_SUPERSAMPLE


@pytest.mark.parametrize("move", sorted(camera.MOVES))
def test_every_move_produces_a_filter_ffmpeg_accepts(move, tmp_path):
    """A malformed expression fails at render time, deep inside a job."""
    image = _detailed_image(tmp_path / "src.png", 640, 360)
    out = tmp_path / f"{move}.mp4"
    chain = camera.motion_filter(move, frames=12, width=320, height=180,
                                 fps=12, factor=1.5)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-vf",
         chain + ",format=yuv420p", "-frames:v", "12", "-r", "12",
         "-c:v", "libx264", "-preset", "ultrafast", "-an", str(out)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert out.exists() and out.stat().st_size > 0


def test_a_pan_is_smooth_enough_that_the_eye_cannot_see_the_steps(tmp_path):
    """The regression test for why pans were banned.

    zoompan places the crop window at integer pixels, so a sub-pixel move per
    frame rounds unevenly and the picture shivers. Measured on a 1280x720 pan,
    the old 1.6x path wobbled with std 0.49 px and worst-case 0.63 px; at 3x it
    is 0.31 / 0.40. This asserts the delivered frames stay sub-pixel.
    """
    cv2 = pytest.importorskip("cv2")
    image = _detailed_image(tmp_path / "src.png", 1600, 900)
    out = tmp_path / "pan.mp4"
    frames, w, h, fps = 36, 640, 360, 24
    chain = camera.motion_filter("pan_right", frames, w, h, fps, factor=3.0)
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-vf",
         chain + ",format=yuv420p", "-frames:v", str(frames), "-r", str(fps),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out)],
        check=True, capture_output=True)

    shifts = []
    cap = cv2.VideoCapture(str(out))
    previous = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        grey = np.float32(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        if previous is not None:
            (dx, _dy), _ = cv2.phaseCorrelate(previous, grey)
            shifts.append(dx)
        previous = grey
    cap.release()

    shifts = np.array(shifts)
    assert len(shifts) > 10
    assert abs(shifts.mean()) > 0.3, "the pan did not actually move"
    # Sub-pixel: below half a pixel the step is invisible.
    assert shifts.std() < 0.5, f"pan wobbles: std {shifts.std():.3f} px"


def _detailed_image(path, width, height):
    """A textured image — phase correlation needs something to lock onto."""
    from PIL import Image
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, size=(height // 8, width // 8, 3), dtype=np.uint8)
    img = Image.fromarray(noise).resize((width, height), Image.BICUBIC)
    img.save(path)
    return path


# ---- grading -------------------------------------------------------------------

@pytest.mark.parametrize("style,expected", [
    ("high-contrast film noir, monochrome", "noir"),
    ("cinematic sci-fi, cold blue-teal palette, anamorphic lens", "teal_orange"),
    ("icy clinical winter light", "cold"),
    ("golden hour, warm nostalgic sunlight", "warm"),
    ("gritty desaturated documentary", "bleach"),
    ("", "neutral"),
])
def test_the_grade_follows_the_look_the_story_asked_for(style, expected):
    assert camera.grade_for(style).name == expected


def test_tone_grades_the_shot_when_the_story_said_nothing():
    assert camera.grade_for("", "uneasy").name == "cold"
    assert camera.grade_for("", "fearful").name == "cold"     # via a synonym
    assert camera.grade_for("", "hopeful").name == "warm"
    assert camera.grade_for("", "melancholic").name == "bleach"
    assert camera.grade_for("", "").name == "neutral"


def test_the_post_chain_carries_grain_vignette_and_the_grade():
    chain = camera.post_chain("film noir", "", add_grain=True, add_vignette=True)
    assert "vignette" in chain
    assert "noise=alls=2:allf=u" in chain       # spatial only; temporal shimmers
    assert camera.GRADES["noir"].filters in chain
    assert chain.endswith("format=yuv420p")

    bare = camera.post_chain("", "", add_grain=False, add_vignette=False)
    assert "vignette" not in bare and "noise=" not in bare


def test_letterbox_bars_are_even_numbers_of_pixels():
    """An odd height makes libx264 refuse the frame."""
    chain = camera.post_chain("", "", letterbox=True, width=1280, height=721)
    bars = [part for part in chain.split(",") if part.startswith("drawbox")]
    assert len(bars) == 2
    for bar in bars:
        height = int(bar.split("h=")[1].split(":")[0])
        assert height % 2 == 0 and height > 0


# ---- compatibility ---------------------------------------------------------------

def test_states_written_before_this_milestone_still_render():
    """Old projects have motion names like `ken_burns_diag` recorded in them."""
    for legacy, modern in animator.LEGACY_MOTIONS.items():
        assert modern in camera.MOVES, legacy
    assert animator.LEGACY_MOTIONS["ken_burns_diag"] == "drift"
    assert animator.LEGACY_MOTIONS["slow_zoom_in"] == "push_in"


# ---- provider pressure ---------------------------------------------------------

def test_a_slow_provider_is_never_hit_harder_than_it_allows(tmp_path, monkeypatch):
    """The worker pool is sized from the preferred provider's concurrency. When
    that one starts failing and the chain falls through to an endpoint that
    takes one request at a time, every worker used to arrive at once — and a
    run came back full of placeholder images."""
    import threading

    from mcp.tools.vision_tools import image_gen_tool
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool
    from shared import providers
    from shared.utils.parallel import run_jobs

    config = tmp_path / "providers.yaml"
    config.write_text(
        "version: 1\nroles:\n  image:\n"
        "    - provider: pollinations\n      concurrency: 1\n      retries: 1\n",
        encoding="utf-8")
    monkeypatch.setenv("PROVIDERS_FILE", str(config))
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    providers.load(force=True)

    live = 0
    peak = 0
    guard = threading.Lock()

    def slow_provider(self, spec, prompt, negative, out, width, height, seed):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        try:
            import time
            time.sleep(0.05)
            Path(out).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
            return {}
        finally:
            with guard:
                live -= 1

    monkeypatch.setattr(ImageGenTool, "_provider_pollinations", slow_provider)
    image_gen_tool._PROVIDER_LIMITS.clear()

    tool = ImageGenTool()
    # Eight workers, as if a faster provider had sized the pool.
    run_jobs([(tool.run, (f"prompt {i}", str(tmp_path / f"{i}.png"))) for i in range(8)],
             workers=8, label="images")

    providers.load(force=True)
    assert peak == 1, f"{peak} calls hit a concurrency-1 provider at once"


def test_supersampling_is_capped_so_a_bigger_project_cannot_exhaust_memory():
    """3x of 720p is 8.3 MP a frame, which is fine; 3x of 1080p is 18.7 MP,
    and several of those in flight is how ffmpeg gets killed mid-render."""
    assert camera.supersample_for(1280, 720, 3.0) == 3.0
    for w, h in ((1920, 1080), (2560, 1440), (3840, 2160)):
        factor = camera.supersample_for(w, h, 3.0)
        assert w * factor * h * factor <= camera.MAX_SUPERSAMPLED_PIXELS * 1.01
        assert factor >= 1.0            # never below the output size


def test_supersampled_dimensions_are_even():
    """libx264 refuses an odd dimension."""
    chain = camera.motion_filter("pan_right", 24, 1920, 1080, 24)
    scaled = chain.split("scale=w=")[1].split(",")[0]
    w, h = (int(v) for v in scaled.replace("h=", "").split(":")[:2])
    assert w % 2 == 0 and h % 2 == 0


def test_a_shot_that_cannot_be_supersampled_is_rendered_anyway(tmp_path, monkeypatch):
    """Losing the whole film because one shot ran out of memory is the wrong
    trade; a steppier shot is the right one."""
    import subprocess as sp

    from agents.video_agent import animator

    image = _detailed_image(tmp_path / "src.png", 640, 360)
    calls = []
    real_run = sp.run

    def flaky(cmd, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg":
            calls.append(list(cmd))   # the retry edits it in place
            if len(calls) == 1:          # the supersampled attempt "runs out"
                return sp.CompletedProcess(cmd, 1, b"", b"Conversion failed!")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(animator.subprocess, "run", flaky)
    shot = animator.Shot(image_path=str(image), duration_ms=500, motion="pan_right")
    out = animator.render_shot(shot, tmp_path / "shot.mp4", 320, 180, 12)

    assert out.exists() and out.stat().st_size > 0
    assert len(calls) == 2, "it should have retried"
    # The retry computes the move at output size.
    assert "scale=w=960:h=540" in " ".join(calls[0])
    assert "scale=w=320:h=180" in " ".join(calls[1])


def test_a_total_image_outage_still_leaves_a_renderable_file(tmp_path, monkeypatch):
    """Every free image endpoint can run out of quota on the same day. The
    agent used to record the path it meant to write, and the missing file
    surfaced much later as ffmpeg failing to open its input."""
    from mcp.base_tool import ToolResult
    from agents.video_agent.agent import _ensure_image

    out = tmp_path / "scene_1_wide.png"
    failed = ToolResult(success=False, error="every provider refused")
    path = _ensure_image(failed, out, "a flooded street", 320, 180)

    assert Path(path) == out
    assert out.exists() and out.stat().st_size > 0
    # And a successful result is passed straight through.
    good = ToolResult(success=True, data=str(out))
    assert _ensure_image(good, out, "x", 320, 180) == str(out)


def test_kokoro_explains_a_missing_package_instead_of_an_import_error(tmp_path,
                                                                      monkeypatch):
    """CI found this: on a machine without requirements-voices.txt installed,
    choosing Kokoro raised `ModuleNotFoundError: No module named 'soundfile'`.
    The laptop it was written on has the packages, so it could not show up
    locally."""
    import builtins

    from mcp.tools.audio_tools.tts_tool import TtsTool

    model, voices = tmp_path / "k.onnx", tmp_path / "v.bin"
    model.write_bytes(b"x")
    voices.write_bytes(b"x")
    monkeypatch.setenv("KOKORO_MODEL", str(model))
    monkeypatch.setenv("KOKORO_VOICES", str(voices))

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name in ("soundfile", "kokoro_onnx"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="requirements-voices.txt"):
        TtsTool()._kokoro("hello", tmp_path / "out.wav")
