# Agentic AI — AI-Powered Animated Video Generation System

> *From a single natural-language prompt to a polished short animated film, end-to-end, with LLM agents.*
>
> National University of Computer & Emerging Sciences — **Agentic AI Semester Project, Spring 2026**

This repository implements the full five-phase agentic pipeline described in
the project brief:

| Phase | Owner | Module | Output |
|-------|-------|--------|--------|
| 1. Story, Script & Character | Member 1 | `agents/story_agent/` | `story.json`, `characters.json`, `script.json`, hand-offs |
| 2. Audio Generation | Member 2 | `agents/audio_agent/` | per-line TTS files + `timing_manifest.json` + master track |
| 3. Video Composition | Member 3 | `agents/video_agent/` | per-scene images, animated clips, `final_output.mp4` |
| 4. Web Interface | Member 4 | `backend/` + `frontend/` | FastAPI + WebSocket + single-page UI |
| 5. Intelligent Edit & Undo | Member 4 (lead) | `agents/edit_agent/` | NL edit → versioned re-runs + revert |

The system is designed so **every phase is independently testable** and the
**entire pipeline runs offline with zero API keys** (template-based fallbacks
for the LLM, silent/free TTS, free image generation, ffmpeg-only video).

---

## Table of contents

1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Shared JSON schema](#shared-json-schema)
4. [Phase-by-phase guide](#phase-by-phase-guide)
5. [Editing agent (Phase 5)](#editing-agent-phase-5)
6. [Running the web UI](#running-the-web-ui)
7. [Testing](#testing)
8. [Configuration](#configuration)
9. [Project layout](#project-layout)
10. [Division of work](#division-of-work)

---

## Quick start

### 1. Install

```bash
# Python 3.10+ recommended (tested on 3.11 and 3.12)
git clone <repo-url>
cd Automated-Story-Generation
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt     # macOS / Linux
```

You **also need ffmpeg on your PATH** (`ffmpeg -version` should work). Audio &
video composition uses it. On Windows: `winget install Gyan.FFmpeg`.

Local Stable Diffusion (`LOCAL_SD=1`) is optional and pulls in PyTorch, so it
has its own file: see [`requirements-local-sd.txt`](requirements-local-sd.txt).

### 2. (Optional) configure providers

The pipeline runs in fully-offline **mock mode** out of the box — every phase
has a template/free fallback. To upgrade to real models, copy `.env.example`
to `.env` and uncomment one of the keys:

```bash
cp .env.example .env
# then edit .env and add e.g.
# GEMINI_API_KEY=AIza...
```

Provider precedence: **Gemini → OpenAI → Anthropic → mock template**.

### 3. Run end-to-end via CLI

```bash
python main.py "A young astronaut discovers a hidden ocean on Mars"
```

You'll see per-phase progress and a final `data/outputs/<project_id>/final_output.mp4`.

### 4. Run the web UI

```bash
python main.py serve
# open http://localhost:8000
```

The UI lets you enter a prompt, watch live phase progress over WebSocket, then
issue free-text edits and revert to any version.

### 5. (Optional) unlock real video + lip sync

The default pipeline produces a watchable multi-shot composition with
animated stills. To get **real** Stable Video Diffusion clips and **real**
SadTalker lip-synced talking heads, follow the 2-minute setup in
[`docs/REAL_VIDEO_SETUP.md`](docs/REAL_VIDEO_SETUP.md). TL;DR:

```bash
# 1. Get a free fal.ai key at https://fal.ai/dashboard/keys
echo "FAL_KEY=your-key-here" >> .env

# 2. Verify
python main.py providers
# -> Video tier : PREMIUM (real motion + real lip sync)

# 3. Re-run
python main.py "your prompt"
```

Cost: free trial covers 3-6 full pipelines; afterward ~$0.15-0.30 per render.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            FRONTEND  (vanilla JS SPA)                    │
│       prompt input · live progress · video preview · edit chat · history │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ HTTP + WebSocket
┌──────────────────────────────▼───────────────────────────────────────────┐
│                         BACKEND  (FastAPI)                               │
│   /api/pipeline   /api/edit   /api/history   /api/projects   /ws/...     │
└──────┬─────────────┬─────────────┬─────────────┬─────────────┬───────────┘
       │             │             │             │             │
   ┌───▼───┐    ┌───▼───┐    ┌───▼───┐    ┌────▼────┐    ┌───▼────┐
   │Phase 1│ -> │Phase 2│ -> │Phase 3│    │Phase 5  │    │Versions│
   │Story  │    │Audio  │    │Video  │    │Edit Ag. │    │Undo    │
   └───┬───┘    └───┬───┘    └───┬───┘    └────┬────┘    └────────┘
       │            │            │             │
       └─────────────────┬───────┴─────────────┘
                         │
                  ┌──────▼───────┐
                  │  MCP layer   │  ← LLM, TTS, BGM, image-gen, ffmpeg, file/state tools
                  └──────────────┘
```

The shared **`PipelineState`** Pydantic object is what flows between phases —
it carries `script`, `audio`, `video` blocks plus per-phase status. Each
phase's output is also serialized to disk as plain JSON, so phases can be
**run independently** by loading the previous phase's hand-off file.

---

## Shared JSON schema

All schemas live in [`shared/schemas/`](shared/schemas) and are validated by Pydantic.
Highlights:

```python
class ScriptOutput(BaseModel):       # Phase 1 output
    story: StoryOutput               # title, logline, synopsis, genre, themes
    characters: CharacterRoster      # name, role, voice_style, voice_gender, ...
    scenes: list[Scene]              # scene_id, visual_prompt, dialogue[], music_mood, ...

class TimingManifest(BaseModel):     # Phase 2 contract
    total_duration_ms: int
    segments: list[AudioSegment]     # {scene_id, line_id, file_path, start_ms, end_ms}

class VideoOutput(BaseModel):        # Phase 3 output
    frames: list[SceneFrame]
    final_video_path: str
    has_subtitles: bool

class EditIntent(BaseModel):         # Phase 5 classifier output
    intent: str
    target: Literal["audio", "video_frame", "video", "script"]
    scope: str                       # "global" | "scene:scene_1" | "character:char_narrator"
    parameters: dict
    confidence: float

class PipelineState(BaseModel):      # The "central state object" passed forward
    project_id: str
    version: int
    user_prompt: str
    script: ScriptOutput | None
    audio: AudioOutput | None
    video: VideoOutput | None
    phase1, phase2, phase3: PhaseState  # status / errors / artifact paths
```

A finished run produces these JSON artifacts in `data/outputs/<project_id>/`:

- `story.json`, `characters.json`, `script.json`
- `phase2_audio_handoff.json`, `phase3_video_handoff.json`
- `timing_manifest.json`
- `audio_summary.json`, `video_summary.json`, `summary.json`
- `final_output.mp4` (picture + master audio), and `final_output_multilang.mp4`
  (the same film with switchable subtitle tracks, when subtitles are on)

---

## Phase-by-phase guide

### Phase 1 — Story, Script & Character (`agents/story_agent/`)

* **Input** — `state.user_prompt`
* **LLM role** — expand prompt → narrative → scenes + dialogue → character roster
* **Tools** — `mcp.tools.llm_tools.LLMClient` (Gemini/OpenAI/Claude/mock)
* **Output** — `ScriptOutput` (validated)

The `StoryAgent` follows a LangGraph-style 3-stage flow:
*Story agent → Character agent → Script agent* with retries, character
consistency check, and duration estimation. We implement the graph in plain
Python (`agents/orchestrator/graph.py`) so no LangGraph install is required;
swapping in real LangGraph is a 30-line change.

If no LLM key is configured, a deterministic four-act template (in
`planner.py`) produces a coherent script for any prompt — used by tests.

### Phase 2 — Audio Generation (`agents/audio_agent/`)

* **Input** — `state.script`
* **Tasks** — per-line TTS with character-consistent voices, the film's
  **timeline**, mood-based BGM per scene, master mix, timing manifest
* **One timeline for everything** ([`shared/timeline.py`](shared/timeline.py)) —
  each scene is `pre-roll (establishing shot) → line → gap → line … → tail`.
  The master audio places every line at its timeline position, the video cuts
  on the same boundaries, and subtitles use the same numbers, so voices,
  faces and captions stay in sync for the whole film.
* **Tools**
  * **edge-tts** (default, free, online) — Microsoft Azure Neural Voices mapped dynamically to character archetypes (e.g., `en-US-AriaNeural`, `en-US-ChristopherNeural`).
  * **gTTS** (fallback, free, online)
  * **pyttsx3** (offline fallback)
  * **ElevenLabs** (premium, if API key set)
  * **silent placeholder** (always works — used in tests)
  * Background music synthesised by ffmpeg's `lavfi` filter graph
    (mood-keyed sine layers + tremolo + fade)
* **Output** — `AudioOutput` + flat `timing_manifest.json`

### Phase 3 — Video Generation (`agents/video_agent/`)

Two-tier rendering for cinematic-feeling output **even without paid APIs**:

#### Tier 1 — multi-shot ffmpeg composition (default)

Instead of one still per scene, the agent renders a **separate sub-clip for
every dialogue line** so a 4-scene project becomes ~12-15 cuts:

* Generate a **shot bank** of 3 images per scene — wide, detail, alternate
  angle (Pollinations.ai by default)
* Generate one **portrait per character** in the cast
* Cut each scene to the audio timeline:
  - **Pre-roll** -> the wide establishing shot
  - **Narrator lines** -> rotate through the scene's shot bank
  - **Character lines** -> that character's portrait (B-roll cutaway on long lines)
* Shots **crossfade** within a scene (200 ms) and between scenes (400 ms); each
  clip is rendered a few frames longer to cover its crossfade, so the film is
  exactly as long as the audio and every cut lands on its line
* Cinematic post: vignette + film grain + mild S-curve
* Re-composition (after an edit) re-renders only scenes whose shots changed

Result: a project that previously had 4 long static shots now has 12-15 cuts
synced to the dialogue, alternating between wide and close-up just like
documentary or anime.

#### Tier 2 — real text-to-video + lip sync (opt-in)

Set `FAL_KEY` (free trial credits) or `REPLICATE_API_TOKEN` and the agent
will automatically:

* Replace establishing shots with **Stable Video Diffusion / fast-SVD** clips
  (real motion: water rippling, hair blowing, camera dolly etc.)
* Replace character close-ups with **SadTalker / sync-lipsync** clips
  (actual lip-sync to the dialogue audio)

| Provider | Text-to-video | Lip sync |
|----------|---------------|----------|
| `FAL_KEY` (recommended — free trial) | fast-SVD, fal-svd | SadTalker |
| `REPLICATE_API_TOKEN` | SVD, zeroscope | Wav2Lip, SadTalker |
| `HF_TOKEN` | DAMO text-to-video-ms | — |
| (none) | ffmpeg ken-burns | heuristic mouth-zoom |

#### Subtitles & multi-language support
* Subtitles are embedded as **switchable soft tracks** (pick the language in
  your player): English + the language chosen in the UI/CLI, plus any listed in
  `SUBTITLE_EXTRA_LANGUAGES`. Supported languages live in
  [`shared/languages.py`](shared/languages.py) (Urdu, Hindi, Arabic, French,
  Spanish, German, Japanese, Chinese, ...).
* Translation uses the configured LLM, falling back to MyMemory (free, no key).
  **If translation fails, that language is skipped** — an English track is never
  shipped under a foreign label. Translations are cached until the dialogue changes.

* **Output** — `VideoOutput` with multi-shot `frames`, `portraits`,
  `final_output.mp4` / `final_output_multilang.mp4`, plus per-shot MP4s under
  `data/outputs/<pid>/video/shots/`

### Phase 4 — Web Interface (`backend/` + `frontend/`)

* **Backend** — FastAPI + WebSocket
* **Frontend** — vanilla HTML/CSS/JS single-page (no build step)
* **Endpoints**
  ```
  POST /api/pipeline/run              start a new pipeline
  POST /api/pipeline/rerun            re-run phase 1/2/3
  GET  /api/pipeline/state/<pid>      current full state
  GET  /api/pipeline/status/<pid>     lightweight status snapshot
  GET  /api/pipeline/languages        supported subtitle languages
  POST /api/edit/classify             classify intent only
  POST /api/edit/apply                apply an edit (versioned)
  GET  /api/edit/log/<pid>            edit history
  GET  /api/history/<pid>             version history
  POST /api/history/<pid>/revert/<n>  revert to version n
  GET  /api/projects/                 list known projects
  WS   /ws/progress/<pid>             live progress events
  GET  /assets/<pid>/<file>           static asset server
  ```

### Phase 5 — Intelligent Edit & Undo (`agents/edit_agent/`)

The intelligent layer that matters most. See [next section](#editing-agent-phase-5).

---

## Editing agent (Phase 5)

```
user types → IntentClassifier → planner.plan → EditExecutor.execute →
                                                StateManager.snapshot (v++)
```

### Intent classification

A LangGraph-style classifier with two paths:

1. **LLM-backed** structured output (when a provider is configured) using
   Pydantic-validated JSON.
2. **Keyword + regex fallback** that runs offline. The fallback is what the
   18-query test suite exercises (see `tests/unit/test_phase5_edit.py`).

Detected `target` is always one of `audio`, `video_frame`, `video`, `script`.

### Examples

| User query | Detected target | Action taken |
|------------|------------------|--------------|
| "Change voice tone to whispered" | `audio` | re-run TTS w/ tone=whispered + remix |
| "make scene 2 darker" | `video_frame` | apply `darker` filter to scene 2 + recompose |
| "add background music tense" | `audio` | regenerate BGM at mood=tense + remix |
| "remove the subtitles" | `video` | recompose the film without subtitle tracks |
| "change character design" | `video_frame` | regenerate character portraits + recompose |
| "speed up 1.5x" / "slow down" | `video` | persistent speed factor (`setpts` + `atempo`), subtitles retimed |
| "regenerate the script" | `script` | re-run phase 1, cascade to 2 & 3 |
| "apply vintage filter" | `video_frame` | apply Pillow vintage filter chain |

### State versioning & undo

Every successful pipeline run **and** every successful edit creates an
**append-only** snapshot:

* SQLite log of versions in `data/state.db` (`versions`, `edit_log` tables)
* Asset copies in `data/state_versions/<project_id>/v<n>/`
* `StateManager.revert(version)` restores both the JSON state and the assets,
  itself recording a new version that documents the revert (so history is
  always linear and the original edit is **never lost**).

### Filters available (Pillow / OpenCV-style)

`brightness contrast saturation sharpness grayscale sepia blur darker brighter warm cool vintage invert`

Style presets that chain filters: `cinematic noir dreamy anime pastel vintage cold_thriller`.
Scene-scoped filters ("make scene 2 darker") change only that scene, including
per-scene copies of its characters' portraits. Unknown filter names fail the
edit with a clear error instead of silently doing nothing.

---

## Running the web UI

```bash
python main.py serve --reload
```

Then visit `http://localhost:8000`. You get:

* a prompt box with knobs (duration, scenes, BGM, subtitles)
* live phase-progress bars updated by WebSocket
* a video preview pane with download link
* a free-text **Edit Agent** input with chip suggestions
* a **Version history** panel with one-click revert

The frontend is intentionally vanilla JS / CSS so it serves directly from
FastAPI with **no build step** — just `python main.py serve`.

---

## Testing

```bash
python -m pytest -q
```

The test suite covers:

* **Phase 1** — template-script generator, genre detection, agent run + JSON
  artifact validation, character consistency
* **Phase 2** — silent TTS path, BGM tool, audio merger, full audio agent
  end-to-end with monkey-patched silent TTS
* **Phase 3** — PIL image fallback, image-to-clip, video compose,
  full video agent run
* **Phase 4** — FastAPI smoke tests via `TestClient` (health, index, classify
  endpoint, history 404)
* **Phase 5** — **18 edit-query types** classified correctly (well past the
  spec's 10-query minimum), planner cascades, end-to-end edit + revert cycle
* **State manager** — version increments, asset persistence + restore, edit log
* **Integration** — full prompt-to-MP4 pipeline in mock/silent mode (≈8 s)

Current results: **87 / 87 passing**.

```
$ python -m pytest -q
87 passed in 62s
```

---

## Configuration

All knobs live in `.env`. Everything is optional.

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | force one of `gemini` / `openai` / `anthropic` / `mock` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Google Gemini (free tier exists) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic Claude |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | premium TTS |
| `POLLINATIONS_API_KEY` / `POLLINATIONS_MODEL` | free Pollinations account key -> real models (default `tongyi-mai/z-image-turbo`); without it the legacy endpoint serves a weaker model at reduced size |
| `POLLINATIONS_DISABLE` | set to `1` to skip the free image-gen API |
| `SUBTITLE_EXTRA_LANGUAGES` | extra subtitle tracks, e.g. `Urdu,French` |
| `MYMEMORY_EMAIL` | raises the free MyMemory translation quota (~5k -> ~50k chars/day) |
| `SD_API_URL` | Automatic1111 / ComfyUI URL for local Stable Diffusion |

Mock mode is the default. The CI / unit-tests run that way to stay deterministic.

---

## Project layout

```
Agentic Project/
├── main.py                  # CLI entry point: run / serve / edit / history / list
├── requirements.txt
├── .env.example
│
├── shared/                  # Cross-phase contracts
│   ├── schemas/             #   Pydantic models (story, audio, video, edit, pipeline)
│   ├── constants/           #   paths, default sizes, phase names
│   └── utils/               #   ids, files, logging
│
├── mcp/                     # Tool abstraction layer
│   ├── base_tool.py
│   ├── tool_registry.py     #   singleton registry; tools register on import
│   ├── tool_executor.py
│   └── tools/
│       ├── llm_tools/       #   text_generate, json_structure, llm_client
│       ├── audio_tools/     #   tts, bgm, audio merger
│       ├── vision_tools/    #   image gen, image edit (filters), style transfer
│       ├── video_tools/     #   ffmpeg ops, image-to-clip, compositor, subtitles
│       └── system_tools/    #   file ops, state ops, structured logger
│
├── agents/                  # One module per phase
│   ├── orchestrator/        #   pipeline graph + workflow + run context
│   ├── story_agent/         #   Phase 1
│   ├── audio_agent/         #   Phase 2
│   ├── video_agent/         #   Phase 3
│   └── edit_agent/          #   Phase 5: classifier, planner, executor, agent
│
├── backend/                 # FastAPI app
│   ├── app.py
│   ├── routes/              #   pipeline, edit, history, projects
│   ├── services/            #   pipeline_service, run_registry
│   └── websocket/           #   progress.py
│
├── frontend/                # Vanilla SPA — no build step
│   └── src/                 #   index.html, styles.css, app.js
│
├── state_manager/           # Append-only versioning + revert
│   ├── state_manager.py
│   ├── snapshot.py          #   asset copy / restore
│   ├── storage.py           #   SQLite layer
│   └── history.py
│
├── tests/
│   ├── unit/                #   per-phase + state manager
│   └── integration/         #   end-to-end pipeline
│
├── docs/                    # Project report scaffold
│   └── REPORT.md
│
└── data/                    # Generated at runtime — gitignored
    ├── outputs/<pid>/       #   per-project artifacts (JSON, audio, frames, MP4)
    ├── state_versions/      #   snapshot copies for revert
    └── state.db             #   SQLite version log
```

---

## Division of work

| Member | Primary phase | Files owned |
|--------|---------------|-------------|
| **Member 1** | Phase 1 — Story & Script | `agents/story_agent/`, `mcp/tools/llm_tools/`, `shared/schemas/story.py` |
| **Member 2** | Phase 2 — Audio | `agents/audio_agent/`, `mcp/tools/audio_tools/`, `shared/schemas/audio.py` |
| **Member 3** | Phase 3 — Video | `agents/video_agent/`, `mcp/tools/vision_tools/`, `mcp/tools/video_tools/`, `shared/schemas/video.py` |
| **Member 4** | Phase 4 — Web App **+** Phase 5 — Edit/Undo | `backend/`, `frontend/`, `agents/edit_agent/`, `state_manager/`, `shared/schemas/edit.py` |

All members jointly own:

1. The shared JSON schema (`shared/schemas/`) — finalised in week 1.
2. The integration tests (`tests/integration/`).
3. The `agents/orchestrator/` graph and `PipelineState` contract.
4. The final report and presentation.

---

## License & attribution

Implemented for the FAST-NUCES *Agentic AI* course, Spring 2026. Free & open
to share. External libs: see `requirements.txt`.

> *"The goal is a system you are genuinely proud to demo."* — assignment brief
