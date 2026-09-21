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
python -m pytest -q                        # full suite (~1 min, offline: mock LLM, no Pollinations)
python -m pytest tests/unit/test_phase5_edit.py -q
python main.py "prompt" --duration 30 --scenes 4   # CLI end-to-end run
python main.py providers                   # which providers are detected
python main.py serve --reload              # web UI on http://localhost:8000
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
| Phase 1 | `agents/story_agent/` | LLM structured output → `ScriptOutput`; `planner.py` is the deterministic template used when the LLM is `mock`. |
| Timeline | `shared/timeline.py` | **Single source of truth for timing.** Audio places lines on it, video cuts on its boundaries (absolute ms → frames), subtitles read it. Never time video or audio independently. |
| Phase 2 | `agents/audio_agent/` | `render_line` (TTS) → `retime` (timeline) → `remix` (per-scene BGM + master with lines placed at `start_ms`). Edits reuse these. |
| Phase 3 | `agents/video_agent/` | `run`: portraits + 3-image shot bank per scene. `compose`: plan shots from the timeline → render changed scenes only (`plan_signature`) → scene crossfades → master mux → speed → soft-sub tracks. Edits call `compose`. |
| Phase 5 | `agents/edit_agent/` | `intent_classifier.py` (LLM or regex; matches character names) → `planner.py` (steps) → `executor.py` (reuses Phase 2/3 primitives) → snapshot. |
| Versioning | `state_manager/` | Append-only SQLite log + asset copies per version; `referenced_files(state)` collects every file the state points to. Revert creates a new version. |
| Languages | `shared/languages.py` | Supported subtitle languages (ISO codes + MyMemory codes). UI dropdown is served from here. |
| Tool layer | `mcp/` | Internal tool registry (**not** the MCP protocol). Tools register on `import mcp.tools`; agents call `ToolExecutor().execute("audio.tts", ...)`. Each tool tries providers in order and falls back. |
| Model settings | `config/providers.yaml` + `shared/providers.py` | **Which model serves each role** (story, edit_intent, translate, image, tts, music). Agents never name a model: they call `providers.chain(role)` and use the first available, falling through on failure. `concurrency:` per provider drives `shared/utils/parallel.run_jobs`. |
| LLM client | `mcp/tools/llm_tools/llm_client.py` | `get_llm_client(role)` walks that role's chain: Gemini (google-genai, native JSON schema), Groq / OpenRouter / Ollama / OpenAI (all via the openai client), Anthropic, then `mock` = use the offline fallback. |
| Backend | `backend/` | FastAPI routes, in-memory run registry, WebSocket progress at `/ws/progress/{pid}`, `/assets` serves `data/outputs`. |
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

Measured: the keyless Pollinations endpoint 429s on parallel requests (1 per IP confirmed
by experiment), so parallelism only pays off with Cloudflare or a local model.
**Not yet verified against live Gemini/Groq/Cloudflare APIs** — no keys on this machine;
adapters are covered by mocked tests.

## Known issues / next milestones

- Live API verification for Gemini / Groq / Cloudflare is pending free keys from the owner.
- Without any image key, images still come from the legacy Pollinations endpoint (`sana`,
  ~1024x576, ~40 s each, one at a time). Cloudflare or local Z-Image fixes both.
- Voices are still edge-tts only; Kokoro / Chatterbox and ACE-Step music are M3 candidates
  (both need a torch install, so they stay optional).
- The web player can't show MP4 soft subtitles; the UI needs `<track>` WebVTT files (M4 frontend).
- Snapshots copy every file per version — storage grows quickly (M4: content-addressed storage).
- Scene-scoped voice edits apply to that scene only until a later global audio edit re-renders it.
