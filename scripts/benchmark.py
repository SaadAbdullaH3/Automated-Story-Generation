"""Run a fixed set of prompts and measure the result.

Gives comparable numbers across changes and providers: how long each phase
took, whether the film hit its target length, whether audio and video stayed
in sync, which provider actually served each image, and what fell back.

    python scripts/benchmark.py --offline            # fast, no network
    python scripts/benchmark.py --duration 30        # with the real providers
    python scripts/benchmark.py --out docs/BENCHMARK.md

Results are also written to data/benchmarks/<timestamp>.json.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMPTS = [
    "A young astronaut discovers a hidden ocean on Mars",
    "A lighthouse keeper befriends a stranded whale",
    "A night librarian finds the books rearranging into a map",
]


def _offline() -> None:
    """Force the offline providers so a benchmark can run anywhere."""
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["PROVIDER_IMAGE"] = "placeholder"
    os.environ["PROVIDER_TTS"] = "silent"


def _video_frames(path: str) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])


def _phase_seconds(state, phase: str) -> float:
    block = getattr(state, phase)
    if not block.started_at or not block.finished_at:
        return 0.0
    started = datetime.fromisoformat(block.started_at)
    finished = datetime.fromisoformat(block.finished_at)
    return round((finished - started).total_seconds(), 1)


def measure(state, target_s: int, elapsed: float, providers_used: Counter,
            preferred_image: str = "") -> Dict[str, Any]:
    """Everything worth comparing between runs, from one finished project."""
    from shared.timeline import ms_to_frame

    manifest = state.audio.manifest
    video = state.video
    shots = [s for f in video.frames for s in f.shots]
    cut_starts = {s.start_ms for s in shots}
    expected_frames = ms_to_frame(manifest.total_duration_ms / video.speed_factor, video.fps)
    actual_frames = _video_frames(video.final_video_path)
    film_s = manifest.total_duration_ms / 1000

    return {
        "project_id": state.project_id,
        "scenes": len(state.script.scenes),
        "lines": len(manifest.segments),
        "shots": len(shots),
        "target_s": target_s,
        "film_s": round(film_s, 1),
        "duration_error_pct": round(abs(film_s - target_s) / target_s * 100, 1),
        "frames_expected": expected_frames,
        "frames_actual": actual_frames,
        "in_sync": actual_frames == expected_frames,
        "lines_on_a_cut": sum(seg.start_ms in cut_starts for seg in manifest.segments),
        "subtitle_languages": video.subtitle_languages,
        "image_providers": dict(providers_used),
        # Images the preferred provider didn't serve (it failed and the chain moved on).
        "fallback_images": sum(n for provider, n in providers_used.items()
                               if preferred_image and provider != preferred_image),
        "total_s": round(elapsed, 1),
        "story_s": _phase_seconds(state, "phase1"),
        "audio_s": _phase_seconds(state, "phase2"),
        "video_s": _phase_seconds(state, "phase3"),
    }


def run_one(prompt: str, args) -> Dict[str, Any]:
    from agents.orchestrator import PipelineOrchestrator
    from mcp.tool_executor import ToolExecutor

    providers_used: Counter = Counter()
    real = ToolExecutor.execute

    def spy(self, tool, **kwargs):
        result = real(self, tool, **kwargs)
        if tool == "vision.generate_image":
            providers_used[result.metadata.get("provider", "failed")] += 1
        return result

    ToolExecutor.execute = spy
    try:
        started = time.monotonic()
        state = PipelineOrchestrator().run_full(
            prompt=prompt,
            target_duration_s=args.duration,
            scene_count=args.scenes,
            with_bgm=not args.no_bgm,
            with_subtitles=not args.no_subs,
            subtitle_language=args.subtitle_lang,
            width=args.width, height=args.height, fps=args.fps,
        )
        elapsed = time.monotonic() - started
    finally:
        ToolExecutor.execute = real
    from shared import providers
    preferred = providers.active("image")
    row = measure(state, args.duration, elapsed, providers_used,
                  preferred_image=preferred.provider if preferred else "")
    row["prompt"] = prompt
    return row


def to_markdown(report: Dict[str, Any]) -> str:
    rows = report["runs"]
    lines = [
        f"# Benchmark — {report['started_at']}",
        "",
        f"`{report['command']}`",
        "",
        f"- providers: image **{report['image_provider']}**, "
        f"llm **{report['llm_provider']}**, tts **{report['tts_provider']}**",
        f"- {len(rows)} prompt(s), target {report['target_s']}s, {report['scenes']} scenes",
        "",
        "| prompt | total | story | audio | video | film | vs target | in sync | lines on a cut | fallback images |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['prompt'][:38]} | {r['total_s']}s | {r['story_s']}s | {r['audio_s']}s | "
            f"{r['video_s']}s | {r['film_s']}s | {r['duration_error_pct']}% | "
            f"{'yes' if r['in_sync'] else 'NO'} | {r['lines_on_a_cut']}/{r['lines']} | "
            f"{r['fallback_images']} |"
        )
    totals = report["totals"]
    lines += [
        "",
        f"**Totals** — {totals['runs']} runs, {totals['total_s']}s, "
        f"all in sync: {'yes' if totals['all_in_sync'] else 'NO'}, "
        f"every line on a cut: {'yes' if totals['all_lines_on_cuts'] else 'NO'}, "
        f"mean length error {totals['mean_duration_error_pct']}%, "
        f"fallback images {totals['fallback_images']}.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="force mock LLM / placeholder images / silent voices")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--scenes", type=int, default=3)
    parser.add_argument("--prompts", type=int, default=len(PROMPTS))
    parser.add_argument("--subtitle-lang", default="English")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--no-bgm", action="store_true")
    parser.add_argument("--no-subs", action="store_true")
    parser.add_argument("--out", help="also write a markdown report here")
    args = parser.parse_args()

    if args.offline:
        _offline()

    import mcp.tools  # noqa: F401  (registers the tools)
    from shared import providers

    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "command": "python " + " ".join(sys.argv[0:1] + sys.argv[1:]),
        "target_s": args.duration,
        "scenes": args.scenes,
        "image_provider": str(providers.active("image")),
        "llm_provider": str(providers.active("story")),
        "tts_provider": str(providers.active("tts")),
        "runs": [],
    }

    for prompt in PROMPTS[:max(1, args.prompts)]:
        print(f"\n=== {prompt}")
        row = run_one(prompt, args)
        report["runs"].append(row)
        print(f"    {row['total_s']}s total · film {row['film_s']}s "
              f"(target {row['target_s']}s) · in sync: {row['in_sync']} · "
              f"images {row['image_providers']}")

    runs: List[Dict[str, Any]] = report["runs"]
    report["totals"] = {
        "runs": len(runs),
        "total_s": round(sum(r["total_s"] for r in runs), 1),
        "all_in_sync": all(r["in_sync"] for r in runs),
        "all_lines_on_cuts": all(r["lines_on_a_cut"] == r["lines"] for r in runs),
        "mean_duration_error_pct": round(
            sum(r["duration_error_pct"] for r in runs) / len(runs), 1),
        "fallback_images": sum(r["fallback_images"] for r in runs),
    }

    out_dir = ROOT / "data" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = to_markdown(report)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    print()
    print(markdown)
    print(f"saved: data/benchmarks/{stamp}.json" + (f" and {args.out}" if args.out else ""))
    return 0 if report["totals"]["all_in_sync"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
