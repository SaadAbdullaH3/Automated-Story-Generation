# Testing and quality

**388 tests**, fully offline, on Linux and Windows in CI, plus the checks
that only mean something inside the production image, run inside it.

## How the suite is built

**Offline by construction.** `tests/conftest.py` forces `LLM_PROVIDER=mock`
(the deterministic template writes scripts, the keyword classifier reads
edits) and `PROVIDER_IMAGE=placeholder`, renders voices as silence of the
right length, points every data path at a temporary directory, sets
`PIPELINE_SKIP_DOTENV=1` and scrubs every credential. No test can read the
real `.env` or spend real quota, and CI needs no secrets.

**Through the real path.** Behaviour tests go through `plan`, `render`,
`EditAgent.edit` and the HTTP API (the same calls the product makes) and
render real (small: 320×180 at 12 fps) films with ffmpeg. Twice a unit test
passed on a code path nothing used: preview reuse was tested by drawing the
storyboard directly at a matching size, while the real `plan` drew it at
another; scene tone was tested on a shot kind the agent never produces. Both
tests were rewritten to go through the entry point, and both found the bug.

**Proven to fail without the fix.** A bug fix lands with a test, and the test
is run against the old code to show it fails for the right reason. Examples
from the last milestone:

| Test | On the old code |
|---|---|
| Two shots render at once | `assert 1 == 2`: one shot at a time |
| A Kokoro voice, then System voices, leaves the process alive | the subprocess exits with code 1: `Error processing file '…/phontab'` |
| Translation names the provider that answered | `llm:gemini` instead of `llm:groq` |
| Six processes boot against one fresh database | 5 of 6 crash creating the schema |
| Urdu burns as real text | 19 of 20 glyph shapes are hollow boxes |

## Running it

```bash
python -m pytest -q                                   # everything, ~6 min on a laptop
python -m pytest tests/unit/test_m8_edit_jobs.py -q   # one area

# The same suite against a real Postgres
docker run -d --name storygen-pg -e POSTGRES_USER=storygen -e POSTGRES_PASSWORD=storygen \
  -e POSTGRES_DB=storygen -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://storygen:storygen@localhost:55432/storygen python -m pytest -q

# Object storage against a real S3 server (or R2 with real credentials)
python -m moto.server -p 5111 &
TEST_S3_ENDPOINT=http://127.0.0.1:5111 python -m pytest -k real_s3

# Inside the production image (fonts, drivers, voice libraries)
docker run --rm -e PIPELINE_SKIP_DOTENV=1 storygen:local python -m pytest -q \
  tests/unit/test_m9_subtitle_fonts.py tests/unit/test_m9_schema_race.py tests/unit/test_m9_system_voices.py
```

## What is covered

| Area | Tests | Highlights |
|---|---|---|
| Script and storyboard | 35 | Template length within 15% of target; every speaker is in the cast; appearance locks persist; the plan streams the script before any frame; previews are reused by the render |
| Voices and sound | 40 | Ducking and loudness; engine availability and its reasons; scene-only voice overrides that survive later edits; the espeak crash reproduced in a subprocess |
| Picture, sync and subtitles | 105 | Final frame count equals the timeline; every line starts on a cut; camera moves follow shot and tone; pan smoothness; subtitle fonts burn every script without missing-glyph boxes; shots render in parallel and still cut each scene from its own shots |
| Editing and versions | 71 | 18 phrasings classified offline, plus scope and parameter extraction; closed vocabulary rejects invented intents; edits as jobs, one per film at a time; a failed edit restores the saved files; revert is byte-identical and saved as a new version |
| Providers | 21 | Chains, credential filtering, fall-through, retries, mock fallback, the answering provider is the one reported |
| API, jobs and accounts | 86 | Atomic claims, heartbeats, requeue, cancellation; sessions, lockout, timing-uniform login, 404 for others' projects, asset traversal; GitHub OAuth against a fake GitHub (state, email refusal, disabled accounts) |
| Deployment and storage | 29 | Local and S3 assets; worker liveness; Kokoro download verified by SHA-256; first-boot schema race; off-machine backup push and pull against moto |
| End to end | 1 | Prompt to MP4 in mock mode |

## Continuous integration

`.github/workflows/tests.yml` runs on every push and pull request:

| Job | What |
|---|---|
| `ubuntu-latest · python 3.11` / `3.12` | `compileall` (a syntax error in a file no test imports still fails), then the suite with ffmpeg and the Noto fonts |
| `windows-latest · python 3.11` | The suite on the development OS (fonts and path separators differ) |
| `web · build and typecheck` | `npm ci` from the lock file, `next build`, `tsc --noEmit`, and a check that the export contains the pages FastAPI serves |
| `image · build and run` | Builds the production image (cached), imports the database, S3 and voice drivers, runs the in-image tests, starts the whole stack on a fresh database and fails on any container restart |

The first CI run found a real bug the laptop could not: choosing Kokoro on a
machine without its optional packages raised `ModuleNotFoundError` before the
check that explains what to install.

## Measurement, not assertion

Some claims cannot be unit-tested, so they are measured and the method is
kept:

- `scripts/benchmark.py` runs fixed prompts and reports time per phase, length
  against target, frame-exact sync, lines on cuts, and which provider served
  each image, so a silent downgrade shows up as a number. Results:
  [BENCHMARK.md](BENCHMARK.md).
- Camera smoothness by phase correlation on real shot clips (worst
  frame-to-frame jump 0.19 px).
- Audio ducking measured per frequency band (~6 dB under speech).
- Speaking rate calibrated against real voices (2.2 words/second), after the
  first benchmark found films 17% long.
- Render speed on the production VM, sampled for CPU every 2 s, before and
  after shots ran in parallel (188 s → 136 s).

## Verified against real services

| Claim | How it was verified |
|---|---|
| Postgres path | The whole suite against a real Postgres 16; 40 jobs claimed by 4 processes at once with no duplicates |
| Cloudflare R2 | 31 assets uploaded by the worker and fetched back byte-identical; nightly backups pushed and a full "machine lost" restore from the bucket |
| Live models | Gemini, Groq and Cloudflare through `python main.py providers --check`; four provider bugs found and fixed |
| GitHub OAuth | The registered app's credentials checked against GitHub's token endpoint, then the full round trip by a person |
| Production | Two real films rendered on the VM, timed by stage |
