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
    """Print which providers are detected and what features are unlocked."""
    from mcp.tools.llm_tools.llm_client import get_llm_client
    llm = get_llm_client()

    fal = bool(os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY"))
    rep = bool(os.getenv("REPLICATE_API_TOKEN"))
    hf = bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY"))
    eleven = bool(os.getenv("ELEVENLABS_API_KEY"))
    sd = bool(os.getenv("SD_API_URL"))

    def yn(b): return "[YES]" if b else "[ -- ]"

    print()
    print("Provider configuration")
    print("-" * 60)
    print(f"  LLM           : {llm.provider}  (model={llm.model})")
    if llm.provider == "mock":
        print("                   set GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY for real LLM")
    local_sd = os.getenv("LOCAL_SD") == "1"
    if local_sd:
        img_line = f"local Diffusers ({os.getenv('LOCAL_SD_MODEL', 'stabilityai/sdxl-turbo')})  [primary]"
    elif sd:
        img_line = f"Stable Diffusion WebUI ({os.getenv('SD_API_URL')})  [primary]"
    else:
        img_line = (f"pollinations.ai ({os.getenv('POLLINATIONS_MODEL', 'tongyi-mai/z-image-turbo')})"
                    if os.getenv("POLLINATIONS_API_KEY") else
                    "pollinations.ai legacy keyless endpoint (reduced quality — "
                    "set POLLINATIONS_API_KEY)")
    if os.getenv("OPENAI_API_KEY"):
        img_line += "  +  OpenAI"
    print(f"  Image gen     : {img_line}")
    print("  TTS           : edge-tts (default)  +  gTTS / pyttsx3 fallbacks"
          + ("  +  ElevenLabs" if eleven else ""))
    translate = "LLM" if llm.provider != "mock" else "MyMemory (free)"
    print(f"  Subtitles     : translation via {translate}"
          + ("  [MyMemory email set]" if os.getenv("MYMEMORY_EMAIL") else ""))

    if fal:
        t2v = "fal.ai (Stable Video Diffusion)"
    elif rep:
        t2v = "Replicate (SVD)"
    elif hf:
        t2v = "Hugging Face (DAMO text-to-video)"
    else:
        t2v = "(none configured)"

    if fal:
        ls = "fal.ai SadTalker"
    elif rep:
        ls = "Replicate Wav2Lip / SadTalker"
    else:
        ls = "heuristic mouth-zoom (offline fallback)"

    print(f"  Text-to-video : {t2v}  {yn(fal or rep or hf)}")
    print(f"  Lip sync      : {ls}  {yn(fal or rep)}")
    tier = "PREMIUM (real motion + real lip sync)" if (fal or rep) else "STANDARD (multi-shot ffmpeg)"
    print(f"  Video tier    : {tier}")
    print()
    print("-" * 60)
    print("Setup guide: docs/REAL_VIDEO_SETUP.md")
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
