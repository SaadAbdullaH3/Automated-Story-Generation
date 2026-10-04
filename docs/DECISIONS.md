# Architecture decisions

The decisions that shape the system, each with the context that forced it,
what was rejected, and what it costs. Most were made, or reversed, because a
measurement or a real run said so.

| # | Decision | Status |
|---|---|---|
| [001](#adr-001-review-a-cheap-storyboard-before-the-expensive-render) | Review a cheap storyboard before the expensive render | Accepted |
| [002](#adr-002-a-plain-python-orchestrator-not-langgraph) | A plain-Python orchestrator, not LangGraph | Accepted |
| [003](#adr-003-jobs-are-database-rows-not-a-broker) | Jobs are database rows, not a broker | Accepted |
| [004](#adr-004-one-timeline-owns-all-timing) | One timeline owns all timing | Accepted |
| [005](#adr-005-models-are-chosen-by-role-in-configuration) | Models are chosen by role, in configuration | Accepted |
| [006](#adr-006-sessions-in-the-database-not-jwts) | Sessions in the database, not JWTs | Accepted |
| [007](#adr-007-edits-are-a-closed-vocabulary) | Edits are a closed vocabulary | Accepted |
| [008](#adr-008-undo-restores-files-and-history-stays-linear) | Undo restores files, and history stays linear | Accepted |
| [009](#adr-009-camera-moves-on-stills-real-motion-is-opt-in) | Camera moves on stills; real motion is opt-in | Accepted |
| [010](#adr-010-the-ui-is-a-static-export-on-the-apis-origin) | The UI is a static export on the API's origin | Accepted |
| [011](#adr-011-one-free-vm-with-compose-and-caddy) | One free VM with Compose and Caddy | Accepted |
| [012](#adr-012-local-disk-unless-told-otherwise) | Local disk unless told otherwise | Accepted |
| [013](#adr-013-shots-render-side-by-side-bounded-by-cores) | Shots render side by side, bounded by cores | Accepted |
| [014](#adr-014-github-sign-in-never-joins-accounts-by-email) | GitHub sign-in never joins accounts by email | Accepted |

---

## ADR-001: Review a cheap storyboard before the expensive render

**Context.** A full render takes minutes and spends image and voice quota.
The first version went straight from prompt to finished film, so a script the
creator disliked cost the whole render to discover.

**Decision.** Split the pipeline at its natural checkpoint. `plan` writes the
script and draws one preview per scene (~20 s); the creator reads and edits it;
`render` spends the rest. The script streams to the browser before any image
exists, so the wait is spent reading.

**Consequences.** The preview is drawn at the render size, so the render reuses
it as the establishing shot instead of paying for it twice (this only worked
after a bug was found where previews were drawn at 512×288 and never matched).
`run_full` remains for the CLI and scripts.

## ADR-002: A plain-Python orchestrator, not LangGraph

**Context.** The pipeline is a fixed sequence (story, audio, video) with one
human checkpoint, plus edit and revert operations that are themselves short
sequences. Progress has to stream, steps have to be cancellable between nodes,
and the whole thing must run in a test with no network.

**Decision.** A 50-line node executor (`agents/orchestrator/graph.py`) with
`on_node` / `on_node_done` callbacks that become progress events. State is a
Pydantic `PipelineState`; persistence is the version log.

**Rejected.** LangGraph, CrewAI, AutoGen. None of the graph shapes here need
conditional routing, parallel branches or agent-to-agent conversation; a
framework would add a dependency, its own state model and a debugging layer.
The node and state shapes match LangGraph's, so adopting it later (for
example, if agents start choosing their own tools) is mechanical.

**Consequences.** Everything is ordinary Python, stack traces point at real
code, and cancellation lands exactly between nodes.

## ADR-003: Jobs are database rows, not a broker

**Context.** Runs were `BackgroundTasks` closures plus an in-memory dict: an
API restart lost them, nothing outside the process could see them, and they
could not be stopped.

**Decision.** A run is a row in `jobs`. The API inserts it and answers at once; a
worker claims with one atomic `UPDATE` (`FOR UPDATE SKIP LOCKED` on Postgres;
on SQLite the `status='queued'` guard makes a second claim match nothing).
Progress is rows in `job_events`. A worker heartbeats every 15 s; a job whose
heartbeat is two minutes old goes back on the queue, bounded by
`max_attempts`. A claim also skips any job whose project has an older
unfinished job, so changes to one film run strictly in order.

**Rejected.** Celery or RQ with Redis: another service to run, secure and
back up, and progress would still need a store the browser can replay.

**Consequences.** Measured: 40 jobs claimed by 4 processes at once, no job
claimed twice and none left behind; a job queued while the API was shut down
ran when it came back; a cancel landed 16 s later at the next step. Polling
rows every 0.4 s for progress is cheap at this scale and would be the first
thing to change at a much larger one (LISTEN/NOTIFY).

## ADR-004: One timeline owns all timing

**Context.** The original pipeline timed audio and video independently. The
measured result: the voice led the picture by 2.4 s in scene 1 and 6.1 s by
scene 4, and films came out twice their requested length.

**Decision.** `shared/timeline.py` places every line (pre-roll, line, gap,
tail). Audio mixes lines at their `start_ms`; video cuts on the same
boundaries, converting *absolute* milliseconds to frames so rounding never
accumulates; subtitles read the same numbers.

**Consequences.** The final video's frame count equals the timeline's
exactly, and a test asserts every line starts on a cut. Edits that change a
line's length re-time everything after it automatically.

## ADR-005: Models are chosen by role, in configuration

**Context.** Free tiers change, rate-limit and go down; paid providers will
come later. Agents with `if os.getenv(...)` ladders could not be changed
without code.

**Decision.** `config/providers.yaml` lists, per role (`story`, `edit_intent`,
`translate`, `image`, `tts`, `video`, `lipsync`, `music`), the providers in
order and the credentials each needs. Agents ask for a role. Tools walk the
chain: transient errors retry with back-off where configured, permanent ones
(400/401/403) move on at once, and every chain ends offline.

**Consequences.** Moving to a paid model is a YAML edit. Running it live
found four provider-specific failures in one session (reasoning models
returning nothing at small token caps, Cloudflare rejecting `seed`, a
Pollinations key with no budget, and tests reading the real `.env`), each
fixed once, in the adapter, for every agent.

## ADR-006: Sessions in the database, not JWTs

**Context.** Before accounts, every project was visible to anyone who could
reach the API, and `/assets/<id>/final_output.mp4` handed over the film to
anyone who guessed an id.

**Decision.** The cookie holds a random token; the database stores only its
SHA-256. Sessions last 14 days. Passwords are argon2id with a dummy verify for
unknown accounts, so login timing does not reveal who is registered; eight
failures lock an account for 15 minutes.

**Rejected.** JWTs: signing out and disabling an account would need a
denylist or would wait for expiry.

**Consequences.** Sign-out is a row delete; disabling an account ends its
sessions at once; a stolen database dump cannot be replayed as a login.

## ADR-007: Edits are a closed vocabulary

**Context.** With free-form JSON, a live model classified "make the voices in
scene 2 whispered" as `whisper_voices` with `{"voice_type": "whisper"}`,
both invented. The planner fell back to re-recording the same lines in the
same voice, and the edit reported success. Gemini meanwhile rejected the open
`parameters` object outright.

**Decision.** The model fills an `EditDraft` whose intents and values are
`Literal` enums generated from what the executor can actually do
(`agents/edit_agent/vocabulary.py`). An invented value fails validation and
is retried with the error; a request nothing matches is `unclear` and is
refused with what to say instead; a missing value is asked for by name.

**Consequences.** Measured against both live models: all five real phrasings
mapped correctly (including "the recipe scene should feel darker" to scene 2
by its title), and "make it better" was refused. Adding an edit means adding
it to the vocabulary and the executor together.

## ADR-008: Undo restores files, and history stays linear

**Context.** A film is many files (audio lines, images, shots, the master);
restoring only the JSON state would leave the wrong media on disk.

**Decision.** Each version stores its `PipelineState` and a copy of every file
the state refers to (`state_manager/snapshot.referenced_files`). Reverting
restores both and saves the result as a **new** version. An edit starts from,
and on failure returns to, the saved version's files.

**Consequences.** A revert is byte-identical to the version it restores, and
nothing is ever lost. The cost is storage: every version copies every file.
Content-addressed storage is the planned fix ([ROADMAP](ROADMAP.md)).

## ADR-009: Camera moves on stills; real motion is opt-in

**Context.** Every provider that animates a still well charges per clip. The
product must cost nothing to run. Pans had been removed earlier because
`zoompan` positions its crop at whole pixels and the picture shivered.

**Decision.** Compute each move at 3× the output size and scale down with
lanczos, turning a whole-pixel error into a fraction of an output pixel. Pick
the move from the shot's job and the scene's tone. Real motion (fal, Hugging
Face, Replicate, Veo 3.1) sits in the `video` chain *behind* the free path;
Veo additionally needs `VIDEO_BUDGET_OK=1`, so holding a Gemini key for the
free text models never starts a per-second bill.

**Consequences.** Measured by phase correlation: shots travel up to 5.13
px/frame with a worst frame-to-frame jump of 0.19 px (the old centred zooms
jumped 0.80 px). Supersampling is also the most CPU-hungry step; see
ADR-013.

## ADR-010: The UI is a static export on the API's origin

**Context.** The new interface needed React-level interactivity; the product
should remain one container and one origin.

**Decision.** Next.js with `output: export`, built in the image's first stage
and served by FastAPI at `/`.

**Consequences.** No Node in production, no CORS (`allow_origins` is empty
unless configured), cookies stay first-party. Server-side rendering is not
available, which this app does not need: every screen is driven by the
user's own server state.

## ADR-011: One free VM with Compose and Caddy

**Context.** $0 to run, and the database and films must be backed up and
restorable.

**Decision.** Oracle Cloud's Always Free arm64 VM (2 OCPU / 12 GB), Docker
Compose, Caddy for HTTPS, `sslip.io` for a hostname, nightly `pg_dump` plus
hard-linked rsync snapshots, mirrored to Cloudflare R2.

**Consequences.** Everything is under the project's control and verified by
drills: a restore from local snapshots, and a full "machine lost" restore from
R2 onto an empty stack. Renders are CPU-bound on two ARM cores (an A1 core is
~2.8× slower than a laptop's at this work). Oracle may stop idle Always Free
instances after a week's notice; the data survives a stop.

## ADR-012: Local disk unless told otherwise

**Context.** S3/R2 publishing exists so API and workers can live on different
hosts. On one machine it only adds latency and expiring links.

**Decision.** Local disk is the default; only `STORAGE_URL=s3://…` switches
publishing on. Credentials in the environment do not; a test pins that, so
adding a key to try something cannot silently reroute every render.

**Consequences.** Verified against a local S3 server and real R2 (31 assets,
17 MB, byte-identical round trip), while production stays on local disk.

## ADR-013: Shots render side by side, bounded by cores

**Context.** On the 2-core server a first real film rendered in 6 min 38 s.
CPU sampling showed shots (75% of the render) holding ~130% of 200%:
`zoompan` is single-threaded and shots ran one at a time.

**Decision.** Plan every scene first, then render the shots of all changed
scenes in one pool, one per core the process may use (`sched_getaffinity`,
so a pinned container is respected), at most four (each holds an 8.5 MP
frame), `SHOT_WORKERS` to override.

**Consequences.** Same benchmark 188 s → 136 s with CPU at ~196%; a real
43 s, 5-scene film rendered in 4 min 52 s. Peak memory 471 MB.

## ADR-014: GitHub sign-in never joins accounts by email

**Context.** Email addresses are not verified here, so linking a GitHub
sign-in to an existing account by matching email would hand the account to
whoever registered that address first (pre-account hijacking).

**Decision.** Identities are keyed by GitHub's numeric id, not the login. A
GitHub sign-in either finds its linked identity or creates a new account
(only when sign-ups are open); an existing account connects GitHub while
signed in. The OAuth `state` is checked against an HttpOnly cookie scoped to
the callback path before the code is spent.

**Consequences.** The first sign-in for an existing admin is a deliberate
"Connect GitHub" from the top bar. Three tests each fail when their guard is
removed (state check, email refusal, disabled-account check).
