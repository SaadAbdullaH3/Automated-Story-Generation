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
python main.py worker                      # a worker process on its own (WORKER_INLINE=0 for the API)
python main.py jobs [--status failed]      # the queue: what ran, what broke
docker compose up --build --scale worker=3 # API + workers + Postgres
python main.py edit <project_id>           # interactive edit REPL
python main.py list | history <project_id>
```

Outputs go to `data/outputs/<project_id>/`, version snapshots to
`data/state_versions/`, and the version log to `data/state.db` (all gitignored).

## Architecture map

| Area | Path | Notes |
|---|---|---|
| Cross-phase contract | `shared/schemas/` | Pydantic models. `PipelineState` is the object passed between phases and versioned. Change these carefully: every phase depends on them. |
| Orchestrator | `agents/orchestrator/` | Plain-Python graph (not LangGraph) running phase1 → phase2 → phase3, emitting `ProgressEvent`s. |
| Phase 1 | `agents/story_agent/` | LLM structured output → `ScriptOutput`; `planner.py` is the deterministic template used when the LLM is `mock`; `appearance.py` locks each character's look + image seed. |
| Storyboard | `shared/schemas/storyboard.py` + orchestrator `plan`/`update_storyboard`/`render` | The cheap draft (script + one preview image per scene) that is reviewed and edited before the expensive render. `PipelineState.stage` is draft → storyboard → rendered. |
| Timeline | `shared/timeline.py` | **Single source of truth for timing.** Audio places lines on it, video cuts on its boundaries (absolute ms → frames), subtitles read it. Never time video or audio independently. |
| Phase 2 | `agents/audio_agent/` | `render_line` (TTS) → `retime` (timeline) → `remix` (per-scene BGM + master with lines placed at `start_ms`). Edits reuse these. |
| Phase 3 | `agents/video_agent/` | `run`: portraits + 3-image shot bank per scene. `compose`: plan shots from the timeline → render changed scenes only (`plan_signature`) → scene crossfades → master mux → speed → soft-sub tracks. Edits call `compose`. |
| Phase 5 | `agents/edit_agent/` | `intent_classifier.py` (LLM or regex; matches character names) → `planner.py` (steps) → `executor.py` (reuses Phase 2/3 primitives) → snapshot. |
| Versioning | `state_manager/` | Append-only SQLite log + asset copies per version; `referenced_files(state)` collects every file the state points to. Revert creates a new version. |
| Languages | `shared/languages.py` | Supported subtitle languages (ISO codes + MyMemory codes). UI dropdown is served from here. |
| Tool layer | `mcp/` | Internal tool registry (**not** the MCP protocol). Tools register on `import mcp.tools`; agents call `ToolExecutor().execute("audio.tts", ...)`. Each tool tries providers in order and falls back. |
| Model settings | `config/providers.yaml` + `shared/providers.py` | **Which model serves each role** (story, edit_intent, translate, image, tts, music). Agents never name a model: they call `providers.chain(role)` and use the first available, falling through on failure. `concurrency:` per provider drives `shared/utils/parallel.run_jobs`. |
| LLM client | `mcp/tools/llm_tools/llm_client.py` | `get_llm_client(role)` walks that role's chain: Gemini (google-genai, native JSON schema), Groq / OpenRouter / Ollama / OpenAI (all via the openai client), Anthropic, then `mock` = use the offline fallback. |
| Jobs | `jobs/` | **Runs are rows, not closures.** `queue.py` (enqueue/claim/heartbeat/cancel, one atomic UPDATE per claim) and `worker.py` (claims jobs, runs the orchestrator, publishes assets). Cancellation lands between pipeline steps, never mid-ffmpeg. |
| Database | `shared/db.py` | One database for the version log and the queue. SQLite (WAL) by default, Postgres via `DATABASE_URL`, same SQL through SQLAlchemy Core. |
| Assets | `shared/assets.py` | The only place a file path becomes a URL. Local disk by default; `STORAGE_URL=s3://bucket` publishes to any S3-compatible bucket (R2) so the worker and the API need not share a filesystem. |
| Voices | `shared/voices.py` + `backend/routes/voices.py` | Which engines work on this machine and why not, their voices, and a cached one-line sample so a voice can be heard before a render. |
| Fonts | `shared/fonts.py` | Picks an installed font with the script's glyphs (`Segoe UI` on Windows, Noto in the container). A missing font draws nothing rather than failing. |
| Backend | `backend/` | FastAPI routes, DB-backed progress (`services/progress.py`), WebSocket at `/ws/progress/{pid}`, `/assets` serves `data/outputs`. |
| Frontend | `frontend/src/` | Vanilla HTML/CSS/JS, no build step, served by FastAPI. |

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
  S3-compatible bucket after a job succeeds.
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
- Using R2 needs three things only the account owner can do: enable R2 (which
  asks for a payment method even for the free 10 GB), create a bucket, and mint
  an **R2 API token** — that is a separate S3 access key id + secret, not the
  Workers AI bearer token. Then:
  `STORAGE_URL=s3://<bucket>`, `S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com`,
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from that token, and
  `python -m pytest -k real_s3` with `TEST_S3_ENDPOINT` set proves it in one run.
- Testing against a real S3 server found one bug: boto3 defaults to
  virtual-host addressing (`bucket.host`), which needs DNS per bucket and fails
  on R2/MinIO-style endpoints. `shared/assets.py` now uses path addressing
  whenever `S3_ENDPOINT_URL` is set.

## Known issues / next milestones

- The Pollinations key has a 0 pollen budget, so the keyed endpoint 402s and the keyless
  (weaker `sana`, ~1024x576) one serves as the backup behind Cloudflare.
- Cloudflare's safety filter occasionally rejects an innocuous scene prompt as NSFW.
- Voices are still edge-tts only; Kokoro / Chatterbox voices and ACE-Step music need a
  ~2-3 GB torch install, so they stay optional (deferred again from M3).
- Storyboard previews are drawn at 512x288 and thrown away at render time; reusing them as
  the wide shot would save one image per scene.
- Snapshots copy every file per version — storage grows quickly (content-addressed storage would fix it).
- The S3/R2 asset backend has been verified against a real S3 server
  (`python -m moto.server`), not just a fake client: a finished film's 32
  assets (19.3 MB) uploaded through the worker's publish step and the film came
  back byte-identical over a presigned URL. It has **not** run against
  Cloudflare R2 itself — see below.
- Two workers on one laptop contend for CPU; concurrency helps across hosts.
- No authentication: every project is visible to anyone who can reach the API.
- Scene-scoped voice edits apply to that scene only until a later global audio edit re-renders it.
