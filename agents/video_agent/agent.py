"""Phase 3 agent: ScriptOutput + AudioOutput -> final MP4 (cinematic, in sync).

Two stages:

1. **Assets** (`run`): one portrait per character and a 3-image shot bank per
   scene (wide / detail / alt), plus an optional real-motion clip for the
   establishing shot when a text/image-to-video provider is configured.

2. **Composition** (`compose`, also used by edits): every scene is cut to the
   audio timeline from Phase 2 (shared/timeline.py):
       pre-roll  -> establishing shot (or the real-motion clip)
       each line -> narrator: rotate through the shot bank
                    character: their portrait (B-roll cutaway on long lines),
                    or a real lip-sync clip when a provider is configured
   Shot boundaries are the timeline's boundaries rounded to frames, and every
   clip is rendered with extra frames to cover its crossfade, so the final
   video is exactly as long as the master audio and every cut lands on its
   line. Scenes whose shot plan is unchanged are reused, not re-rendered.
   Then scenes are crossfaded, the master audio is muxed, an optional speed
   change is applied, and subtitle tracks are embedded.
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.story_agent.appearance import build_appearance_lock, character_seed
from mcp.tool_executor import ToolExecutor
from shared import providers
from shared.constants import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH, PHASE_VIDEO
from shared.languages import canonical
from shared.schemas.audio import AudioSegment, SceneTiming
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import Character, Scene
from shared.schemas.storyboard import Storyboard, StoryboardFrame, StoryboardLine
from shared.schemas.video import CharacterPortrait, SceneFrame, Shot, VideoOutput
from shared.timeline import (
    SCENE_XFADE_MS, SHOT_XFADE_MS, build_timeline, span_frames, split_span,
    xfade_frames,
)
from shared.utils.files import project_dir, write_json
from shared.utils.parallel import run_jobs
from shared.utils.logging import get_logger

from .animator import (
    Shot as RenderShot, assemble_scene, pick_motion_for_index, render_shot,
)

log = get_logger("video_agent")

MAX_SHOT_MS = 4500   # longest a single still stays on screen before a cut
MIN_SHOT_MS = 1500   # shortest cut we'll create when splitting a long span

ANIME_STYLE = ("anime, studio ghibli style, cel-shaded, vibrant saturated colors, "
               "clean detailed line art, soft cinematic lighting")
SCENE_STYLE = ANIME_STYLE + ", painterly background, masterpiece, high quality, ultra detailed"
PORTRAIT_STYLE = ANIME_STYLE + ", masterpiece, high quality"
SCENE_NEGATIVE = ("blurry, low quality, jpeg artifacts, photograph, photorealistic, "
                  "3d render, text, watermark, signature, deformed")
PORTRAIT_NEGATIVE = ("blurry, low quality, jpeg artifacts, deformed, extra limbs, mutated, "
                     "ugly, photograph, photorealistic, 3d render, text, watermark, signature")

# Three deliberately different framings of the same scene so cuts feel meaningful.
BANK_FRAMINGS = [
    ("wide",   "wide establishing shot, full landscape, expansive composition"),
    ("detail", "extreme close-up detail, macro, intricate texture, shallow depth of field"),
    ("alt",    "alternate angle, low-angle hero shot, dramatic perspective, dynamic framing"),
]


@dataclass
class PlannedShot:
    shot_id: str
    kind: str                     # establishing | character | lip_sync
    source: str                   # image path, or video path when is_video
    start_ms: int
    end_ms: int
    nominal_frames: int
    render_frames: int
    motion: str
    character_id: Optional[str] = None
    audio_path: Optional[str] = None
    is_video: bool = False


class VideoAgent:
    def __init__(self):
        self.tools = ToolExecutor()

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, with_subtitles: bool = True,
            subtitle_language: str = "English",
            width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
            fps: int = DEFAULT_FPS,
            use_text_to_video: Optional[bool] = None,
            use_lip_sync: Optional[bool] = None,
            cinematic_post: bool = True) -> VideoOutput:
        if not state.script:
            raise ValueError("phase 3 requires state.script (run phase 1 first)")
        log.info("phase 3 start (project=%s, %dx%d@%d, subs=%s, lang=%s)",
                 state.project_id, width, height, fps, with_subtitles, subtitle_language)
        state.phase3.status = "running"
        state.phase3.started_at = datetime.utcnow().isoformat()

        # Auto-detect upgrades from env if caller didn't specify.
        if use_text_to_video is None:
            use_text_to_video = bool(
                os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or
                os.getenv("REPLICATE_API_TOKEN") or os.getenv("HF_TOKEN")
            )
        if use_lip_sync is None:
            use_lip_sync = bool(
                os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or
                os.getenv("REPLICATE_API_TOKEN")
            )

        try:
            # Portraits and every scene's shot bank are independent: generate them
            # as concurrently as the active image provider allows.
            portraits = run_jobs(
                [(self.generate_portrait, (state.project_id, c, width, height))
                 for c in state.script.characters.characters],
                workers=providers.concurrency("image"), label="portraits",
            )
            frames = run_jobs(
                [(self._generate_scene_frame,
                  (state.project_id, scene, width, height, fps, use_text_to_video))
                 for scene in state.script.scenes],
                workers=providers.concurrency("image"), label="scene images",
            )

            lang = canonical(subtitle_language)
            if subtitle_language and not lang:
                log.warning("subtitle language %r is not supported — using English",
                            subtitle_language)
            state.video = VideoOutput(
                project_id=state.project_id,
                frames=frames,
                final_video_path="",
                width=width, height=height, fps=fps,
                has_subtitles=with_subtitles,
                portraits=portraits,
                used_text_to_video=use_text_to_video,
                used_lip_sync=use_lip_sync,
                cinematic_post=cinematic_post,
                subtitle_language=lang or "English",
            )
            final_video = self.compose(state)

            state.phase3.status = "complete"
            state.phase3.finished_at = datetime.utcnow().isoformat()
            state.phase3.artifact_paths = self.serialize(state)
            log.info("phase 3 complete (%d scenes, %d shots total, video=%s)",
                     len(frames), sum(len(f.shots) for f in state.video.frames), final_video)
            return state.video
        except Exception as e:  # noqa: BLE001
            state.phase3.status = "failed"
            state.phase3.error = f"{type(e).__name__}: {e}"
            log.exception("phase 3 failed")
            raise

    def compose(self, state: PipelineState, force: bool = False) -> str:
        """Cut every scene to the current timeline and build the final film.

        Reuses a scene's existing clip when its shot plan and source images are
        unchanged (unless `force`). Returns the final video path.
        """
        video = state.video
        timings = self._scene_timings(state)
        scene_by_id = {s.scene_id: s for s in state.script.scenes}
        segs_by_scene: Dict[str, List[AudioSegment]] = {}
        if state.audio:
            for seg in state.audio.manifest.segments:
                if seg.kind == "dialogue":
                    segs_by_scene.setdefault(seg.scene_id, []).append(seg)

        rendered = reused = 0
        last_index = len(video.frames) - 1
        for i, frame in enumerate(video.frames):
            timing = timings[frame.scene_id]
            planned = self._plan_scene(state, scene_by_id[frame.scene_id], frame, timing,
                                       segs_by_scene.get(frame.scene_id, []),
                                       is_last_scene=(i == last_index))
            signature = self._signature(planned, video)
            clip_ok = frame.clip_path and Path(frame.clip_path).exists()
            if not force and clip_ok and frame.plan_signature == signature:
                reused += 1
            else:
                frame.clip_path = str(self._render_scene(state, frame.scene_id, planned))
                frame.plan_signature = signature
                rendered += 1
            frame.shots = [self._to_shot(frame.scene_id, p) for p in planned]
            frame.start_ms = timing.start_ms
            frame.duration_ms = timing.duration_ms
        log.info("composition: %d scene(s) rendered, %d reused", rendered, reused)

        final = self._compose_final(state)
        video.final_video_path = str(final)
        video.duration_ms = int(round(sum(t.duration_ms for t in timings.values())
                                      / video.speed_factor))
        return str(final)

    def serialize(self, state: PipelineState) -> List[str]:
        """Write video_summary.json; returns every Phase 3 artifact path."""
        output = state.video
        proj = project_dir(state.project_id)
        summary = proj / "video_summary.json"
        write_json(summary, {
            "project_id": state.project_id,
            "phase": PHASE_VIDEO,
            "status": "complete",
            "final_video": output.final_video_path,
            "frame_count": len(output.frames),
            "shot_count": sum(len(f.shots) for f in output.frames),
            "has_subtitles": output.has_subtitles,
            "subtitle_languages": output.subtitle_languages,
            "used_text_to_video": output.used_text_to_video,
            "used_lip_sync": output.used_lip_sync,
            "speed_factor": output.speed_factor,
            "width": output.width, "height": output.height, "fps": output.fps,
            "duration_ms": output.duration_ms,
            "frames": [f.model_dump(mode="json") for f in output.frames],
            "portraits": [p.model_dump(mode="json") for p in output.portraits],
        })
        artifacts: List[str] = [str(summary), output.final_video_path]
        for f in output.frames:
            artifacts.append(f.image_path)
            artifacts.extend(f.shot_bank)
            artifacts.extend(f.portrait_overrides.values())
            for extra in (f.clip_path, f.t2v_clip):
                if extra:
                    artifacts.append(extra)
            artifacts.extend(s.clip_path for s in f.shots if s.clip_path)
        for p in output.portraits:
            artifacts.append(p.image_path)
            if p.talking_head_clip:
                artifacts.append(p.talking_head_clip)
        seen = set()
        state.phase3.artifact_paths = [a for a in artifacts
                                       if a and not (a in seen or seen.add(a))]
        return state.phase3.artifact_paths

    # ---- assets ----------------------------------------------------------

    def generate_portrait(self, project_id: str, c: Character, width: int, height: int,
                          seed_salt: str = "") -> CharacterPortrait:
        """(Re)generate one character's close-up portrait."""
        out = project_dir(project_id) / "video" / "portraits" / f"{c.id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        # The appearance lock (not the raw description) keeps the face stable.
        look = c.appearance_lock or build_appearance_lock(c)
        prompt = (
            f"anime style close-up portrait of {look}, "
            f"{c.role}, expressive face, large detailed eyes, looking at camera, "
            f"vibrant colors, cel-shaded, clean line art, soft anime lighting"
        )
        seed = character_seed(c, seed_salt) if seed_salt else (
            c.image_seed if c.image_seed is not None else character_seed(c))
        res = self.tools.execute(
            "vision.generate_image", prompt=prompt, out_path=str(out),
            width=width, height=height, style=PORTRAIT_STYLE,
            negative_prompt=PORTRAIT_NEGATIVE, seed=seed,
        )
        log.info("  portrait: %s -> %s (%s)", c.name, out.name,
                 res.metadata.get("provider") if res.success else res.error)
        return CharacterPortrait(character_id=c.id, image_path=res.data if res.success else str(out))

    def generate_storyboard(self, state: PipelineState, width: int = 512, height: int = 288,
                            scene_ids: Optional[List[str]] = None) -> Storyboard:
        """One small preview image per scene, for approval before the real render.

        A preview costs a single image; a full render costs three per scene plus
        portraits, every voice line and all the video work.
        """
        script = state.script
        board = state.storyboard or Storyboard(
            project_id=state.project_id, prompt=state.user_prompt,
            title=script.story.title, logline=script.story.logline,
            preview_width=width, preview_height=height,
        )
        names = {c.id: c.name for c in script.characters.characters}
        targets = [s for s in script.scenes if not scene_ids or s.scene_id in scene_ids]
        previews = run_jobs(
            [(self._generate_preview, (state.project_id, scene, width, height))
             for scene in targets],
            workers=providers.concurrency("image"), label="storyboard previews",
        )
        preview_by_scene = dict(zip([s.scene_id for s in targets], previews))

        frames = []
        for scene in script.scenes:
            existing = board.frame(scene.scene_id)
            frames.append(StoryboardFrame(
                scene_id=scene.scene_id, index=scene.index, title=scene.title,
                setting=scene.setting, visual_prompt=scene.visual_prompt,
                preview_path=preview_by_scene.get(
                    scene.scene_id, existing.preview_path if existing else None),
                dialogue=[StoryboardLine(line_id=ln.line_id, character_id=ln.character_id,
                                         character_name=names.get(ln.character_id, ""),
                                         text=ln.text)
                          for ln in scene.dialogue],
                estimated_ms=scene.duration_ms,
            ))
        board.frames = frames
        board.title, board.logline = script.story.title, script.story.logline
        board.touch()
        state.storyboard = board
        state.stage = "storyboard" if state.stage == "draft" else state.stage
        return board

    def _generate_preview(self, project_id: str, scene: Scene,
                          width: int, height: int) -> str:
        out = project_dir(project_id) / "video" / "storyboard" / f"{scene.scene_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        prompt = f"anime scene of {scene.visual_prompt}, {BANK_FRAMINGS[0][1]}"
        res = self.tools.execute(
            "vision.generate_image", prompt=prompt, out_path=str(out),
            width=width, height=height, style=SCENE_STYLE,
            negative_prompt=SCENE_NEGATIVE,
            # A new prompt gives a new preview; the same prompt reuses the look.
            seed=_seed(prompt, ""),
        )
        log.info("  storyboard %s -> %s (%s)", scene.scene_id, out.name,
                 res.metadata.get("provider") if res.success else res.error)
        return res.data if res.success else str(out)

    def generate_shot_bank(self, project_id: str, scene: Scene, width: int, height: int,
                           seed_salt: str = "") -> List[str]:
        """(Re)generate a scene's wide / detail / alt images. Returns their paths."""
        img_dir = project_dir(project_id) / "video" / "frames"
        img_dir.mkdir(parents=True, exist_ok=True)
        bank: List[str] = []
        for tag, suffix in BANK_FRAMINGS:
            out = img_dir / f"{scene.scene_id}_{tag}.png"
            prompt = f"anime scene of {scene.visual_prompt}, {suffix}"
            res = self.tools.execute(
                "vision.generate_image", prompt=prompt, out_path=str(out),
                width=width, height=height, style=SCENE_STYLE,
                negative_prompt=SCENE_NEGATIVE, seed=_seed(prompt, seed_salt),
            )
            bank.append(res.data if res.success else str(out))
            log.info("  scene %s %s shot -> %s (%s)", scene.scene_id, tag, out.name,
                     res.metadata.get("provider") if res.success else res.error)
        return bank

    def _generate_scene_frame(self, project_id: str, scene: Scene, width: int, height: int,
                              fps: int, use_text_to_video: bool) -> SceneFrame:
        bank = self.generate_shot_bank(project_id, scene, width, height)
        t2v_clip = None
        if use_text_to_video:
            t2v_out = project_dir(project_id) / "video" / "t2v" / f"{scene.scene_id}.mp4"
            t2v_out.parent.mkdir(parents=True, exist_ok=True)
            # Image-to-video — the wide still gives the model a strong visual anchor.
            res = self.tools.execute(
                "vision.text_to_video", prompt=scene.visual_prompt, image_path=bank[0],
                out_path=str(t2v_out), duration_s=4.0, width=width, height=height, fps=fps,
            )
            if res.success:
                t2v_clip = res.data
                log.info("  scene %s: t2v clip generated (%s)", scene.scene_id,
                         res.metadata.get("provider"))
            else:
                log.info("  scene %s: t2v unavailable (%s) — using still",
                         scene.scene_id, res.error)
        return SceneFrame(scene_id=scene.scene_id, image_path=bank[0], shot_bank=bank,
                          width=width, height=height, duration_ms=scene.duration_ms,
                          transition_in=scene.transition_in, t2v_clip=t2v_clip)

    # ---- shot planning ---------------------------------------------------

    def _scene_timings(self, state: PipelineState) -> Dict[str, SceneTiming]:
        if state.audio and state.audio.manifest.scenes:
            return {t.scene_id: t for t in state.audio.manifest.scenes}
        # No audio timeline (e.g. video-only runs): scenes play their script length.
        slots, _, _ = build_timeline([(s.scene_id, [], s.duration_ms)
                                      for s in state.script.scenes])
        return {s.scene_id: SceneTiming(scene_id=s.scene_id, start_ms=s.start_ms,
                                        end_ms=s.end_ms) for s in slots}

    def _plan_scene(self, state: PipelineState, scene: Scene, frame: SceneFrame,
                    timing: SceneTiming, segments: List[AudioSegment],
                    is_last_scene: bool) -> List[PlannedShot]:
        """Decide every cut in a scene. Pure planning — nothing is rendered."""
        video = state.video
        fps = video.fps
        bank = frame.shot_bank or [frame.image_path]
        characters = {c.id: c for c in state.script.characters.characters}
        spans: List[Dict[str, Any]] = []   # {start, end, sources, kind, char, audio, video}

        first_line_start = segments[0].start_ms if segments else timing.end_ms
        if first_line_start > timing.start_ms:
            if frame.t2v_clip and Path(frame.t2v_clip).exists():
                spans.append({"start": timing.start_ms, "end": first_line_start,
                              "sources": [frame.t2v_clip], "kind": "establishing",
                              "video": True})
            else:
                spans.append({"start": timing.start_ms, "end": first_line_start,
                              "sources": bank if not segments else bank[:1],
                              "kind": "establishing"})

        for i, seg in enumerate(segments):
            end = segments[i + 1].start_ms if i + 1 < len(segments) else timing.end_ms
            char = characters.get(seg.character_id)
            if char is None or char.role == "narrator":
                # Narrator: show the world, rotating through the shot bank.
                spans.append({"start": seg.start_ms, "end": end, "sources": bank,
                              "kind": "establishing"})
                continue
            portrait = frame.portrait_overrides.get(seg.character_id) or \
                (video.portrait_for(seg.character_id).image_path
                 if video.portrait_for(seg.character_id) else bank[0])
            lip_clip = self._lip_sync_clip(state, frame.scene_id, i, portrait, seg) \
                if video.used_lip_sync else None
            if lip_clip:
                spans.append({"start": seg.start_ms, "end": end, "sources": [lip_clip],
                              "kind": "lip_sync", "char": seg.character_id,
                              "audio": seg.file_path, "video": True})
            else:
                # Portrait, with a B-roll cutaway in the middle of long lines.
                sources = [portrait, bank[1 % len(bank)], portrait] \
                    if end - seg.start_ms > MAX_SHOT_MS else [portrait]
                spans.append({"start": seg.start_ms, "end": end, "sources": sources,
                              "kind": "character", "char": seg.character_id,
                              "audio": seg.file_path})

        planned: List[PlannedShot] = []
        for span_idx, span in enumerate(spans):
            pieces = [(span["start"], span["end"])] if span.get("video") \
                else split_span(span["start"], span["end"], MAX_SHOT_MS, MIN_SHOT_MS)
            for j, (a, b) in enumerate(pieces):
                suffix = "est" if span["kind"] == "establishing" and span_idx == 0 \
                    and first_line_start > timing.start_ms else f"s{span_idx + 1}"
                shot_id = f"{frame.scene_id}_{suffix}" + (f"_p{j + 1}" if len(pieces) > 1 else "")
                nominal = span_frames(a, b, fps)
                planned.append(PlannedShot(
                    shot_id=shot_id, kind=span["kind"],
                    source=span["sources"][j % len(span["sources"])],
                    start_ms=a, end_ms=b, nominal_frames=nominal, render_frames=nominal,
                    motion="lip_sync" if span["kind"] == "lip_sync"
                    else pick_motion_for_index(scene.index, len(planned)),
                    character_id=span.get("char"),
                    audio_path=span.get("audio") if j == 0 else None,
                    is_video=bool(span.get("video")),
                ))

        # Extra frames to cover crossfade overlaps: every shot but the last
        # overlaps the next shot; the scene's last shot overlaps the next scene.
        shot_x = xfade_frames(SHOT_XFADE_MS, fps)
        scene_x = xfade_frames(SCENE_XFADE_MS, fps)
        for k, p in enumerate(planned):
            if k < len(planned) - 1:
                p.render_frames = p.nominal_frames + shot_x
            elif not is_last_scene:
                p.render_frames = p.nominal_frames + scene_x
        return planned

    def _lip_sync_clip(self, state: PipelineState, scene_id: str, line_idx: int,
                       portrait: str, seg: AudioSegment) -> Optional[str]:
        """Real lip-sync clip from a provider, or None to fall back to stills."""
        if not seg.file_path or not Path(seg.file_path).exists():
            return None
        out = project_dir(state.project_id) / "video" / "lipsync" / f"{scene_id}_l{line_idx + 1}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        res = self.tools.execute(
            "vision.lip_sync", image_path=portrait, audio_path=seg.file_path,
            out_path=str(out), duration_s=seg.duration_ms / 1000.0,
            width=state.video.width, height=state.video.height, fps=state.video.fps,
        )
        if res.success and str(res.metadata.get("provider", "")).startswith(("fal", "replicate")):
            return res.data
        return None

    def _signature(self, planned: List[PlannedShot], video: VideoOutput) -> str:
        """Fingerprint of a scene's plan + source files; changes when a re-render is needed."""
        def stamp(path: Optional[str]) -> Any:
            if not path:
                return None
            try:
                st = Path(path).stat()
                return [path, st.st_size, st.st_mtime_ns]
            except OSError:
                return [path, None]
        payload = {
            "fmt": [video.width, video.height, video.fps, video.cinematic_post,
                    SHOT_XFADE_MS, SCENE_XFADE_MS],
            "shots": [[p.kind, stamp(p.source), p.render_frames, p.motion, p.is_video]
                      for p in planned],
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _to_shot(scene_id: str, p: PlannedShot) -> Shot:
        return Shot(
            shot_id=p.shot_id, scene_id=scene_id,
            kind=p.kind if p.kind in ("establishing", "character", "lip_sync") else "broll",
            character_id=p.character_id, image_path=p.source, clip_path=None,
            duration_ms=p.end_ms - p.start_ms, motion=p.motion, audio_path=p.audio_path,
            start_ms=p.start_ms, render_frames=p.render_frames,
        )

    # ---- rendering -------------------------------------------------------

    def _render_scene(self, state: PipelineState, scene_id: str,
                      planned: List[PlannedShot]) -> Path:
        video = state.video
        proj = project_dir(state.project_id)
        shot_dir = proj / "video" / "shots"
        scene_dir = proj / "video" / "scenes"
        shot_dir.mkdir(parents=True, exist_ok=True)
        scene_dir.mkdir(parents=True, exist_ok=True)

        paths: List[Path] = []
        for p in planned:
            rs = RenderShot(image_path=p.source, duration_ms=p.end_ms - p.start_ms,
                            motion=p.motion, frames=p.render_frames, is_lip_sync=p.is_video)
            paths.append(render_shot(rs, shot_dir / f"{p.shot_id}.mp4",
                                     video.width, video.height, video.fps,
                                     add_grain=video.cinematic_post,
                                     add_vignette=video.cinematic_post))
        scene_clip = scene_dir / f"{scene_id}.mp4"
        assemble_scene(
            paths, scene_clip,
            crossfade_ms=xfade_frames(SHOT_XFADE_MS, video.fps) * 1000.0 / video.fps,
            durations_s=[p.render_frames / video.fps for p in planned],
        )
        log.info("  scene %s composed (%d shots)", scene_id, len(planned))
        return scene_clip

    def _compose_final(self, state: PipelineState) -> Path:
        video = state.video
        proj = project_dir(state.project_id)
        fps = video.fps
        shot_x = xfade_frames(SHOT_XFADE_MS, fps)
        scene_x = xfade_frames(SCENE_XFADE_MS, fps)

        clips, durations = [], []
        for f in video.frames:
            frames = sum(s.render_frames for s in f.shots) - shot_x * (len(f.shots) - 1)
            clips.append(f.clip_path)
            durations.append(frames / fps)

        out = proj / "final_output.mp4"
        master = state.audio.master_track if state.audio else None
        compose = self.tools.execute(
            "video.compose", clips=clips, out_path=str(out), audio_path=master,
            transition="fade", transition_ms=scene_x * 1000.0 / fps, durations_s=durations,
        )
        if not compose.success:
            raise RuntimeError(f"final compose failed: {compose.error}")
        if compose.metadata.get("fallback"):
            log.warning("crossfade compose failed; fell back to hard cuts — "
                        "audio may drift by up to %.1fs",
                        (len(clips) - 1) * scene_x / fps)

        final = out
        if abs(video.speed_factor - 1.0) > 1e-3:
            final = self._apply_speed(out, proj / "final_output_speed.mp4",
                                      video.speed_factor, has_audio=bool(master))

        video.subtitle_languages = []
        if video.has_subtitles and state.audio:
            subbed = self._embed_subtitles(state, final)
            if subbed:
                final = subbed
        return final

    @staticmethod
    def _apply_speed(src: Path, dst: Path, factor: float, has_audio: bool) -> Path:
        """ffmpeg setpts (video) + atempo chain (audio, bounded to [0.5, 2] per stage)."""
        af_chain = []
        atempo = factor
        while atempo > 2.0:
            af_chain.append("atempo=2.0")
            atempo /= 2.0
        while atempo < 0.5:
            af_chain.append("atempo=0.5")
            atempo *= 2.0
        af_chain.append(f"atempo={atempo:.4f}")
        fc = f"[0:v]setpts={1 / factor:.6f}*PTS[v]"
        cmd = ["ffmpeg", "-y", "-i", str(src)]
        if has_audio:
            fc += f";[0:a]{','.join(af_chain)}[a]"
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-filter_complex", fc, "-map", "[v]", "-an"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(dst)]
        subprocess.run(cmd, check=True, capture_output=True)
        return dst

    # ---- subtitles -------------------------------------------------------

    def _embed_subtitles(self, state: PipelineState, video_path: Path) -> Optional[Path]:
        """Embed English + requested languages as switchable soft-sub tracks.

        A language whose translation fails is skipped (never shipped as English
        under a foreign label). Translations are cached on the VideoOutput and
        reused until the English lines change.
        """
        video = state.video
        segs = [s for s in state.audio.manifest.segments if s.kind == "dialogue" and s.text]
        if not segs:
            return None
        english = [s.text for s in segs]
        if video.subtitle_source != english:
            video.subtitle_source = english
            video.subtitle_cache = {}

        tracks: Dict[str, List[Dict[str, Any]]] = {}
        speed = video.speed_factor
        for lang in self._subtitle_languages(video):
            texts = english if lang == "English" else video.subtitle_cache.get(lang)
            if texts is None or len(texts) != len(english):
                res = self.tools.execute("text.translate", lines=english, target_language=lang)
                if not res.success:
                    log.warning("subtitle translation to %s failed (%s) — track skipped",
                                lang, res.error)
                    continue
                texts = res.data
                video.subtitle_cache[lang] = texts
                log.info("translated %d subtitle lines to %s via %s",
                         len(texts), lang, res.metadata.get("provider"))
            tracks[lang] = [{"start_ms": int(s.start_ms / speed), "end_ms": int(s.end_ms / speed),
                             "text": t} for s, t in zip(segs, texts)]

        out = project_dir(state.project_id) / "final_output_multilang.mp4"
        res = self.tools.execute("video.multi_subtitle", in_path=str(video_path),
                                 out_path=str(out), tracks=tracks,
                                 default_language=video.subtitle_language
                                 if video.subtitle_language in tracks else "English")
        if not res.success:
            log.warning("subtitle embed failed: %s — returning video without subtitles",
                        res.error)
            return None
        video.subtitle_languages = list(res.metadata.get("languages", []))
        log.info("embedded %d soft-sub tracks (%s); default=%s", len(video.subtitle_languages),
                 ", ".join(video.subtitle_languages), video.subtitle_language)
        return out

    @staticmethod
    def _subtitle_languages(video: VideoOutput) -> List[str]:
        """English, the chosen language, then any SUBTITLE_EXTRA_LANGUAGES from .env."""
        wanted = ["English", video.subtitle_language]
        wanted += [s for s in os.getenv("SUBTITLE_EXTRA_LANGUAGES", "").split(",") if s.strip()]
        out: List[str] = []
        for name in wanted:
            lang = canonical(name)
            if not lang:
                log.warning("ignoring unsupported subtitle language %r", name)
            elif lang not in out:
                out.append(lang)
        return out


def _seed(prompt: str, salt: str = "") -> int:
    """Stable per-prompt seed; a salt (e.g. the version) gives a fresh variation."""
    return int(hashlib.md5(f"{prompt}|{salt}".encode()).hexdigest()[:8], 16) % 2**31
