# Project Report: Agentic AI — Animated Video Generation System

> **Historical document.** This is the original FAST-NUCES semester submission and
> describes the project as it stood then. The system has since moved on (one shared
> timeline for audio/video/subtitles, a model settings layer, new providers), so for
> current behaviour see [`Readme.md`](../Readme.md) and [`CLAUDE.md`](../CLAUDE.md).

## 1. System Design & Architecture

The system implements a five-phase agentic pipeline that converts natural language prompts into polished short animated films. 

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

The shared **`PipelineState`** Pydantic object flows between phases. It carries `script`, `audio`, `video` blocks, and per-phase status. Each phase's output is serialized to disk as JSON, allowing independent execution.

### Phase 1: Story & Script
* **Input**: User prompt
* **Function**: Expands prompt → narrative → scenes → dialogue → characters
* **Output**: `ScriptOutput`

### Phase 2: Audio & Subtitles
* **Input**: `ScriptOutput`
* **Function**: Per-line TTS with character-consistent voices, mood-based BGM synthesis, master mixing, and subtitle translation (via LLM).
* **Output**: `AudioOutput` + `timing_manifest.json`

### Phase 3: Video Composition
* **Input**: `AudioOutput`
* **Function**: Generates establishing shots, character portraits, and composes them with cinematic crossfades (sub-clips per dialogue line) using `ffmpeg`. Optionally generates real motion video via SVD. Burns translated subtitles into the MP4.
* **Output**: `VideoOutput` + `final_output.mp4`

### Phase 4: Web Interface
* **Function**: FastAPI + WebSocket backend serving a vanilla JS frontend. Enables live progress tracking, streaming, and editing.

### Phase 5: Intelligent Edit & Undo
* **Function**: Natural language intent classification (audio, video, script). Creates append-only snapshots in an SQLite database, allowing full "Undo" functionality to restore earlier states.

---

## 2. API Choices & Technology Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| **LLM Provider** | Mock Template / Gemini / OpenAI | Mock mode ensures the project runs 100% offline without API keys. Gemini/OpenAI are supported for dynamic script generation. |
| **TTS Engine** | Edge-TTS (Azure Neural) | Completely free, ultra-realistic neural voices without requiring API keys, vastly outperforming `gTTS`. |
| **Image Gen** | Pollinations.ai / Local SDXL | Free, no-key image generation for rapid prototyping. Local SDXL allows 100% local rendering. |
| **Video Composition** | FFmpeg | Deeply programmatic video editing without heavy GUI overhead. Enables precise sub-clip timings, crossfades, and multi-language `libass` subtitle burning. |
| **Backend / API** | FastAPI + WebSockets | High performance, native async support for long-running generation tasks, and WebSockets for real-time UI progress bars. |

---

## 3. Challenges

1. **Subtitle Rendering with CJK Fonts**: Windows FFmpeg often fails to parse absolute paths correctly and defaults to missing fonts for Japanese/Chinese characters. Solved by explicitly passing `filename=` to the subtitle filter and using `libass` to map system-level font fallbacks.
2. **State Management & Undo Logic**: Orchestrating complex rollbacks (restoring both SQLite metadata and heavy media files like MP4s and WAVs) was difficult. Solved using an append-only snapshotting system (`data/state_versions/`) that treats every edit as a forward-moving version.
3. **High-Quality Audio without Costs**: Originally, voices sounded robotic using `gTTS`. Premium APIs like ElevenLabs were cost-prohibitive. Solved by integrating `edge-tts`, reverse-engineering the Microsoft Edge Read Aloud API to gain free access to Azure Neural Voices.
4. **Cinematic Pacing**: Generating one static image per scene felt like a slideshow. Solved by slicing scenes into sub-clips mapped precisely to dialogue lines using `ffprobe` duration extraction, allowing cuts between "establishing shots" and "character closeups."

---

## 4. Results

The system successfully achieves the goals of the Semester Project brief:
* **Fully Automated**: From a single prompt, a full video is generated in ~10 seconds (mock mode) or ~2 minutes (LLM mode).
* **Highly Modular**: Each agent operates independently.
* **Iterative Editing**: Users can successfully say "make scene 2 darker" or "change the voice to whispered", and the Edit Agent parses the intent and re-runs only the necessary phase.
* **Multi-lingual**: Full support for dynamic subtitle translations (e.g., English audio with Japanese subtitles).
* **Cost-Efficient**: Operates with zero required API costs by utilizing clever fallbacks (Edge-TTS, Pollinations, Mock LLM).

---

## 5. Division of Work (3 Members)

The workload was distributed across 3 group members to cover the 5 phases of the project:

| Member | Primary Responsibilities | Files Owned / Areas |
|--------|--------------------------|---------------------|
| **Member 1** | **Phase 1 (Story/Script) + Phase 4 (Web Interface)**<br>Responsible for the LangGraph-style story planner, character consistency algorithms, FastAPI backend setup, WebSocket progress streaming, and the Vanilla JS frontend UI. | `agents/story_agent/`, `backend/`, `frontend/`, `mcp/tools/llm_tools/` |
| **Member 2** | **Phase 2 (Audio & Subtitles) + Phase 5 (Edit Classifier)**<br>Implemented Edge-TTS neural voice mapping, BGM synthesizer, multi-language subtitle translation logic, and the NLP intent classifier for the Edit Agent. | `agents/audio_agent/`, `mcp/tools/audio_tools/`, `agents/edit_agent/classifier.py` |
| **Member 3** | **Phase 3 (Video Comp) + Phase 5 (State Manager/Undo)**<br>Engineered the FFmpeg cinematic crossfading, image generation toolchains (Pollinations/SDXL), and the complex SQLite append-only versioning system allowing pipeline rollbacks. | `agents/video_agent/`, `mcp/tools/video_tools/`, `state_manager/`, `agents/edit_agent/executor.py` |

All members jointly collaborated on the shared Pydantic schemas (`shared/schemas/`), integration tests, and architecture planning.
