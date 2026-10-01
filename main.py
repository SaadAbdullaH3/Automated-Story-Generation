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

# Load .env if python-dotenv is available. Tests set PIPELINE_SKIP_DOTENV=1 so a
# developer's real keys can never leak into them (no quota spent, no flaky runs).
if os.getenv("PIPELINE_SKIP_DOTENV") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

# Register all MCP tools.
import mcp.tools  # noqa: F401

from agents.edit_agent import EditAgent
from jobs.queue import JOB_KINDS
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
        burn_subtitles=not args.no_burn_subs,
        tts_engine=args.voice_engine,
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


def _print_storyboard(state) -> None:
    board = state.storyboard
    print()
    print("=" * 74)
    print(f"STORYBOARD  {state.project_id}   v{state.version}")
    if board:
        print(f"  {board.title} — {board.logline}")
        print(f"  {len(board.frames)} scenes, about "
              f"{board.estimated_duration_ms() / 1000:.0f}s")
    print("=" * 74)
    for frame in (board.frames if board else []):
        print(f"\n  [{frame.scene_id}] {frame.title}")
        print(f"      setting : {frame.setting}")
        print(f"      visual  : {frame.visual_prompt[:100]}")
        if frame.preview_path:
            print(f"      preview : {frame.preview_path}")
        for line in frame.dialogue:
            print(f"      {line.character_name or line.character_id:<12} {line.text[:64]}")
    print()
    print("-" * 74)
    print(f"Edit a scene:  python main.py restyle {state.project_id} <scene_id> "
          f'--visual "..."')
    print(f"Render it:     python main.py render {state.project_id}")
    print()


def cmd_plan(args: argparse.Namespace) -> int:
    """Write the script + preview images, then stop for review."""
    orch = PipelineOrchestrator()
    print(f"\n>>> planning storyboard for:\n    {args.prompt}\n")
    state = orch.plan(
        prompt=args.prompt,
        target_duration_s=args.duration,
        scene_count=args.scenes,
        with_preview=not args.no_preview,
        on_event=_print_event,
    )
    _print_storyboard(state)
    return 0


def cmd_storyboard(args: argparse.Namespace) -> int:
    state = StateManager().latest(args.project_id)
    if not state or not state.script:
        print(f"no storyboard for {args.project_id}")
        return 1
    _print_storyboard(state)
    return 0


def cmd_restyle(args: argparse.Namespace) -> int:
    """Edit one storyboard scene before rendering."""
    orch = PipelineOrchestrator()
    dialogue = dict(pair.split("=", 1) for pair in args.line) if args.line else None
    try:
        state = orch.update_storyboard(
            args.project_id, args.scene_id, title=args.title, setting=args.setting,
            visual_prompt=args.visual, dialogue=dialogue,
        )
    except ValueError as e:
        print(f"error: {e}")
        return 1
    _print_storyboard(state)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render an approved storyboard into the finished film."""
    orch = PipelineOrchestrator()
    print(f"\n>>> rendering {args.project_id}\n")
    try:
        state = orch.render(
            args.project_id,
            with_bgm=not args.no_bgm,
            with_subtitles=not args.no_subs,
            subtitle_language=args.subtitle_lang,
            burn_subtitles=not args.no_burn_subs,
            tts_engine=args.voice_engine,
            use_text_to_video=False if args.no_real_video else None,
            use_lip_sync=False if args.no_lipsync else None,
            on_event=_print_event,
        )
    except ValueError as e:
        print(f"error: {e}")
        return 1
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
    print("Run `python main.py providers --check` to call each one for real.")
    print()

    if getattr(_args, "check", False):
        return _check_providers()
    return 0


def _check_providers() -> int:
    """Make one tiny real call per role so credentials can be verified."""
    import tempfile
    import time

    from mcp.tool_executor import ToolExecutor
    from mcp.tools.llm_tools.llm_client import get_llm_client

    print("Live check")
    print("-" * 74)
    failures = 0

    for role in ("story", "edit_intent"):
        client = get_llm_client(role, force_new=True)
        if client.provider == "mock":
            print(f"  {role:<12} skipped (no model configured)")
            continue
        started = time.monotonic()
        resp = client.generate("Reply with exactly: OK", max_tokens=20, temperature=0)
        elapsed = time.monotonic() - started
        ok = resp.provider != "mock"
        failures += 0 if ok else 1
        answer = resp.text.strip().replace("\n", " ")[:40]
        print(f"  {role:<12} {'OK ' if ok else 'FAILED'} via {resp.provider}"
              f" ({resp.model}) in {elapsed:.1f}s: {answer!r}")

    # Translation may be served by an LLM or by MyMemory, so use the tool itself.
    started = time.monotonic()
    res = ToolExecutor().execute("text.translate", lines=["Good evening."],
                                 target_language="French")
    elapsed = time.monotonic() - started
    failures += 0 if res.success else 1
    print(f"  {'translate':<12} {'OK ' if res.success else 'FAILED'} via "
          f"{res.metadata.get('provider') if res.success else res.error} in {elapsed:.1f}s"
          + (f": {res.data[0]!r}" if res.success else ""))

    from shared import providers
    image_spec = providers.active("image")
    if image_spec:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            res = ToolExecutor().execute(
                "vision.generate_image", prompt="a red apple on a table",
                out_path=f"{tmp}/probe.png", width=256, height=256,
            )
            elapsed = time.monotonic() - started
            served = res.metadata.get("provider") if res.success else None
            expected = served == image_spec.provider
            failures += 0 if (res.success and expected) else 1
            detail = f"served by {served}" if res.success else res.error
            print(f"  {'image':<12} {'OK ' if expected else 'FELL BACK'} "
                  f"(wanted {image_spec.provider}, {detail}) in {elapsed:.1f}s")

    print()
    print("All configured providers answered." if not failures
          else f"{failures} role(s) did not use their preferred provider — see the log above.")
    print()
    return 1 if failures else 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Run a pipeline worker: claim queued jobs and execute them."""
    from jobs import worker
    print(f"worker up - database {_database_label()}")
    print("waiting for jobs (Ctrl-C to stop)\n")
    try:
        if args.once:
            job = worker.run_once(kinds=args.kinds or None)
            print(f"ran {job.id} ({job.kind})" if job else "queue empty")
        else:
            worker.run_forever(poll_interval=args.poll, kinds=args.kinds or None)
    except KeyboardInterrupt:
        print("\nstopping")
    return 0


def _read_password(prompt: str) -> str:
    """Ask for a password without echoing it, or read it from a pipe."""
    import getpass
    if sys.stdin is not None and not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    return getpass.getpass(prompt)


def cmd_users(args: argparse.Namespace) -> int:
    """Manage accounts. This is the way back in if nobody can sign in."""
    from auth import accounts, sessions

    action = args.action or "list"
    if action == "list":
        people = accounts.list_users()
        if not people:
            print("no accounts yet - the first person to open the web UI becomes admin,")
            print("or run: python main.py users create <email> --admin")
            return 0
        print(f"{'email':<36}{'role':<8}{'active':<8}last sign-in")
        for u in people:
            last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "never"
            print(f"{u.email:<36}{u.role:<8}{'yes' if u.is_active else 'no':<8}{last}")
        return 0

    if action == "create":
        password = args.password or _read_password(f"password for {args.email}: ")
        try:
            user = accounts.create(args.email, password,
                                   role="admin" if args.admin else "user")
        except accounts.AuthError as e:
            print(f"error: {e}")
            return 1
        print(f"created {user.role} {user.email}")
        return 0

    user = accounts.get_by_email(args.email) if args.email else None
    if user is None:
        print(f"no account for {args.email}")
        return 1

    if action == "passwd":
        password = args.password or _read_password(f"new password for {user.email}: ")
        try:
            accounts.set_password(user.id, password)
        except accounts.AuthError as e:
            print(f"error: {e}")
            return 1
        ended = sessions.revoke_all(user.id)
        print(f"password changed; {ended} session(s) signed out")
        return 0

    if action == "role":
        if args.value not in ("user", "admin"):
            print("role must be 'user' or 'admin'")
            return 1
        accounts.set_role(user.id, args.value)
        print(f"{user.email} is now {args.value}")
        return 0

    if action in ("enable", "disable"):
        accounts.set_active(user.id, action == "enable")
        if action == "disable":
            sessions.revoke_all(user.id)
        print(f"{user.email} {action}d")
        return 0

    print(f"unknown action {action!r}")
    return 1


def cmd_jobs(args: argparse.Namespace) -> int:
    """Show the queue."""
    import jobs as job_queue
    rows = job_queue.list_jobs(project_id=args.project_id, status=args.status,
                               limit=args.limit)
    if not rows:
        print("no jobs")
        return 0
    print(f"{'job':<18}{'kind':<12}{'status':<11}{'attempts':<9}project")
    for job in rows:
        print(f"{job.id:<18}{job.kind:<12}{job.status:<11}"
              f"{job.attempts}/{job.max_attempts:<7}{job.project_id}")
        if job.error:
            print(f"    {job.error.splitlines()[-1][:110]}")
    return 0


def _database_label() -> str:
    from shared import db
    url = db.get_engine().url
    return f"{url.get_backend_name()} ({url.database})"


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
        stage = s.stage if s else "-"
        print(f"  {pid}  v{s.version if s else '-'}  {stage:<10}  {title}")
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
    rp.add_argument("--no-burn-subs", action="store_true",
                    help="keep subtitles as soft tracks only (most players hide those "
                         "behind a menu) instead of burning them into the picture")
    rp.add_argument("--voice-engine", default=None,
                    help="kokoro | edge | gtts | pyttsx3 (default: config/providers.yaml)")
    rp.add_argument("--no-real-video", action="store_true",
                    help="force ffmpeg ken-burns even if FAL_KEY is set (saves API credit)")
    rp.add_argument("--no-lipsync", action="store_true",
                    help="force heuristic lip sync even if FAL_KEY is set")
    rp.set_defaults(fn=cmd_run)

    # ---- storyboard workflow: plan -> review/edit -> render ----------------
    pl = sub.add_parser("plan", help="write the script + preview images, then stop for review")
    pl.add_argument("prompt")
    pl.add_argument("--duration", type=int, default=40)
    pl.add_argument("--scenes", type=int, default=4)
    pl.add_argument("--no-preview", action="store_true",
                    help="skip the preview images (script only)")
    pl.set_defaults(fn=cmd_plan)

    sb = sub.add_parser("storyboard", help="show a project's storyboard")
    sb.add_argument("project_id")
    sb.set_defaults(fn=cmd_storyboard)

    rs = sub.add_parser("restyle", help="edit one storyboard scene before rendering")
    rs.add_argument("project_id")
    rs.add_argument("scene_id")
    rs.add_argument("--title")
    rs.add_argument("--setting")
    rs.add_argument("--visual", help="new visual prompt (redraws the preview)")
    rs.add_argument("--line", action="append", metavar="LINE_ID=TEXT",
                    help="rewrite a line, e.g. --line scene_1_l1='Hello there'")
    rs.set_defaults(fn=cmd_restyle)

    rd = sub.add_parser("render", help="render an approved storyboard into the film")
    rd.add_argument("project_id")
    rd.add_argument("--no-bgm", action="store_true")
    rd.add_argument("--no-subs", action="store_true")
    rd.add_argument("--subtitle-lang", default="English")
    rd.add_argument("--no-burn-subs", action="store_true")
    rd.add_argument("--voice-engine", default=None,
                    help="kokoro | edge | gtts | pyttsx3 (default: config/providers.yaml)")
    rd.add_argument("--no-real-video", action="store_true")
    rd.add_argument("--no-lipsync", action="store_true")
    rd.set_defaults(fn=cmd_render)

    pp = sub.add_parser("providers", help="show which model serves each role")
    pp.add_argument("--check", action="store_true",
                    help="also make one real call per role to verify credentials")
    pp.set_defaults(fn=cmd_providers)

    sp = sub.add_parser("serve", help="launch the FastAPI web app")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(fn=cmd_serve)

    wp = sub.add_parser("worker", help="run a pipeline worker (claims queued jobs)")
    wp.add_argument("--once", action="store_true", help="run a single job, then exit")
    wp.add_argument("--poll", type=float, default=1.0, help="seconds between polls")
    wp.add_argument("--kinds", nargs="*", default=[],
                    help=f"only these job kinds ({', '.join(JOB_KINDS)})")
    wp.set_defaults(fn=cmd_worker)

    up = sub.add_parser("users", help="manage accounts (list, create, passwd, role)")
    up.add_argument("action", nargs="?", default="list",
                    choices=["list", "create", "passwd", "role", "enable", "disable"])
    up.add_argument("email", nargs="?")
    up.add_argument("value", nargs="?", help="for 'role': user | admin")
    up.add_argument("--admin", action="store_true", help="for 'create'")
    up.add_argument("--password", default=None,
                    help="skip the prompt (visible in shell history - prefer the prompt)")
    up.set_defaults(fn=cmd_users)

    jp = sub.add_parser("jobs", help="show the job queue")
    jp.add_argument("--project-id", default=None)
    jp.add_argument("--status", default=None,
                    help="queued | running | succeeded | failed | cancelled")
    jp.add_argument("--limit", type=int, default=25)
    jp.set_defaults(fn=cmd_jobs)

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
        "run", "plan", "storyboard", "restyle", "render", "serve", "edit",
        "history", "list", "providers", "worker", "jobs", "users", "-h", "--help"
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
