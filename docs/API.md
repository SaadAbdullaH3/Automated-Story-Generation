# API reference

Everything the web app does goes through this API; the CLI calls the same
orchestrator directly. The interactive reference is served at `/api/docs` and
the OpenAPI schema at `/api/openapi.json` (`/docs` is the product's own guide).

**Conventions**

- **Same origin, cookie auth.** Sign in sets an HttpOnly session cookie
  (`storygen_session`, `Secure` over HTTPS, 14 days). Every `/api/*` route
  except `/api/auth/*` requires it, enforced on the router, not per endpoint.
- **Someone else's project is a `404`**, never a `403`: a different status
  would confirm the id exists.
- **Long work is a job.** Endpoints that start work return at once with
  `{project_id, job_id, status, websocket}`; follow progress on the WebSocket
  or poll `/api/jobs/{job_id}`.
- Errors are `{"detail": "…"}` with a sentence a person can act on.

---

## Health

| Method | Path | Auth | |
|---|---|---|---|
| GET | `/health` | none | Liveness: the process is up |
| GET | `/ready` | none | Readiness: the database answers. `{"status", "database", "storage", "inline_worker"}` |

## Accounts: `/api/auth`

| Method | Path | Auth | |
|---|---|---|---|
| GET | `/status` | none | `{authenticated, user, needs_setup, signups_allowed, min_password_length, github: {enabled, connected}}` |
| POST | `/register` | none | `{email, password}`. The first account becomes the admin; after that `403` unless `ALLOW_SIGNUPS=1` |
| POST | `/login` | none | `{email, password}`. Wrong password and unknown address give the same `401`; 8 failures lock for 15 min |
| POST | `/logout` | session | Deletes the session row |
| GET | `/me` | session | The signed-in user |
| POST | `/password` | session | `{current_password, new_password}`; ends every other session |
| GET | `/sessions` | session | Your active sessions |
| GET | `/github/start?mode=signin\|connect` | none | Redirects to GitHub with a `state` bound to an HttpOnly cookie |
| GET | `/github/callback` | none | Checks `state`, exchanges the code, signs in or connects; redirects to `/?connected=github` or `/?auth_error=…` |
| GET | `/users` | admin | All accounts |
| POST | `/users` | admin | `{email, password, role}` |
| PATCH | `/users/{user_id}` | admin | `{role?, is_active?}`; an admin cannot demote or disable themselves |

## Making a film: `/api/pipeline`

| Method | Path | Body | |
|---|---|---|---|
| POST | `/plan` | `{prompt, target_duration_s=45, scene_count=4, with_preview=true}` | Queue a storyboard: script + one preview per scene |
| GET | `/storyboard/{project_id}?engine=` | none | Scene cards (tone, camera move, lines and the voice that will speak each), cast, image budget |
| PATCH | `/storyboard/{project_id}/{scene_id}` | `{title?, setting?, visual_prompt?, dialogue?: {line_id: text}}` | Edit one scene; a new `visual_prompt` redraws its preview. Saved as a version |
| POST | `/render/{project_id}` | `{with_bgm, with_subtitles, subtitle_language, burn_subtitles, tts_engine?}` | Queue the render of an approved storyboard |
| POST | `/run` | `{prompt, target_duration_s, scene_count, with_bgm, with_subtitles, subtitle_language, tts_engine?}` | Plan and render in one job |
| POST | `/rerun` | `{project_id, phase: story\|audio\|video}` | Re-run a phase and everything after it, keeping the film's settings |
| GET | `/film/{project_id}` | none | What the player needs: `video_url`, duration, size, version, and scenes as chapters with `start_ms`/`end_ms` and a poster |
| GET | `/subtitles/{project_id}` | none | The film's WebVTT tracks for the player: language, URL, and which one is burned in |
| GET | `/languages` | none | The 14 subtitle languages |
| GET | `/state/{project_id}` | none | The full `PipelineState` |
| GET | `/status/{project_id}` | none | A lightweight status snapshot |

`tts_engine` is validated before a job exists: an unknown engine is `400`, one
this machine cannot run is `409` with the reason ("run
`python scripts/get_kokoro.py`").

## Changing a film: `/api/edit` and `/api/history`

| Method | Path | Body | |
|---|---|---|---|
| POST | `/api/edit/{project_id}` | `{query}` | Queue an edit (one attempt). `409` if the film has not been rendered |
| POST | `/api/edit/classify` | `{query, project_id?}` | What the query would be understood as, without changing anything |
| POST | `/api/edit/apply` | `{project_id, query}` | Queue an edit and wait for it (kept for scripts and the classic page) |
| GET | `/api/edit/log/{project_id}` | none | Every edit with its intent and result |
| GET | `/api/history/{project_id}` | none | The version list |
| GET | `/api/history/{project_id}/film` | none | Versions that produced a film, in the creator's words, with which is current |
| POST | `/api/history/{project_id}/revert/{version}` | none | Go back: restores that version's state and files as a **new** version |

## Jobs: `/api/jobs`

| Method | Path | |
|---|---|---|
| GET | `/?project_id=&status=` | Your jobs (admins see all) |
| GET | `/{job_id}` | Status, attempts, error |
| GET | `/{job_id}/events` | Every progress event the job emitted |
| POST | `/{job_id}/cancel` | A queued job stops now; a running one at its next step |

## Library and voices

| Method | Path | |
|---|---|---|
| GET | `/api/projects/` | Your films: title, prompt, stage, version, scene count, duration, poster frame and video URL |
| GET | `/api/voices/` | Each engine, whether it works on this machine and why not, and its voices |
| POST | `/api/voices/preview` | `{engine, voice, text?}` → a cached one-line sample. If the engine fell back, the response says so |

## Files

| Method | Path | |
|---|---|---|
| GET | `/assets/{project_id}/{path}` | A project's file, after an ownership check and a traversal check; answers `Range` requests so the player can seek |

---

## Progress over WebSocket

`WS /ws/progress/{project_id}`: authenticated by the same cookie on the
handshake; a stranger is closed with code `1008`.

On connect the server replays the project's current job from its first event,
then streams new ones as they are written (it reads `job_events` rows every
0.4 s), with a heartbeat every 30 s. Clients de-duplicate by event `id`, so a
reconnect is seamless.

```json
{"type": "snapshot", "data": {"project_id": "…", "job_id": "job_…", "kind": "render", "status": "running",
  "phase": "video", "progress": 0.6, "message": "Generating images + composing video", "recent_events": […]}}
{"type": "event", "data": {"id": 812, "phase": "storyboard", "status": "frame",
  "message": "Scene 2 drawn: The recipe", "progress": 0.73,
  "payload": {"scene_id": "scene_2", "preview_url": "/assets/…/scene_2.png"}}}
{"type": "heartbeat"}
```

| `phase` / `status` | When |
|---|---|
| `story` / `started`, `complete` | Script being written, then ready |
| `storyboard` / `script` | The script exists: payload has scenes, cast and image budget |
| `storyboard` / `frame` | One preview is drawn: payload has `scene_id`, `preview_url` |
| `audio`, `video` / `started`, `complete` | Render phases |
| `edit` / `understood` | What a change was taken to mean: payload has `summary` |
| `edit` / `step` | Each edit step, in words ("Recording the voices") |
| `complete` / `complete` | Done: payload has the new `version` |
| `error` / `failed` | Failed: `message` says why |

## Example: a film from the command line

```bash
B=https://139-185-59-132.sslip.io
curl -c jar -H 'content-type: application/json' \
     -d '{"email":"me@example.com","password":"…"}' $B/api/auth/login
curl -b jar -H 'content-type: application/json' \
     -d '{"prompt":"A lighthouse keeper befriends a stranded whale","scene_count":3,"target_duration_s":24}' \
     $B/api/pipeline/plan
# → {"project_id":"20261004_…","job_id":"job_…","status":"queued","websocket":"/ws/progress/20261004_…"}
curl -b jar -X POST -H 'content-type: application/json' -d '{}' $B/api/pipeline/render/20261004_…
curl -b jar $B/api/pipeline/film/20261004_…
```
