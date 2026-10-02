# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

An agentic pipeline that turns one natural-language prompt into a short animated
film, with natural-language editing and versioned undo. Originally a FAST-NUCES
Agentic AI semester project; now being developed into a production-grade product.

Pipeline: **Phase 1 Story** → **Phase 2 Audio** → **Phase 3 Video**, plus
**Phase 4 Web UI** (FastAPI + vanilla JS) and **Phase 5 Edit & Undo**.

## Working agreement

- Work is split into milestones (M0 baseline → M1 correctness → M2 model settings layer
  and free-tier upgrades → M3 quality → M4 production architecture → M5 premium video
  → M6 ship polish).
- **At the end of every milestone: commit, push, open a draft PR, report back, and
  wait for the owner's go-ahead before starting the next milestone.**
- Current constraint: **$0 spend.** Use the best free API tiers and open-source
  models. Paid providers (Claude, Seedance, etc.) come later, so keep every model
  choice swappable through configuration, not hard-coded in agent logic.
- The dev machine is Windows 11 with an RTX 3050 Laptop GPU (6 GB VRAM) and 16 GB RAM.
  Heavy video models (Wan 2.2, lip-sync) must run on free cloud GPUs, not locally.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# optional local Stable Diffusion (pulls PyTorch): see requirements-local-sd.txt
cp .env.example .env    # all keys optional; runs in mock/template mode without them
```

`ffmpeg` and `ffprobe` must be on PATH (Windows: `winget install Gyan.FFmpeg`).
The default TTS engine shells out to the `edge-tts` CLI, which lives in
`.venv/Scripts`, so activate the venv (or put it on PATH) before running.

## Commands

```bash
python -m pytest -q                        # full suite (~3 min, offline: mock LLM + placeholder images)
TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest -q   # same suite, real Postgres
TEST_S3_ENDPOINT=http://127.0.0.1:5111 python -m pytest -k real_s3   # against a real S3/R2 server
python -m pytest tests/unit/test_phase5_edit.py -q
python main.py "prompt" --duration 30 --scenes 4   # CLI end-to-end run
python main.py plan "prompt" --scenes 4            # storyboard only (cheap)
python main.py storyboard|restyle|render <pid>     # review, edit, then render
python scripts/benchmark.py --offline              # fixed prompts, measured
python main.py providers                   # which providers are detected
python main.py serve --reload              # web UI on http://localhost:8000 (runs a worker too)
cd web && npm ci && npm run build          # build the creator UI; serve then uses it at /
cd web && npm run dev                      # UI with hot reload on :3000, API proxied to :8000
python main.py worker                      # a worker process on its own (WORKER_INLINE=0 for the API)
python main.py jobs [--status failed]      # the queue: what ran, what broke
python main.py users [create <email> --admin | passwd <email> | role <email> admin]
docker compose up --build --scale worker=3 # API + workers + Postgres
python main.py edit <project_id>           # interactive edit REPL
python main.py list | history <project_id>
```

Outputs go to `data/outputs/<project_id>/`, version snapshots to
`data/state_versions/`, and the version log to `data/state.db` (all gitignored).
`DATA_DIR=/somewhere` moves all three — a throwaway instance for testing never
touches the real films (state records absolute paths, so copying `data/` is
not enough).

## Architecture map

| Area | Path | Notes |
|---|---|---|
| Cross-phase contract | `shared/schemas/` | Pydantic models. `PipelineState` is the object passed between phases and versioned. Change these carefully: every phase depends on them. |
| Orchestrator | `agents/orchestrator/` | Plain-Python graph (not LangGraph) running phase1 → phase2 → phase3, emitting `ProgressEvent`s. |
| Phase 1 | `agents/story_agent/` | LLM structured output → `ScriptOutput`; `planner.py` is the deterministic template used when the LLM is `mock`; `appearance.py` locks each character's look + image seed. |
| Storyboard | `shared/schemas/storyboard.py` + orchestrator `plan`/`update_storyboard`/`render` | The cheap draft (script + one preview image per scene) that is reviewed and edited before the expensive render. `PipelineState.stage` is draft → storyboard → rendered. |
| Timeline | `shared/timeline.py` | **Single source of truth for timing.** Audio places lines on it, video cuts on its boundaries (absolute ms → frames), subtitles read it. Never time video or audio independently. |
| Phase 2 | `agents/audio_agent/` | `render_line` (TTS) → `retime` (timeline) → `remix` (per-scene BGM + master with lines placed at `start_ms`). Edits reuse these. |
| Camera | `agents/video_agent/camera.py` | Camera moves and the grade over them. The move is computed at 3x the output size and scaled down, which is what makes pans smooth enough to use; `move_for` picks it from the shot's job and the scene's tone, `grade_for` from the story's own `visual_style`. |
| Phase 3 | `agents/video_agent/` | `run`: portraits + 3-image shot bank per scene. `compose`: plan shots from the timeline → render changed scenes only (`plan_signature`) → scene crossfades → master mux → speed → soft-sub tracks. Edits call `compose`. |
| Phase 5 | `agents/edit_agent/` | `intent_classifier.py` (LLM or regex; matches character names) → `planner.py` (steps) → `executor.py` (reuses Phase 2/3 primitives) → snapshot. Runs as an `edit` job; starts from, and on failure returns to, the saved version's files. `describe.py` says what a request was understood as. Scene-only voices live in `AudioOutput.scene_voices`. |
| Versioning | `state_manager/` | Append-only SQLite log + asset copies per version; `referenced_files(state)` collects every file the state points to. Revert creates a new version. |
| Languages | `shared/languages.py` | Supported subtitle languages (ISO codes + MyMemory codes). UI dropdown is served from here. |
| Tool layer | `mcp/` | Internal tool registry (**not** the MCP protocol). Tools register on `import mcp.tools`; agents call `ToolExecutor().execute("audio.tts", ...)`. Each tool tries providers in order and falls back. |
| Model settings | `config/providers.yaml` + `shared/providers.py` | **Which model serves each role** (story, edit_intent, translate, image, tts, music). Agents never name a model: they call `providers.chain(role)` and use the first available, falling through on failure. `concurrency:` per provider drives `shared/utils/parallel.run_jobs`. |
| LLM client | `mcp/tools/llm_tools/llm_client.py` | `get_llm_client(role)` walks that role's chain: Gemini (google-genai, native JSON schema), Groq / OpenRouter / Ollama / OpenAI (all via the openai client), Anthropic, then `mock` = use the offline fallback. |
| Accounts | `auth/` | `passwords.py` (argon2id), `accounts.py` (users, lockout, project ownership), `sessions.py` (opaque token in an HttpOnly cookie, stored hashed), `deps.py` (**the only place a request becomes a user** — routes depend on `require_user` / `require_project` rather than checking for themselves). |
| Jobs | `jobs/` | **Runs are rows, not closures.** `queue.py` (enqueue/claim/heartbeat/cancel, one atomic UPDATE per claim) and `worker.py` (claims jobs, runs the orchestrator, publishes assets). Cancellation lands between pipeline steps, never mid-ffmpeg. **One job per project at a time, in queue order** — everything that writes a version (plan, render, edit, revert) goes through here. |
| Database | `shared/db.py` | One database for the version log and the queue. SQLite (WAL) by default, Postgres via `DATABASE_URL`, same SQL through SQLAlchemy Core. |
| Assets | `shared/assets.py` | The only place a file path becomes a URL. Local disk by default; `STORAGE_URL=s3://bucket` publishes to any S3-compatible bucket (R2) so the worker and the API need not share a filesystem. |
| Voices | `shared/voices.py` + `backend/routes/voices.py` | Which engines work on this machine and why not, their voices, and a cached one-line sample so a voice can be heard before a render. |
| Fonts | `shared/fonts.py` | Picks an installed font with the script's glyphs (`Segoe UI` on Windows, Noto in the container). A missing font draws nothing rather than failing. |
| Backend | `backend/` | FastAPI routes, DB-backed progress (`services/progress.py`), WebSocket at `/ws/progress/{pid}`, `/assets` serves `data/outputs`. |
| Frontend | `web/` | **The creator interface.** Next.js 16 static export, served by FastAPI at `/` when built. The storyboard is the screen: a plan streams in (script first, then each frame), the creator edits it, renders, and watches with scenes as chapters. See `web/README.md`. |
| Storyboard cards | `agents/orchestrator/storyboard_view.py` | The single source of a card — tone, camera move, lines and the voice that will speak each — used by both the live plan stream and the storyboard endpoint, so a reload shows exactly what the stream did. |
| Classic UI | `frontend/src/` | The original vanilla page, kept at `/classic/` while the new one takes over. |

## Conventions

- Tools return `ToolResult(success, data, error, metadata)`; `safe_run` turns exceptions into failures.
- New tools subclass `mcp.base_tool.BaseTool` and are registered in the category `__init__.py`.
- Every phase writes JSON artifacts to disk and records paths in `state.phaseN.artifact_paths`.
  Snapshots copy every file referenced anywhere in the state (`state_manager.snapshot.referenced_files`),
  so anything an agent or edit records in the state is undoable.
- Clips are rendered to exact frame counts (`-frames:v`), and each clip except the last in a
  crossfade chain gets extra frames (`xfade_frames`) so crossfades never shorten the film.
- Providers must fail loudly: return `success=False` (or raise) rather than silently shipping
  degraded output (e.g. untranslated subtitles, error pages saved as images).
- Test helpers in `tests/conftest.py`: `isolated_dirs`, `small_project` (full 320x180@12fps render
  with silent TTS), `silence_tts(tools)`, `fake_translation`.
- Tests force `LLM_PROVIDER=mock` and `PROVIDER_IMAGE=placeholder` (see `tests/conftest.py`)
  and monkeypatch `shared.constants` paths into `tmp_path`. Point `PROVIDERS_FILE` at a
  temp YAML to test other chains, and call `providers.load(force=True)` after changing env.
- Adding a provider = a `_provider_<name>` method (images) or an adapter in `llm_client`,
  plus an entry in `config/providers.yaml`. Never add an `if os.getenv(...)` chain to an agent.

## Baseline (M0, 2026-09-18)

52/52 tests pass. One CLI run (`--duration 30 --scenes 4`, mock LLM, edge-tts, Pollinations)
took **779 s**: story <1 s, audio 32 s, **images ~630 s (15 serial Pollinations calls, ~42 s each)**,
shot rendering + scene assembly 24 s, final compose ~7 s, subtitle translation 81 s.
Measured voice-vs-picture drift: the voice leads the picture by 2.4 s in scene 1, 3.6 s in scene 2, 5.0 s in scene 3 and 6.1 s in scene 4.

## M1 results (2026-09-19)

87/87 tests pass. Fixed: audio/video/subtitle drift (single timeline; final video frame count
equals the timeline exactly), duration overshoot (template + LLM word budget), untranslated
subtitle tracks (LLM → MyMemory, failed languages skipped), Pollinations downgrade (keyed endpoint
support, image validation, exact size/format, warning when degraded), edit voice bugs (voice kept,
tone/rate/pitch/volume applied via edge-tts prosody, character names understood), edits dropping
multi-language subs and speed, snapshot coverage, silent no-op filters, thread-unsafe progress
events, Windows path bug, UI language list, re-run phase resetting settings.

Real run (same prompt as M0, `--duration 30 --scenes 4 --subtitle-lang Urdu`): 662 s total (images
still ~630 s via the legacy Pollinations endpoint), 30.8 s film (was 60.5 s), final video 739/739
frames = timeline, every line on a cut, Urdu + English tracks. Whisper edit on Aria re-recorded only
her lines (slower -> timeline 34.9 s -> 37.1 s) and the video followed exactly (891/891 frames).
Scene filter edit ~6 s; revert 0.2 s and byte-identical to v1.

## M2 results (2026-09-21)

111/111 tests pass. Added the model settings layer (`config/providers.yaml`): every agent
resolves its model through a role chain with automatic fallback, so swapping in paid
providers later is a config edit. LLM providers: Gemini via the current google-genai SDK
(native structured output) plus Groq, OpenRouter and Ollama through the openai client;
`gemini-flash-latest` is used as the model alias so a retired id can't break it. Images:
Cloudflare Workers AI FLUX (free 10k neurons/day) ahead of Pollinations, with provider
metadata and validation kept from M1. Image and TTS jobs now run in parallel up to each
provider's `concurrency`. Structured-output retries now only repeat for malformed JSON;
a provider that is down is abandoned immediately.

Providers get `retries:` attempts (3 for the free image endpoints) with backoff before the
chain falls through; permanent errors (401/403/400) fail fast. Measured on the live keyless
Pollinations endpoint: 6 images, 2 hit 500s, retries rescued 1 (succeeded on attempt 3) and
1 exhausted its attempts — so retries halved the images lost to placeholders. `python main.py providers`
lists every role's chain and what each alternative needs; `--check` makes one real call per
role to verify keys.

Measured: the keyless Pollinations endpoint 429s on parallel requests (1 per IP confirmed by
experiment), so parallelism only pays off with Cloudflare or a local model. Live M2 run
(24 s, 3 scenes): 397 s, TTS lines 4-up in 5.3 s, 664/664 frames, edits and revert unchanged.
**Not yet verified against live Gemini/Groq/Cloudflare APIs** — no keys on this machine;
adapters are covered by mocked tests.

## M3 results (2026-09-22)

136/136 tests pass. Quality milestone:
- **Mix**: music side-chains to the dialogue (measured ~6 dB duck while a line plays,
  recovering over the next second) so the bed sits louder (0.18 → 0.32); every master is
  normalised to -16 LUFS.
- **Storyboard**: `plan` (script + one preview per scene) → review/edit → `render`, in CLI,
  API and UI, each step snapshotted. Verified end to end in the browser.
- **Character consistency**: `appearance_lock` + fixed `image_seed` per character, used by
  every portrait prompt; "change character design" re-rolls and persists them.
- **Benchmark**: `scripts/benchmark.py` reports per-phase time, length vs target, frame-exact
  sync, lines-on-cuts and which provider served each image; `--offline` runs anywhere.
  Its first real run found the planner's speaking-rate estimate was wrong (films came out
  ~17% long); calibrating WORDS_PER_SECOND 2.6 -> 2.2 against measured edge-tts brought a
  24 s target to 23.5 s (2.1% error). Latest run in docs/BENCHMARK.md.

## Live provider verification (2026-10-01)

Keys arrived; `python main.py providers --check` found four real problems, all now fixed:
- **Reasoning models returned nothing** at small token caps (gemini-flash-latest and
  gpt-oss spend output tokens thinking: 20 tokens -> empty/MAX_TOKENS, 600 -> "OK").
  `MIN_OUTPUT_TOKENS = 600` floor, `reasoning_effort=low` for gpt-oss, and an empty
  response now reports its finish_reason instead of "empty response".
- **Cloudflare flux-1-schnell rejects `seed`** (HTTP 400). The error names the field, so
  the provider drops it and retries once.
- **A Pollinations key with no pollen budget 402s**; it now falls back to the keyless
  endpoint for the rest of the run instead of failing the image.
- **Tests were picking up the real .env** (main.py loads it on import), which would spend
  the owner's quota. conftest sets PIPELINE_SKIP_DOTENV=1 and scrubs every credential;
  scripts/benchmark.py loads .env the same way the app does.

First fully live run (24 s, 3 scenes, Urdu subs): **87.8 s** (was 146 s keyless), script by
Gemini/Groq, 12 of 13 images from Cloudflare FLUX, frame-exact sync, 4/4 lines on cuts,
5.8% length error. In that one run Gemini 503'd and Groq took over, and one prompt was
refused by Cloudflare as NSFW (false positive) and was covered by Pollinations — the
chain degraded visibly instead of silently. See docs/BENCHMARK.md.

## Post-M3 fixes from watching a real film (2026-10-01)

Saad watched the first live render and reported three things:
- **Subtitles never appeared** in VLC or anywhere else. The data was fine (extracting the
  track gave correct Urdu); players simply don't auto-enable mov_text in MP4. Now the
  chosen language is burned in by default (`--no-burn-subs` opts out, `VideoOutput.
  burn_subtitles`), the rest stay as soft tracks minus the burned one, and `.srt`/`.vtt`
  sidecars are written beside the video (VLC auto-loads the .srt; the web player attaches
  the .vtt via `/api/pipeline/subtitles/{pid}`). Non-Latin scripts get a font with the
  glyphs — a Latin-only font drops them silently.
- **Every image looked like anime** regardless of story, from one hardcoded style string.
  `agents/story_agent/visual_style.py` now derives the look: `StoryOutput.visual_style`
  (LLM) > genre preset > default, with the scene tone nudging lighting and the wrong look
  pushed into the negative prompt. `VIDEO_STYLE` overrides everything.
- **Wanted an open-source voice.** Kokoro (Apache-2.0, onnxruntime, no torch, no GPU,
  ~340 MB of model files via `scripts/get_kokoro.py`) is now first in the tts chain when
  `KOKORO_MODEL` is set; ~2 s per line on this laptop. Voice names are per engine
  (`AudioAgent.voice_for`), so "change voice" picks alternates from the right pool.

## M4 results (2026-10-01)

195/195 tests pass. Production architecture:
- **Durable jobs.** A run was a `BackgroundTasks` closure plus a module-level
  dict: a restart orphaned it, nothing outside the process could see it, and it
  couldn't be stopped. Now the API enqueues and returns in milliseconds, a worker
  claims the job with a single atomic UPDATE (Postgres `SKIP LOCKED`; on SQLite
  the `status='queued'` guard makes a second claim match nothing), and progress
  events are rows, so a reconnecting browser replays what it missed. A worker
  that stops heartbeating for two minutes has its job requeued, bounded by
  `max_attempts`. Cancellation is cooperative and lands between steps.
- **One database, two backends.** `shared/db.py` holds the version log and the
  queue through SQLAlchemy Core: SQLite in WAL mode locally (so the API and the
  inline worker can both write), Postgres by setting `DATABASE_URL`. Setting
  `TEST_DATABASE_URL` runs the whole suite against a real server instead of
  throwaway SQLite files, so the Postgres path is tested, not assumed.
- **Scales apart.** `main.py serve` still runs a worker thread, so a laptop
  needs one command; `WORKER_INLINE=0` plus `main.py worker` splits them, and
  `docker compose up --scale worker=3` runs three renders at once.
- **Asset URLs in one place.** `shared/assets.py` replaced three hand-built
  `/assets/...` strings; with `STORAGE_URL` set it publishes to an
  S3-compatible bucket after a job succeeds. **Verified against real
  Cloudflare R2** (2026-10-01): a finished film's 31 referenced assets, 17 MB,
  uploaded through the worker's own publish step, and the film came back
  byte-identical over a presigned URL.
- **Choose and hear the voice.** `/api/voices` says which engines work here and
  why not; `/api/voices/preview` renders a cached one-line sample. The choice
  rides with the run to the audio agent, and a re-run keeps it. A preview that
  fell back to another engine says so rather than passing it off.
- **Portable fonts.** The burned-in subtitle font is resolved against what is
  installed (`fc-match` where available), because `Segoe UI` does not exist in
  the Linux image and a missing font silently draws nothing.

Measured live, API and worker as separate processes:
- A 24 s / 3-scene film with Kokoro voices and burned Urdu subtitles: the POST
  returned at once, the worker finished in **118.8 s**, and the film is correct
  (1280x720, Urdu burned in with right-to-left shaping, English soft track).
- A Kokoro sample for the voice picker rendered in **4.4 s** and is then cached.
- Cancelling a running job stopped it **16.3 s** later at the next step, left no
  error event behind, and the worker went straight on to the next job. (That
  last part needed a fix: `JobCancelled` derives from BaseException, because the
  orchestrator's `except Exception` was catching a deliberate stop and reporting
  it to the browser as a failure.)
- A job queued while the API was **shut down entirely** was still `queued`
  afterwards and ran to completion when the API came back — the exact case the
  old in-memory registry lost.

Verified against a real Postgres 16 (`docker run postgres:16-alpine`):
- The **whole suite, 195/195**, passes with `TEST_DATABASE_URL` pointed at it.
- 40 jobs, 4 separate OS processes claiming at once: 11/11/9/9, **no job
  claimed twice, none left behind**. This is what `SKIP LOCKED` is for.
- The real stack — API plus two worker processes on one Postgres — rendered two
  films concurrently, one on each worker. Wall clock 327 s for both, but they
  were 98 s and 324 s: two ffmpeg pipelines on one laptop fight for cores, so
  extra workers pay off across machines, not on this one.

## Cloudflare account check (2026-10-01)

- `CLOUDFLARE_API_TOKEN` is scoped to **Workers AI only**: `/accounts/{id}` and
  `/accounts/{id}/r2/buckets` both return 403, while the Workers AI endpoints
  answer normally. R2 therefore cannot be reached with the key already in `.env`.
- Workers AI itself is fine, but the **10,000 neuron/day free allocation was
  exhausted** by the M4 verification renders (HTTP 429, "you have used up your
  daily free allocation"). It resets daily; until then images fall through to
  Pollinations' keyless endpoint, which is what the chain is for.
- R2 is now set up (bucket `multi-agent-storygen`). Its S3 access key is
  bucket-scoped, so `ListBuckets` and `HeadBucket` on any other name return
  403 rather than 404 — the bucket name has to be known, it cannot be
  discovered. The `cfat_` management token cannot read the Cloudflare API at
  all (403), which does not matter: only the S3 keys are used.
  `TEST_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
  TEST_S3_BUCKET=multi-agent-storygen python -m pytest -k real_s3` re-proves
  it in one run.
- Testing against a real S3 server found one bug: boto3 defaults to
  virtual-host addressing (`bucket.host`), which needs DNS per bucket and fails
  on R2/MinIO-style endpoints. `shared/assets.py` now uses path addressing
  whenever `S3_ENDPOINT_URL` is set.

## M5 results (2026-10-01)

233/233 tests pass, 37 of them new. Every project used to be visible to anyone
who could reach the API, and `/assets/<project_id>/final_output.mp4` handed over
the finished film to anyone who guessed an id.

- **Sessions, not JWTs.** The cookie carries a random token; the database stores
  only its SHA-256, so a dump can't be replayed as a login. Because a session is
  a row, signing out actually signs out and an admin disabling an account ends
  its sessions immediately — no denylist, no waiting for an expiry.
- **argon2id** via argon2-cffi, with a dummy verify on unknown accounts so the
  login endpoint can't be timed to enumerate who is registered. Wrong password
  and unknown address return the identical 401. Eight failures locks the account
  for 15 minutes.
- **Enforced on the router, not per endpoint.** `dependencies=[Depends(require_user)]`
  is declared where the routers are mounted, so a new endpoint is protected the
  moment it is added. Project-scoped routes take `require_project`, which answers
  **404 rather than 403** — a different status would confirm the id exists.
- **Assets are no longer a static mount.** `/assets/{project_id}/{path}` checks
  ownership, blocks traversal out of the outputs directory, and still answers
  Range requests so seeking in the player works.
- **The WebSocket authenticates too** (the cookie rides the handshake) and closes
  with 1008 for a stranger; progress for any project was previously readable.
- **CORS was `allow_origins=["*"]` with credentials.** Now no cross-origin request
  is allowed unless `CORS_ORIGINS` names one.
- First account is the admin and adopts every project that existed before
  accounts did — including any whose owner was later deleted. Further sign-ups
  need `ALLOW_SIGNUPS=1`; `python main.py users` is the way back in either way.
- The session cookie is `Secure` only when the request arrived over HTTPS;
  marking it Secure on plain HTTP makes the browser drop it, and the sign-in
  appears to work and then doesn't.

Verified in a browser: first-run setup screen, sign-in, the voice preview
working through an authenticated session, sign-out returning to the gate. With
no session, `/api/projects/` and a real film's `/assets/...` URL both answer 401.

## M6 results (2026-10-01)

272/272 tests pass, 39 of them new. Premium video, at $0.

- **Pans work again.** They had been removed with a comment explaining that
  `zoompan` positions its crop window at integer pixels, so a sub-pixel move
  per frame rounds unevenly and the picture shivers. That was true, so it was
  measured rather than argued with — per-frame shift by phase correlation,
  against a 0.000 px floor on a locked-off shot:

      supersample   std dev   worst jump   render (3 s shot)
      1.6x (old)    0.494 px    0.631 px        1.1 s
      3x (now)      0.312 px    0.397 px        2.8 s
      4x            0.242 px    0.365 px        5.1 s

  The move is computed at 3x and scaled down with lanczos, turning a
  whole-pixel error upstream into a fraction of an output pixel. On real shot
  clips from a finished film the new shots travel **up to 5.13 px/frame** — an
  actual pan — while wobbling *less* than the old centred zooms managed at
  0.62 px/frame: worst jump **0.19 px against 0.80 px**.
- **The move means something.** `camera.move_for` picks from the shot's job and
  the scene's tone — tense close-up pushes in, establishing wide pulls back, a
  face delivering a line is locked off — instead of a round-robin over a list.
  The grade follows the story's own `visual_style` (noir, teal-orange, cold,
  warm, bleach) rather than one fixed mild S-curve.
- **A real render found the two modules spoke different tone vocabularies**, so
  "melancholic" and "joyful" both fell through to a generic move. A test now
  asserts every tone `visual_style.TONE_HINTS` can emit has camera moves, and a
  synonym table catches what a writing model invents.
- **Real motion is a provider chain** (`video` and `lipsync` roles) instead of
  an `if os.getenv(...)` ladder inside the agent — which is what the provider
  layer exists to replace. Free providers come first on purpose: a per-clip
  charge does not get to be the default just because it is better. **Veo 3.1**
  is reachable (the Gemini key already present can call it) but is billed per
  second and outside the free tier, so it sits below the free options and
  needs `VIDEO_BUDGET_OK` as well as the key. **Saad ruled it out on cost;
  it has never been called.**

Three robustness fixes found while verifying:
- The worker pool is sized from the *preferred* provider's concurrency, so when
  that one started failing and the chain fell through to an endpoint that takes
  one request at a time, every worker arrived at once and the run came back
  full of placeholders. Each provider now has its own semaphore.
- When every image provider failed, the agent recorded the path it had meant to
  write; the missing file surfaced much later as ffmpeg failing to open its
  input. `_ensure_image` now guarantees a placeholder exists.
- A shot that cannot be supersampled is re-rendered at output size with a
  warning rather than losing the film, and the supersampled frame is capped at
  8.5 MP so a 1080p project cannot exhaust memory.

## M7 results (2026-10-01)

273/273 tests pass. Ship polish:
- **Continuous integration**, which there was none of. `ubuntu-latest` and
  `windows-latest`, Python 3.11 and 3.12, with ffmpeg, the Noto fonts and
  `libgl1` installed on the Linux runner. The suite is fully offline, so CI
  needs no secrets and spends no quota, and `compileall` runs first so a
  syntax error in a file no test imports still fails the build.
  **The first run found a real bug**: on a machine without the optional voice
  packages, choosing Kokoro raised `ModuleNotFoundError: No module named
  'soundfile'` because the import ran before the check that explains what to
  install. It could not show up on the dev laptop, which has them.
- **A film library in the UI.** `/api/projects/` existed and nothing showed
  it, so there was no way back to yesterday's film. Clicking one loads the
  video, its subtitle tracks and its history, or opens the storyboard if it
  was never rendered.
- **A front page that describes the product**, not the course brief. The old
  one opened with a Member 1-4 ownership table; the original team project is
  now credited in an Origins section instead of being the headline.

Worth knowing operationally: `data/state.db` runs in WAL mode, so copying that
file alone does **not** copy recent writes — a backup taken with `cp` came back
empty. Use SQLite's backup API (`sqlite3.Connection.backup`) or `VACUUM INTO`.

## M8 results (2026-10-02, in progress)

Frontend rebuilt as the "creator" design — chosen over an editorial dashboard
and an industrial terminal (all three are in `docs/mockups/`), because both
dashboards put the machine in the middle and a content creator doesn't care
that sync is frame-exact. Next.js 16 static export served by FastAPI: still one
container, one origin, no CORS. Title face: Fraunces (eight candidates in
`docs/mockups/fonts.html`).

Verified in a real browser against a copy of the real database: first-run
setup, the library with real posters, a plan streaming in live (script first,
then each frame developing in), a render, and the player — whose scene chapters
seek correctly. The render's log showed all three wide shots
**"reused the storyboard preview"**.

What building it found, all fixed and each pinned by a test that fails on the
old code:
- **Preview reuse never fired in the real path.** `plan()` still defaulted
  previews to 512x288, overriding the 1280x720 the schema said, so signatures
  never matched the render. The earlier test drew the storyboard directly at a
  matching size and passed anyway; the new one goes through `plan` and asks with
  the size `render` actually uses.
- **A scene's tone never reached the camera in a real render** — M6's claim
  was overstated. The agent only makes `establishing`, `character` and
  `lip_sync` shots, and `move_for` sent all three to fixed lists; tone was only
  consulted for a `"detail"` kind the agent never produces, which is exactly
  what M6's test checked. Now establishing shots and narration take the scene's
  mood, and tense dialogue pushes in. The new test renders a film and reads back
  the moves the agent chose.
- **Then every scene opened on the same move.** The pick was offset by scene
  number into each mood's list, and `drift` happened to sit at that position in
  all three — a scene now opens on its mood's own move.
- Scene numbers came from the model's own (zero-based) index; cards and status
  messages now count from one by position.

New backend surface for the UI: the plan streams `storyboard/script` (cards,
cast, image budget, prompt) and `storyboard/frame` events; `GET
/api/pipeline/film/{pid}` gives the player URLs instead of file paths (the old
page split Windows paths in the browser to guess them); the storyboard takes
`?engine=` so cards name the voices that will actually speak; the library has a
poster frame per film; the job status carries the prompt.

CI gained a `web` job (lockfile install, build, typecheck, export check), and
the Dockerfile is now multi-stage so the image serves the new interface — it
would otherwise have shipped the old page, since `web/out` is gitignored. The
Node stage was verified by a clean `npm ci && npm run build` from only the
committed files; the full image has not been built (Docker was not running).

**Voice edits compose.** A scene-scoped voice edit rendered that scene's lines
from throwaway copies of each voice, so the next edit to touch them — "make
everyone louder", a change to one character, "regenerate the audio" — rebuilt
them from the character's voice and the scene's change vanished; asking twice
for a different voice in a scene landed on the same one. Scenes now keep their
own voices (`AudioOutput.scene_voices`) and later edits adjust those as well,
in order: scene 2 whispering, then everyone louder, is scene 2 whispering
louder. "Regenerate the audio" used to rebuild every voice from defaults *and*
switch a Kokoro film to the chain's first engine; it now re-records in place.
The offline classifier missed "voices", "whispered", "deeper", "narrator", so
the natural phrasing of a scene voice edit did nothing; and a tone it didn't
know relabelled the voice, re-recorded identical lines and reported success —
it now fails, naming the tones it knows. Every behaviour test goes through
`EditAgent.edit` on a rendered film, and all six still fail when only the
executor is put back to the old code.

**Edit and undo in the creator interface.** The new UI ended at the player;
editing and versions existed only on `/classic/`. Under the film now: one
sentence, which first comes back as what it was understood to mean, and a
version list in the creator's own words with "Go back".

Building that found the edit path was not safe to put in front of anyone:
- **Edits ran inside the HTTP request** — no progress, no cancel, gone on an
  API restart, and on a worker's host only by luck. Edits and reverts are now
  jobs (`edit`, `revert`); the classic endpoints queue and wait so their
  contract holds.
- **Two jobs for one film could run at once** on two workers, both from the
  same version, the second silently undoing the first. The claim now skips a
  job while an older one for its project is unfinished — decided by that
  row's existence, not its lock, so it holds under `SKIP LOCKED`.
- **A failed edit left its half-applied change on disk** (a filter darkens the
  shots in place, then the cut fails) for the next edit to bake in. An edit
  now starts from, and on failure or cancel returns to, the saved version's
  files.
- **Every revert was saved claiming to be the version it went back to.**
  Restoring a version's files copied its own `state.json` along, and the
  revert's snapshot wrote it over the new record — so the player showed the
  wrong version and "regenerate this scene", which salts its seed with the
  version, could repeat an earlier image.
- **One unreadable image hung a worker forever.** ffmpeg loops a still as an
  endless input and, failing to decode it, retries instead of exiting; the
  heartbeat kept the job looking alive and a cancel never landed. Stills are
  decoded first and shots have a time limit.

Each fix has a test shown to fail without it. `DATA_DIR` now moves everything
a deployment keeps (used to verify against a throwaway instance; the
container will want it too).

**Live, an edit did nothing and said "Change made".** With real keys, Groq
classified "make the voices in scene 2 whispered" as intent `whisper_voices`
with `{"voice_type": "whisper"}` — the prompt asked for "a short snake_case
action name" and free-form parameters, so the model invented both. The
planner fell back to re-recording, the executor ignored the unknown key, and
the same lines were recorded in the same voice. Gemini never got a turn: the
open `parameters` dict became `additionalProperties`, which its developer API
rejects. Now the model fills a closed form (`agents/edit_agent/vocabulary.py`)
whose intents and values are enumerated from what the editor can actually do;
an invented name fails validation and is retried with the error, a request
nothing matches is "unclear", and the planner refuses unclear or incomplete
edits with what to say instead ("which tone? whispered, soft, …"). Measured
against both live models: Gemini now accepts the schema; Groq mapped all five
real phrasings, including "the recipe scene should feel darker" to scene 2 by
its title, and answered "unclear" for "make it better".

Verified end to end in a browser against a throwaway instance (`DATA_DIR`),
on a film made through the interface (Groq script after Gemini 503'd,
Cloudflare FLUX images, Kokoro voices, 107 s render): "make the voices in scene 2
whispered" re-recorded only scene 2 at 140 wpm (its lines 3.8 s → 5.1 s, the
chapters after it moved), "make everyone louder" left scene 2 whispering at
0.91 and everyone else at 1.3, "Go back" to the first cut produced v6 —
recording that it is v6 — with the film byte-identical to v2, and "make it
better" was refused with nothing changed.

Still in M8: GitHub OAuth (OAuth app registered, keys in `.env`).

## Known issues / next milestones

- The Pollinations key now has a small pollen budget, so the keyed endpoint
  serves the requested model at full size. The keyless endpoint it falls back
  to when the budget runs out 402s intermittently and returns a weaker model
  (`sana`, ~1024x576), so a run can degrade mid-film without failing.
- All three free image providers can be exhausted at once — Cloudflare's 10,000
  neurons/day, Pollinations keyed and keyless — and then a render completes
  with placeholder images rather than failing. That is the designed behaviour,
  but it does mean a demo render has to wait for the daily reset.
- No real-motion provider has been exercised end to end: every one costs money
  per clip. The chain, the Veo adapter and the fallback to camera moves are
  unit-tested; the network call is not.
- Cloudflare's safety filter occasionally rejects an innocuous scene prompt as NSFW.
- Voices are still edge-tts only; Kokoro / Chatterbox voices and ACE-Step music need a
  ~2-3 GB torch install, so they stay optional (deferred again from M3).
- Snapshots copy every file per version — storage grows quickly (content-addressed storage would fix it).
- The S3/R2 asset backend is verified against both a local S3 server
  (`python -m moto.server`) and **real Cloudflare R2**, bucket
  `multi-agent-storygen`: 31 assets / 17 MB up, film back byte-identical.
  **Local disk is the default and stays the default** — the API and the
  worker share a disk here, so a bucket would only add latency and one-hour
  presigned URLs. Only `STORAGE_URL` switches it; having R2 credentials in the
  environment does not, and a test pins that down.
- Two workers on one laptop contend for CPU; concurrency helps across hosts.
- No password reset by email (no mail service at $0) — `python main.py users
  passwd <email>` is the recovery path, which suits a self-hosted deployment.
- No OAuth or two-factor; sessions are not yet listed and revokable per device
  in the UI, though the API and the data model both support it.
- An edit that rewrites the script starts its voices fresh (scene 2 is a
  different scene afterwards); only the voice engine carries over.
- Versions saved by a revert before M8 carry the wrong `version` inside their
  state (the number they went back to). History order is unaffected and the
  next save numbers correctly; nothing rewrites the old records.
