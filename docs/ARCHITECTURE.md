# Software architecture

How the system is put together, from the outside in: what it talks to, the
processes it runs as, the components inside them, how a request becomes a
film, and the data that persists. The agents themselves (how a prompt becomes
a script, voices and shots) are in [AGENTS.md](AGENTS.md); every framework
and why it was chosen is in [TECH_STACK.md](TECH_STACK.md).

- [1. System context](#1-system-context)
- [2. Containers](#2-containers)
- [3. Components](#3-components)
- [4. Making a film, end to end](#4-making-a-film-end-to-end)
- [5. Editing a finished film](#5-editing-a-finished-film)
- [6. Lifecycles](#6-lifecycles)
- [7. Data model](#7-data-model)
- [8. Deployment](#8-deployment)
- [9. Cross-cutting concerns](#9-cross-cutting-concerns)

---

## 1. System context

One product, one origin. A creator uses the web app (or the CLI); everything
the product cannot do on its own machine (language models, image models,
online voices) is a provider behind a swappable chain, and every chain ends
in something that works offline.

```mermaid
flowchart LR
    creator(["Creator<br/>browser or CLI"])

    subgraph product["Dastango"]
        app["Web app · API · workers<br/>Kokoro voices and ffmpeg run locally"]
    end

    creator -- "a sentence, storyboard edits,<br/>changes in plain English" --> app
    app -- "live storyboard, progress,<br/>the finished film" --> creator

    app -- "script · edit intent · translation" --> llm["LLM APIs<br/>Gemini · Groq · OpenRouter"]
    app -- "storyboard and shot images" --> img["Image APIs<br/>Cloudflare Workers AI FLUX · Pollinations"]
    app -- "online voice fallbacks" --> tts["Edge TTS · gTTS"]
    app -- "subtitle fallback" --> mm["MyMemory"]
    app -- "sign in" --> gh["GitHub OAuth"]
    app -- "nightly backups" --> r2[("Cloudflare R2")]
    le["Let's Encrypt"] -- "TLS certificate" --> app

    classDef ext fill:#f4f4f5,stroke:#a1a1aa,color:#18181b
    classDef core fill:#dbeafe,stroke:#2563eb,color:#0f172a
    class llm,img,tts,mm,gh,r2,le ext
    class app core
```

| External system | Used for | If it is down |
|---|---|---|
| Gemini, Groq, OpenRouter | Writing the script, reading an edit, translating subtitles | The next model in the chain answers; with none, a deterministic template writes the script and a keyword classifier reads edits |
| Cloudflare Workers AI, Pollinations | Storyboard and shot images | The next provider draws it; a local placeholder guarantees a file exists |
| Edge TTS, gTTS | Voices when Kokoro is not installed | Kokoro runs offline; silence of the right length keeps timing intact |
| MyMemory | Subtitle translation when no LLM can | That language is skipped; an English track is never shipped under a foreign label |
| GitHub | Optional sign-in | Email and password still work |
| Cloudflare R2 | Off-machine copy of the nightly backup | Local snapshots on the VM still exist |

---

## 2. Containers

The production stack is five services in Docker Compose on one VM. The API and
the workers share nothing but the database and the data volume, so workers
scale out (`--scale worker=3`) and a render survives an API restart.

```mermaid
flowchart TB
    browser["Browser<br/>Next.js 16 static export · React 19"]

    subgraph vm["Oracle Cloud VM · Ubuntu 24.04 aarch64 · Docker Compose"]
        caddy["caddy<br/>Caddy 2 · automatic HTTPS · HTTP/3"]
        api["api<br/>FastAPI on uvicorn<br/>serves the web app, REST and WebSocket"]
        worker["worker<br/>claims jobs → orchestrator → agents<br/>ffmpeg · Kokoro on onnxruntime"]
        db[("db<br/>PostgreSQL 16<br/>accounts · jobs · events · versions")]
        data[("data volume<br/>films · version snapshots · voice model")]
        models["models<br/>one-shot: fetch and SHA-256-verify Kokoro"]
        cron["cron → deploy/backup.sh<br/>pg_dump + hard-linked rsync snapshots"]
    end

    providers["Model providers<br/>LLMs · images · online TTS"]
    r2[("Cloudflare R2")]

    browser -- "HTTPS · WSS" --> caddy
    caddy -- "reverse proxy" --> api
    api -- "enqueue · read events · versions" --> db
    worker -- "claim (SKIP LOCKED) · heartbeat · events" --> db
    api -- "stream films with Range" --> data
    worker -- "write films and snapshots" --> data
    models --> data
    worker -- "HTTPS" --> providers
    cron --> db
    cron --> data
    cron -- "changed files only" --> r2

    classDef svc fill:#dbeafe,stroke:#2563eb,color:#0f172a
    classDef store fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef ext fill:#f4f4f5,stroke:#a1a1aa,color:#18181b
    class caddy,api,worker,models,cron svc
    class db,data store
    class providers,r2,browser ext
```

| Service | Image | Health |
|---|---|---|
| `caddy` | `caddy:2-alpine` | Obtains and renews the certificate itself |
| `api` | the app image (`Dockerfile`, multi-stage: Node 22 builds the UI, Python 3.11 runs it) | `GET /health` |
| `worker` | the same image, `python main.py worker` | Touches a file each time it reaches the queue; stale for 2 min → unhealthy |
| `db` | `postgres:16-alpine` | `pg_isready` |
| `models` | the app image, runs once per start | Fetches and SHA-256-verifies the model; a failed download never blocks the app; voices fall back to Edge TTS |

On a laptop the same code runs as **one process**: `python main.py serve`
starts the API with a worker thread inside it and uses a SQLite file in WAL
mode. Nothing in the agents knows which of the two it is running in.

---

## 3. Components

Inside the two processes. Arrows point from caller to callee; the database is
the only thing the API and the worker have in common.

```mermaid
flowchart TB
    subgraph apiP["API process (backend/)"]
        direction TB
        routes["routes/<br/>auth · pipeline · edit · history<br/>jobs · projects · voices"]
        wsr["websocket/progress.py<br/>replays job_events rows"]
        assetsR["/assets route<br/>owner check · Range"]
        deps["auth/deps.py<br/>require_user · require_project"]
    end

    subgraph wkP["Worker process (jobs/worker.py)"]
        direction TB
        loop["claim → run → publish → finish"] --> orch["agents/orchestrator<br/>PipelineOrchestrator"]
        orch --> ag["Story · Audio · Video · Edit agents"]
        ag --> tools["mcp/ tool registry · 23 tools"]
        tools --> prov["shared/providers.py<br/>role → provider chain"]
    end

    subgraph shared["Shared core"]
        direction LR
        queue["jobs/queue.py<br/>enqueue · claim · heartbeat · cancel"] ~~~ sm["state_manager/<br/>versions + file snapshots"] ~~~ assetsM["shared/assets.py<br/>path → URL"] ~~~ dbm[("shared/db.py<br/>SQLite or Postgres")]
    end

    routes --> deps
    wsr --> deps
    assetsR --> deps
    routes --> queue
    routes --> sm
    wsr --> dbm
    loop --> queue
    loop --> assetsM
    ag --> sm
```

| Component | Path | Responsibility |
|---|---|---|
| Routes | `backend/routes/` | HTTP contract. Starting work means enqueueing a job and answering in milliseconds with its id. |
| Auth dependencies | `auth/deps.py` | **The only place a request becomes a user.** Declared on the routers, so a new endpoint is protected the moment it exists. |
| Progress | `backend/services/progress.py`, `backend/websocket/` | Streams a project's job events to the browser by reading rows (every 0.4 s), so a reload or a worker on another host loses nothing. |
| Job queue | `jobs/queue.py` | Runs are rows. One atomic `UPDATE` per claim; one job per project at a time, in order. |
| Worker | `jobs/worker.py` | Claims, heartbeats every 15 s, runs the orchestrator, publishes assets, records the outcome. |
| Orchestrator | `agents/orchestrator/` | `plan`, `render`, `run_full`, `edit`, `revert`: each a small graph of agent steps that emits progress events. |
| Agents | `agents/*_agent/` | The work: script, voices, pictures, edits. See [AGENTS.md](AGENTS.md). |
| Tool layer | `mcp/` | An internal registry of 23 tools (`audio.tts`, `vision.generate_image`, `video.compose`, …). Each tool walks its provider chain. Not the MCP protocol. |
| Providers | `shared/providers.py`, `config/providers.yaml` | Which model serves each role, in order. Agents never name a model. |
| State manager | `state_manager/` | Append-only version log plus a copy of every file a version refers to; revert is a new version. |
| Timeline | `shared/timeline.py` | The single source of timing for audio, cuts and subtitles. |
| Assets | `shared/assets.py` | The only place a file path becomes a URL. |

---

## 4. Making a film, end to end

The storyboard is the product's central idea: the cheap half (script and one
picture per scene, ~20 s) is shown and edited **before** the expensive half
(voices and shots, minutes) is spent.

```mermaid
sequenceDiagram
    autonumber
    actor C as Creator
    participant W as Web app
    participant A as API
    participant DB as Postgres
    participant K as Worker
    participant O as Orchestrator and agents
    participant P as Providers

    C->>W: Writes one sentence
    W->>A: POST /api/pipeline/plan
    A->>DB: insert job (plan, queued)
    A-->>W: queued (project_id and job_id), in milliseconds
    W->>A: open WS /ws/progress/{project_id}
    K->>DB: claim the oldest runnable job in one UPDATE
    K->>O: plan(prompt)
    O->>P: structured script (Gemini, else Groq, else OpenRouter, else template)
    O->>DB: event storyboard/script (scenes, cast, image cost)
    Note over A,DB: The socket reads new job_events rows every 0.4 s
    A-->>W: the script appears, every scene readable
    loop one preview per scene
        O->>P: image (Cloudflare FLUX, else Pollinations, else placeholder)
        O->>DB: event storyboard/frame
        A-->>W: that frame develops in
    end
    O->>DB: snapshot version 1, stage storyboard
    K->>DB: job succeeded

    C->>W: Fixes a scene, then Render this film
    W->>A: PATCH storyboard scene, then POST /api/pipeline/render/{project_id}
    A->>DB: insert job (render, queued)
    K->>O: render(project_id)
    O->>O: Audio agent: voices, timeline, music, master
    O->>P: portraits and shot images
    O->>O: Video agent: shots on the timeline, scenes, film, subtitles
    O->>DB: snapshot version 2, stage rendered
    K->>DB: job succeeded
    A-->>W: complete
    W->>A: GET /api/pipeline/film/{project_id}
    W->>A: GET /assets/{project_id}/final_output.mp4 with Range
```

Why each step is shaped this way:

- **A job, never work inside the request.** A render takes minutes;
  as a row it survives an API restart, can be watched from any process, and
  can be cancelled.
- **Events are rows.** The browser's socket replays the job from the first
  event on connect and de-duplicates by row id, so a reload mid-render shows
  exactly what a continuous connection would have.
- **The preview is drawn at the render size**, so the render reuses it as the
  scene's establishing shot instead of paying for the same image twice.
- **Snapshots after every step** make a storyboard edit as undoable as a
  render.

---

## 5. Editing a finished film

An edit is a job too, and it either completes as a new version or leaves the
film exactly as it was.

```mermaid
sequenceDiagram
    autonumber
    actor C as Creator
    participant A as API and Web app
    participant K as Worker
    participant E as Edit agent
    participant M as LLM (edit_intent chain)
    participant S as State manager

    C->>A: "make the voices in scene 2 whispered"
    A->>K: job (edit, max_attempts 1)
    K->>E: edit(project_id, query)
    E->>S: put back the saved version's files
    E->>M: fill a closed form: intent, scope, parameters
    M-->>E: change_voice_tone · scene_2 · tone whispered
    E-->>A: event understood: "whispered voices · scene 2"
    alt understood and complete
        E->>E: plan steps: rerun_audio(scene_2), recompose_video
        E->>E: execute with the same agent primitives a render uses
        E->>S: snapshot as a new version
        E-->>A: complete (new version)
    else unclear, incomplete, or a step fails
        E->>S: put back the saved version's files
        E-->>A: refused with what to say instead (film unchanged)
    end
```

Going back ("Go back" in the UI, `POST /api/history/{id}/revert/{n}`) restores
version *n*'s state and files and saves the result as a **new** version, so
history stays linear and nothing is ever lost.

---

## 6. Lifecycles

### A job

```mermaid
stateDiagram-v2
    direction LR
    [*] --> queued
    queued --> running: claimed
    running --> succeeded
    running --> queued: retry
    running --> failed: no attempts left
    queued --> cancelled: cancel
    running --> cancelled: cancel
    succeeded --> [*]
    failed --> [*]
    cancelled --> [*]
```

- **retry** happens when a run fails with attempts left (after a 5 s
  back-off), or when its worker stops heartbeating for 120 s: a crashed or
  killed worker's job goes back on the queue.
- **cancel** stops a queued job at once and a running one at its next step
  boundary.
- A claim skips any job whose project still has an **older unfinished job**,
  so two workers can never run two changes to one film at once, decided by
  that row's existence, which holds under `SKIP LOCKED`.
- Pipeline runs get two attempts; edits and reverts get one (a half-applied
  edit is rolled back, not retried).
- Cancellation is cooperative and lands between pipeline steps, never in the
  middle of an ffmpeg call, so a cancelled run leaves no half-written file.

### A project

Every arrow saves a version: a storyboard edit, a render, an edit and a "go
back" are all undoable the same way.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> draft
    draft --> storyboard: plan
    draft --> rendered: run_full
    storyboard --> storyboard: edit a scene
    storyboard --> rendered: render
    rendered --> rendered: edit · go back
```

---

## 7. Data model

One database holds the version log, the job queue, and accounts: SQLite by
default, PostgreSQL in production, the same SQL through SQLAlchemy Core.

```mermaid
erDiagram
    USERS ||--o{ SESSIONS : "signs in with"
    USERS ||--o{ IDENTITIES : "links"
    USERS ||--o{ PROJECTS : "owns"
    PROJECTS ||--o{ JOBS : "runs as"
    JOBS ||--o{ JOB_EVENTS : "emits"
    PROJECTS ||--o{ VERSIONS : "is saved as"
    PROJECTS ||--o{ EDIT_LOG : "records"

    USERS {
        string id PK
        string email UK
        text password_hash "argon2id, or unusable for GitHub-only accounts"
        string role "user or admin"
        boolean is_active
        int failed_attempts
        datetime locked_until
    }
    SESSIONS {
        string id PK "SHA-256 of the cookie token"
        string user_id FK
        datetime expires_at "14 days"
        datetime last_seen_at
    }
    IDENTITIES {
        string provider PK "github"
        string subject PK "GitHub's numeric id"
        string user_id FK
        string login
    }
    PROJECTS {
        string project_id PK
        string owner_id FK
        datetime created_at
    }
    JOBS {
        string id PK
        string project_id FK
        string kind "plan, render, run_full, rerun_phase, edit, revert"
        json payload
        string status "queued, running, succeeded, failed, cancelled"
        int attempts
        int max_attempts
        boolean cancel_requested
        datetime heartbeat_at
    }
    JOB_EVENTS {
        int id PK
        string job_id FK
        string phase
        string status
        float progress
        json payload
    }
    VERSIONS {
        int id PK
        string project_id FK
        int version
        int parent_version
        text state_path "PipelineState JSON for this version"
        json asset_paths "every file it refers to"
        json edit_intent
    }
    EDIT_LOG {
        int id PK
        string project_id FK
        text query
        json intent_json
        json result_json
    }
```

The **`PipelineState`** (Pydantic, `shared/schemas/pipeline.py`) is the one
object passed between phases and versioned: `script` (story, cast, scenes and
lines), `audio` (voices, per-scene voice overrides, timing manifest, master
track), `video` (frames, shots, portraits, subtitle tracks, speed), the
storyboard, the stage, and per-phase status. Each version stores its state as
JSON plus copies of every file the state refers to, which is what makes
"go back" restore the film itself and not only its description.

On disk, everything lives under `DATA_DIR` (default `./data`):
`outputs/<project_id>/` for the current files, `state_versions/` for each
version's copies, and the SQLite file when not using Postgres.

---

## 8. Deployment

Live at **https://139-185-59-132.sslip.io** on Oracle Cloud's Always Free ARM
VM. The step-by-step runbook is [deploy/README.md](../deploy/README.md).

```mermaid
flowchart LR
    user["Browser"] -- "139-185-59-132.sslip.io" --> dns["sslip.io DNS<br/>the name encodes the IP"]
    user -- "443 tcp/udp" --> sl
    subgraph oci["Oracle Cloud · UAE East · Always Free"]
        sl["Security list<br/>22 · 80 · 443 tcp · 443 udp"]
        subgraph vm2["VM.Standard.A1.Flex · 2 OCPU · 12 GB"]
            ipt["iptables<br/>web ports above Oracle's REJECT"]
            caddy2["Caddy :443"]
            api2["api :8000<br/>reachable only through Caddy"]
            worker2["worker ×1"]
            pg["Postgres"]
        end
    end
    sl --> ipt --> caddy2 --> api2
    api2 --- pg
    worker2 --- pg
    caddy2 -. "TLS-ALPN-01" .- le["Let's Encrypt"]
    worker2 -- "03:15 UTC backup" --> r2b[("R2 bucket")]
```

| Concern | How |
|---|---|
| HTTPS | Caddy obtains and renews a Let's Encrypt certificate; `sslip.io` gives a real hostname without buying a domain |
| Only one way in | The API publishes no port in production; it is reachable only through Caddy, which is what makes trusting `X-Forwarded-*` safe |
| First boot | API and worker race to create the schema; Postgres takes an advisory lock, SQLite retries |
| Secrets | `.env` on the server, mode 600, never in git; compose pins container paths over anything in it |
| Backups | Nightly `pg_dump` + rsync snapshot hard-linked to the previous night (7 kept), and the changed files pushed to R2 (14 dumps kept) |
| Restore | `deploy/restore.sh latest` or `bucket`; both proven by deleting the data and restoring it |
| Updates | `git pull`, rebuild, `docker compose up -d` once no job is running |
| Speed | 2 OCPUs: a 43 s, 5-scene film renders in 4 min 52 s with shots drawn two at a time |

### Continuous integration

```mermaid
flowchart LR
    push["push or pull request"] --> t1["tests · Ubuntu · Python 3.11"]
    push --> t2["tests · Ubuntu · Python 3.12"]
    push --> t3["tests · Windows · Python 3.11"]
    push --> web["web · npm ci → next build → tsc"]
    push --> img["image · docker build with GHA cache"]
    img --> i1["drivers import"]
    i1 --> i2["in-image tests<br/>subtitle fonts · boot race · voice crash"]
    i2 --> i3["compose up on a fresh database<br/>/ready answers · zero restarts"]
```

The suite is fully offline (a mock LLM, placeholder images and silent voices),
so CI needs no secrets and spends no one's quota.

---

## 9. Cross-cutting concerns

**Failure is visible, never silent.** A provider that fails returns
`success=False` or raises; the chain moves on and the log says which provider
served each image. Translation that fails skips the language. An edit that
cannot be carried out is refused, not reported as done. A render with every
image provider exhausted still completes, with placeholders, and says so.

**Timing has one owner.** `shared/timeline.py` places every line; video
converts *absolute* millisecond boundaries to frames (never durations), so
rounding cannot accumulate and the final video's frame count equals the
timeline's exactly.

**Work is idempotent where it is expensive.** A scene's clip is reused when
its shot plan and source images are unchanged (a signature over both), so an
edit to scene 2 re-renders scene 2. A storyboard preview drawn at render size
becomes the establishing shot.

**Concurrency is bounded by what each resource tolerates.** Image and voice
calls fan out to each provider's declared `concurrency`, with a semaphore per
provider so a fallback endpoint that takes one request at a time is never
flooded. Shots render one per core the process may use (at most four).

**Security is enforced in one place each.** Requests become users only in
`auth/deps.py`; paths become URLs only in `shared/assets.py`; another user's
project answers `404`, never `403`. See [SECURITY.md](../SECURITY.md).

**Observability.** Structured logs per phase and provider; the job table
answers "what ran, what failed and why" (`python main.py jobs --status failed`);
`/health` and `/ready` (database reachable) for the proxy and Compose.
