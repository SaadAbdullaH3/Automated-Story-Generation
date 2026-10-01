# Agentic Video Generator

**One sentence in. A finished short film out — script, voices, pictures,
music, subtitles and cuts.**

[![tests](https://github.com/SaadAbdullaH3/Automated-Story-Generation/actions/workflows/tests.yml/badge.svg)](https://github.com/SaadAbdullaH3/Automated-Story-Generation/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![tests](https://img.shields.io/badge/tests-274%20passing-brightgreen)
![cost](https://img.shields.io/badge/running%20cost-%240-brightgreen)

```bash
python main.py "A clockmaker in a flooded city repairs the hours people lose"
```

Two and a half minutes later there is a 24-second film: a three-scene script
with named characters, each line spoken in its own voice, images in a look the
story chose for itself, a camera move per shot, music ducked under the
dialogue, and burned-in subtitles in any of 14 languages.

Then you can talk to it:

```
edit> make scene 2 darker and slow Mira's voice down
edit> actually, revert that
```

Every change is versioned, and undo restores the audio and the video, not just
the text.

### What makes it more than a wrapper

- **It runs at zero cost.** Every model has a free tier or an offline
  fallback, and the chain degrades visibly rather than failing: a dead image
  provider drops to the next one and says so in the log.
- **Nothing is hard-coded.** Which model writes the script, speaks the lines,
  draws the frames or animates them is one YAML file, not an `if` in an agent.
- **One timeline.** Audio places lines on it, video cuts on its boundaries,
  subtitles read from it. The finished film is frame-exact against it — not
  approximately, exactly.
- **It is a service, not a script.** Runs are rows in a database executed by
  workers, so they survive a restart, can be watched from another process, and
  can be cancelled. Accounts are real: projects are private to their owner,
  down to the video file.
- **Claims here are measured.** Speaking rate calibrated against real voices;
  camera smoothness measured by phase correlation; audio ducking measured per
  frequency band. Where something is unverified, the docs say so.

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
10. [Origins](#origins)

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

Or **plan first and render once you're happy** — the script and one preview
image per scene cost seconds; the full render costs minutes:

```bash
python main.py plan "A young astronaut discovers a hidden ocean on Mars" --scenes 4
python main.py restyle <project_id> scene_2 --visual "the ocean seen through cracked ice"
python main.py render  <project_id>
```

The web UI has the same flow: **Plan storyboard**, edit any scene, **Render this film**.

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
  * **Kokoro** (open-source, offline, Apache-2.0) — top-rated free voice model,
    runs on CPU with no key and no GPU. Install with
    `pip install -r requirements-voices.txt && python scripts/get_kokoro.py`.
  * **edge-tts** (free, online, no key) — Microsoft neural voices mapped to character
    archetypes (e.g., `en-US-AriaNeural`, `en-US-ChristopherNeural`).
  * **gTTS** (fallback, free, online)
  * **pyttsx3** (offline fallback)
  * **ElevenLabs** (premium, if API key set)
  * **silent placeholder** (always works — used in tests)
  * Background music synthesised by ffmpeg's `lavfi` filter graph
    (mood-keyed sine layers + tremolo + fade)
* **Mix** — music is side-chained to the dialogue (it ducks ~6 dB while a line
  plays and returns in the gaps) and the master is normalised to -16 LUFS, so
  levels are consistent between films
* **Output** — `AudioOutput` + flat `timing_manifest.json`

### Phase 3 — Video Generation (`agents/video_agent/`)

Two-tier rendering for cinematic-feeling output **even without paid APIs**:

#### Tier 1 — multi-shot ffmpeg composition (default)

Instead of one still per scene, the agent renders a **separate sub-clip for
every dialogue line** so a 4-scene project becomes ~12-15 cuts:

* Every image is generated in the **film's own visual style**: the LLM proposes one
  in `visual_style`, otherwise the genre picks a preset (sci-fi concept art, horror,
  noir, painterly fantasy, anime, ...) and the scene's tone nudges the lighting.
  `VIDEO_STYLE` in `.env` overrides it. See
  [`agents/story_agent/visual_style.py`](agents/story_agent/visual_style.py).
* Generate a **shot bank** of 3 images per scene — wide, detail, alternate
  angle (Cloudflare FLUX / Pollinations by default)
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
* The chosen language is **burned into the picture** by default, because most
  players don't auto-enable subtitle tracks inside an MP4 (`--no-burn-subs` to
  opt out). Non-Latin scripts get a font that has the glyphs, and right-to-left
  languages like Urdu are shaped correctly.
* The other languages ride along as **switchable soft tracks**, and `.srt` /
  `.vtt` sidecars are written next to the video — VLC loads the `.srt`
  automatically, and the web player uses the `.vtt`.
* Languages: English + the one chosen in the UI/CLI, plus any in
  `SUBTITLE_EXTRA_LANGUAGES`. Supported list lives in
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
  POST /api/pipeline/run              queue a new pipeline run
  POST /api/pipeline/rerun            re-run phase 1/2/3
  GET  /api/pipeline/state/<pid>      current full state
  GET  /api/pipeline/status/<pid>     lightweight status snapshot
  GET  /api/pipeline/languages        supported subtitle languages
  GET  /api/jobs/                     the queue (filter by project or status)
  GET  /api/jobs/<id>                 one job: status, attempts, error
  POST /api/jobs/<id>/cancel          stop a run
  GET  /api/voices/                   engines available here, and their voices
  POST /api/voices/preview            render a one-line sample to listen to
  GET  /health  ·  GET /ready         liveness, and can it reach the database
  POST /api/edit/classify             classify intent only
  POST /api/edit/apply                apply an edit (versioned)
  GET  /api/edit/log/<pid>            edit history
  GET  /api/history/<pid>             version history
  POST /api/history/<pid>/revert/<n>  revert to version n
  GET  /api/projects/                 list known projects
  WS   /ws/progress/<pid>             live progress events
  GET  /assets/<pid>/<file>           static asset server
  ```

### Runs are jobs, not requests

Starting a run writes a row and returns immediately; a worker claims it and
does the work. That is what makes the run survive a restart, watchable from
another process, and stoppable:

```bash
python main.py serve            # API + a worker thread: one command, laptop-friendly
python main.py jobs             # what is queued, running, failed — and why
```

Claiming is one atomic `UPDATE`, so two workers never take the same job.
Progress events are rows too, so a browser that reconnects replays what it
missed instead of waiting on an empty socket. A worker that dies mid-render
stops heartbeating and its job goes back on the queue, bounded by
`max_attempts` so a reproducible failure can't loop forever. Cancelling lands
between pipeline steps, so nothing is killed half-way through an ffmpeg call.

To scale the API and the renderers apart, set `WORKER_INLINE=0` and run
`python main.py worker` as its own process — or use the containers:

```bash
docker compose up --build --scale worker=3   # API + 3 workers + Postgres
```

`DATABASE_URL` switches the version log and the queue from the default SQLite
file to Postgres; `STORAGE_URL=s3://bucket` publishes finished films to any
S3-compatible bucket (Cloudflare R2 is free to 10 GB) so the API and the
workers don't have to share a disk.

Both paths are tested rather than assumed. The whole suite runs against a real
Postgres server, and the object store against a real S3 one:

```bash
docker run -d --name storygen-pg -e POSTGRES_USER=storygen \
  -e POSTGRES_PASSWORD=storygen -e POSTGRES_DB=storygen -p 55432:5432 postgres:16-alpine
pip install -r requirements-postgres.txt
TEST_DATABASE_URL=postgresql+psycopg://storygen:storygen@localhost:55432/storygen \
  python -m pytest -q
```

```bash
pip install -r requirements-s3.txt "moto[server]"
python -m moto.server -p 5111 &
TEST_S3_ENDPOINT=http://127.0.0.1:5111 S3_REGION=us-east-1 \
  AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
  python -m pytest tests/unit/test_m4_deploy.py -k real_s3
```

Point `TEST_S3_ENDPOINT` at `https://<account>.r2.cloudflarestorage.com` with
real R2 credentials and the same test verifies Cloudflare R2.

### Camera moves

A still is not a shot. Each image gets a camera move chosen for what the shot
is doing — a tense close-up pushes in, an establishing wide pulls back, a face
delivering a line is locked off — and a colour grade taken from the look the
story asked for.

Pans were once removed from this project because they shivered: `zoompan`
positions its crop window at whole pixels, so a sub-pixel move per frame
rounds unevenly. The fix is to compute the move at three times the output size
and scale down, which turns a whole-pixel error upstream into a fraction of an
output pixel. Measured by phase correlation on real shot clips, the new shots
travel up to 5.13 px/frame while wobbling less than the old centred zooms did
at 0.62 px/frame — worst jump 0.19 px against 0.80 px.

`SUPERSAMPLE` tunes the factor (default 3, capped so a 1080p project cannot
exhaust memory). A shot that still fails is re-rendered at output size with a
warning rather than losing the film.

### Real motion, when you want to pay for it

The `video` role in `config/providers.yaml` picks who animates a still:

```
gemini_veo   Veo 3.1 via the Gemini API — billed per second, needs VIDEO_BUDGET_OK
fal          fal.ai, ~$1 of free trial credit
replicate    paid per second
huggingface  free tier, lower quality
ffmpeg       the camera moves above — offline, always works, costs nothing
```

The free options come first on purpose — a provider that charges per clip
does not get to be the default just because it is better — and the chain ends
on `ffmpeg`, so a render never fails for want of a paid provider. Veo needs
`VIDEO_BUDGET_OK=1` on top of the API key, deliberately: holding a Gemini key
for the free text models should not quietly start a per-second video bill.

### Accounts

Everything behind `/api` needs one. The first person to open the UI creates the
administrator account — and inherits any projects that already existed, from
CLI runs made before accounts did — after which sign-ups are closed unless
`ALLOW_SIGNUPS=1`.

Sessions are rows, not JWTs: the cookie holds a random token and the database
stores only its hash, so a stolen dump can't be replayed, signing out really
signs out, and disabling an account ends its sessions at once. Passwords are
argon2id. A wrong password and an unknown address give the identical answer,
and eight failures lock the account for fifteen minutes.

Projects are private to their owner. Asking for someone else's gets a 404
rather than a 403, because a 403 would confirm the id is real. That applies to
the films too — `/assets/...` is an authorised route, not a static mount, so a
guessed project id no longer downloads the video.

```bash
python main.py users                          # who has an account
python main.py users create me@example.com --admin
python main.py users passwd me@example.com    # the way back in
```

### Choosing a voice, and hearing it first

The voice engine comes from `config/providers.yaml`, but the UI can override it
per film. The picker lists every engine, marks the ones this machine can't use
with the reason ("run `python scripts/get_kokoro.py`"), and renders a one-line
sample on demand so a voice can be heard before committing to a full render.
If an engine fails and the tool falls back, the preview says so rather than
playing another engine's voice as the one that was chosen.

```bash
python main.py "a prompt" --voice-engine kokoro   # or edge, gtts, pyttsx3
```

### Storyboard — plan, review, then render

`plan` writes the script and one small preview image per scene and stops.
Review the scenes, edit titles, visuals or dialogue (changing the visuals
redraws just that preview), then `render`. Every step is snapshotted, so a
storyboard edit is undoable like any other change.

Characters carry an **appearance lock** — their description topped up with
stable details for whatever the writer left vague — plus a fixed image seed,
so a character doesn't change face between shots or after a re-render.
"Change character design" re-rolls both, deliberately, and keeps the new look.

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
python -m pytest -q                     # full suite, offline
python scripts/benchmark.py --offline   # fixed prompts, measured
```

`scripts/benchmark.py` runs a fixed set of prompts and reports, per run: time
per phase, film length vs target, whether the video matched the audio timeline
exactly, whether every line landed on a cut, and which provider served each
image (so a silent downgrade shows up as a number). Reports land in
`data/benchmarks/` and can be written as markdown with `--out`.

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

Current results: **153 / 153 passing**.

```
$ python -m pytest -q
153 passed in ~3 min
```

---

## Configuration

**Which model does what** lives in [`config/providers.yaml`](config/providers.yaml);
`.env` only holds credentials. Each agent asks for a *role* — `story`,
`edit_intent`, `translate`, `image`, `tts`, `music` — and gets the first provider
in that role's list whose keys are present. If a call fails at run time, the next
provider takes over. Nothing works? Everything still runs offline (template
script, keyword edits, placeholder images, silent audio).

```bash
python main.py providers     # shows the chain per role and what each one needs
```

To change a model, reorder a chain, or add a paid provider later, edit the YAML —
no code changes. Handy overrides: `PROVIDERS_FILE` (use another file),
`PROVIDER_IMAGE=placeholder` (force one provider for a role), `LLM_PROVIDER=mock`
(force the offline path; the tests use this).

### Free keys worth adding (all no-card)

| Key in `.env` | Unlocks | Where |
|---|---|---|
| `GEMINI_API_KEY` | Gemini Flash writes the script, classifies edits and translates subtitles | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `GROQ_API_KEY` | fast fallback LLM (`openai/gpt-oss-120b`) | [console.groq.com](https://console.groq.com/keys) |
| `OPENROUTER_API_KEY` | second fallback via `openrouter/free` | [openrouter.ai](https://openrouter.ai/keys) |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | **FLUX images, 4 at a time** (10,000 neurons/day ≈ 170 images) instead of the slow keyless Pollinations endpoint | [dash.cloudflare.com](https://dash.cloudflare.com) |
| `POLLINATIONS_API_KEY` | real Pollinations models instead of the degraded legacy one | [enter.pollinations.ai](https://enter.pollinations.ai) |
| `OLLAMA_HOST` | local models through Ollama, fully offline | `ollama serve` |

Other `.env` knobs: `ELEVENLABS_API_KEY` (premium TTS), `SD_API_URL` (Automatic1111 /
ComfyUI), `LOCAL_SD=1` (in-process diffusers), `SUBTITLE_EXTRA_LANGUAGES=Urdu,French`,
`MYMEMORY_EMAIL` (raises the free translation quota from ~5k to ~50k chars/day).

### Parallelism

Each provider declares how many calls it tolerates at once (`concurrency:`), and
images and TTS lines fan out to that limit. Cloudflare runs 4 image jobs in
parallel; the keyless Pollinations endpoint answers one request per IP (it returns
429s for the rest), and a local GPU pipeline stays at 1.

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

## Origins

This started as a four-person Agentic AI semester project at the National
University of Computer & Emerging Sciences (Spring 2026), where the five
phases were split across the team:

| Phase | Module |
|-------|--------|
| 1. Story, Script & Character | `agents/story_agent/`, `mcp/tools/llm_tools/` |
| 2. Audio Generation | `agents/audio_agent/`, `mcp/tools/audio_tools/` |
| 3. Video Composition | `agents/video_agent/`, `mcp/tools/vision_tools/`, `mcp/tools/video_tools/` |
| 4. Web Interface | `backend/`, `frontend/` |
| 5. Intelligent Edit & Undo | `agents/edit_agent/`, `state_manager/` |

Everything after that original submission — the single timeline that fixed the
audio/video drift, the provider configuration layer, the storyboard review
step, the durable job queue, accounts, the containers, and the camera work —
is continued solo development, documented milestone by milestone in
[CLAUDE.md](CLAUDE.md).

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
