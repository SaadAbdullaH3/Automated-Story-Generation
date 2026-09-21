"""CLI runner — end-to-end pipeline + edit demo without the web UI.

Usage:
    python main.py "your prompt here"
    python main.py "prompt" --duration 30 --scenes 4 --no-bgm
    python main.py serve            # launches FastAPI on :8000
    python main.py edit <project>   # interactive edit REPL on an existing project
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# Make sure project root is on sys.path when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env if python-dotenv is available.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

# Register all MCP tools.
import mcp.tools  # noqa: F401

from agents.edit_agent import EditAgent
from agents.orchestrator import PipelineOrchestrator, ProgressEvent
from shared.schemas.edit import EditCommand
from state_manager.state_manager import StateManager


def _print_event(ev: ProgressEvent) -> None:
    pct = int((ev.progress or 0) * 100)
    print(f"  [{pct:3d}%] {ev.phase:8s} {ev.status:10s} {ev.message}")


def cmd_run(args: argparse.Namespace) -> int:
    orch = PipelineOrchestrator()
    print(f"\n>>> running pipeline for prompt:\n    {args.prompt}\n")
    # Resolve the video tier flags. None = auto-detect from env keys.
    use_t2v = False if args.no_real_video else None
    use_lip = False if args.no_lipsync else None
    state = orch.run_full(
        prompt=args.prompt,
        target_duration_s=args.duration,
        scene_count=args.scenes,
        with_bgm=not args.no_bgm,
        with_subtitles=not args.no_subs,
        subtitle_language=args.subtitle_lang,
        on_event=_print_event,
        use_text_to_video=use_t2v,
        use_lip_sync=use_lip,
    )
    print()
    print("=" * 70)
    print(f"DONE: project={state.project_id} version={state.version}")
    if state.video:
        print(f"VIDEO: {state.video.final_video_path}")
    return 0


def cmd_providers(_args: argparse.Namespace) -> int:
    """Show which model serves each role, and what the alternatives need."""
    from shared import providers

    cfg = providers.load()
    roles = providers.describe()

    print()
    print(f"Model settings  ({cfg.source})")
    print("=" * 74)
    for role, entries in roles.items():
        active = next((e for e in entries if e["available"]), None)
        label = f"{active['provider']}" + (f"  ({active['model']})" if active and active["model"]
                                           else "") if active else "(nothing available)"
        print()
        print(f"  {role:<12} -> {label}")
        for entry in entries:
            if entry is active:
                mark, note = "  *", "in use"
            elif entry["available"]:
                mark, note = "   ", "ready (fallback)"
            else:
                mark, note = "   ", "needs " + ", ".join(entry["missing"])
            model = f" {entry['model']}" if entry["model"] else ""
            print(f"  {mark} {entry['provider']:<14}{model:<34} {note}")

    # Premium video tiers stay env-driven until M5 moves them into the config.
    fal = bool(os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY"))
    rep = bool(os.getenv("REPLICATE_API_TOKEN"))
    hf = bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY"))
    tier = "PREMIUM (real motion + real lip sync)" if (fal or rep) else "STANDARD (multi-shot ffmpeg)"
    print()
    print(f"  video tier   -> {tier}")
    if not (fal or rep or hf):
        print("     text-to-video / lip sync: none configured (see docs/REAL_VIDEO_SETUP.md)")

    print()
    print("-" * 74)
    print("Edit config/providers.yaml to change the order, models or parallelism.")
    print("Free keys: aistudio.google.com/app/apikey (Gemini), console.groq.com (Groq),")
    print("           dash.cloudflare.com (Workers AI), enter.pollinations.ai (images).")
    print()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    sm = StateManager()
    if not sm.latest(args.project_id):
        print(f"no such project: {args.project_id}")
        return 1
    agent = EditAgent(sm)
    print(f"\nEdit REPL for {args.project_id}. Type 'quit' to exit.\n")
    print("Commands: 'history', 'revert <n>', or any natural-language edit.\n")
    while True:
        try:
            q = input("edit> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in ("quit", "exit", "q"):
            return 0
        if q == "history":
            for row in agent.history(args.project_id):
                print(f"  v{row['version']:>3}  {row.get('description','')}")
            continue
        if q.startswith("revert "):
            ver = int(q.split()[1])
            agent.revert(args.project_id, ver)
            print(f"  reverted to v{ver}")
            continue
        result = agent.edit(EditCommand(project_id=args.project_id, query=q))
        print(f"  intent : {result.intent.intent}/{result.intent.target}/{result.intent.scope}")
        print(f"  result : success={result.success} version={result.new_version}")
        if not result.success:
            print(f"  error  : {result.error}")


def cmd_history(args: argparse.Namespace) -> int:
    sm = StateManager()
    rows = sm.history(args.project_id)
    if not rows:
        print("no history")
        return 1
    for r in rows:
        print(f"  v{r['version']:>3}  {r['created_at']}  {r['description']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    sm = StateManager()
    pids = sm.list_projects()
    if not pids:
        print("no projects yet")
        return 0
    for pid in pids:
        s = sm.latest(pid)
        title = s.script.story.title if s and s.script else "(untitled)"
        print(f"  {pid}  v{s.version if s else '-'}  {title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentic-video", description=__doc__)
    sub = p.add_subparsers(dest="cmd")

    rp = sub.add_parser("run", help="run the full pipeline")
    rp.add_argument("prompt")
    rp.add_argument("--duration", type=int, default=40)
    rp.add_argument("--scenes", type=int, default=4)
    rp.add_argument("--no-bgm", action="store_true")
    rp.add_argument("--no-subs", action="store_true")
    rp.add_argument("--subtitle-lang", default="English", help="Subtitle language")
    rp.add_argument("--no-real-video", action="store_true",
                    help="force ffmpeg ken-burns even if FAL_KEY is set (saves API credit)")
    rp.add_argument("--no-lipsync", action="store_true",
                    help="force heuristic lip sync even if FAL_KEY is set")
    rp.set_defaults(fn=cmd_run)

    pp = sub.add_parser("providers", help="show which API providers are detected")
    pp.set_defaults(fn=cmd_providers)

    sp = sub.add_parser("serve", help="launch the FastAPI web app")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(fn=cmd_serve)

    ep = sub.add_parser("edit", help="interactive edit REPL on an existing project")
    ep.add_argument("project_id")
    ep.set_defaults(fn=cmd_edit)

    hp = sub.add_parser("history", help="show version history")
    hp.add_argument("project_id")
    hp.set_defaults(fn=cmd_history)

    lp = sub.add_parser("list", help="list all known projects")
    lp.set_defaults(fn=cmd_list)
    return p


def main() -> int:
    parser = build_parser()
    if len(sys.argv) > 1 and sys.argv[1] not in (
        "run", "serve", "edit", "history", "list", "providers", "-h", "--help"
    ):
        # Treat first arg as a prompt for convenience.
        args = parser.parse_args(["run"] + sys.argv[1:])
    else:
        args = parser.parse_args()
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return args.fn(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
