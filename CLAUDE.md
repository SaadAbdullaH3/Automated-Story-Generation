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
| Phase 2 | `agents/audio_agent/` | Per-line TTS, per-scene BGM, master mix, `timing_manifest.json`. |
| Phase 3 | `agents/video_agent/` | Portraits + 3-image shot bank per scene → per-line shots (`animator.py`) → per-scene crossfade → final compose + soft-sub tracks. |
| Phase 5 | `agents/edit_agent/` | `intent_classifier.py` (LLM or regex) → `planner.py` (steps) → `executor.py` (re-runs) → snapshot. |
| Versioning | `state_manager/` | Append-only SQLite log + full asset copies per version; revert creates a new version. |
| Tool layer | `mcp/` | Internal tool registry (**not** the MCP protocol). Tools register on `import mcp.tools`; agents call `ToolExecutor().execute("audio.tts", ...)`. Each tool tries providers in order and falls back. |
| LLM client | `mcp/tools/llm_tools/llm_client.py` | Gemini → OpenAI → Anthropic → mock. `LLM_PROVIDER` forces one. |
| Backend | `backend/` | FastAPI routes, in-memory run registry, WebSocket progress at `/ws/progress/{pid}`, `/assets` serves `data/outputs`. |
| Frontend | `frontend/src/` | Vanilla HTML/CSS/JS, no build step, served by FastAPI. |

## Conventions

- Tools return `ToolResult(success, data, error, metadata)`; `safe_run` turns exceptions into failures.
- New tools subclass `mcp.base_tool.BaseTool` and are registered in the category `__init__.py`.
- Every phase writes JSON artifacts to disk and records paths in `state.phaseN.artifact_paths`.
  Snapshots only copy paths listed there.
- Tests force `LLM_PROVIDER=mock` and `POLLINATIONS_DISABLE=1` (see `tests/conftest.py`)
  and monkeypatch `shared.constants` paths into `tmp_path`.

## Known issues (tracked for M1)

- Audio/video/subtitle timelines diverge: the master audio is dialogue concatenated with no gaps,
  while the video adds establishing pre-roll and crossfades, and the manifest adds scene padding.
- Edit re-runs of TTS drop the character's `voice_id`, and edge-tts ignores `rate` (so "whispered" is a no-op).
- Recompose after any edit burns in English subs, replacing the multi-language soft-sub tracks.
- Edits don't update `artifact_paths`, so new files aren't included in snapshots.
- Filters `noir/cinematic/dreamy/pastel/anime` are classified but have no implementation.
- `run_registry.push_event` calls `asyncio.Queue.put_nowait` from a worker thread (not thread-safe).
- The legacy `image.pollinations.ai` endpoint silently serves the `sana` model at 1024x576 instead
  of FLUX; the new `gen.pollinations.ai` requires an API key.
- The UI offers a Japanese subtitle option, but only English/French/Spanish/German/Urdu tracks are generated.
- `backend/routes/projects.py` splits paths on `/` only (breaks on Windows).
- Gemini uses the deprecated `google-generativeai` SDK and the retired `gemini-1.5-flash` default.
