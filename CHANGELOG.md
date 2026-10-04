# Changelog

The project is built in milestones. Each ends with a pull request, CI green,
and a measured result; the detailed engineering log for every milestone is in
[CLAUDE.md](CLAUDE.md).

## Unreleased: Dastango

- A name and a mark. *Dastango*, a teller of dastans; the logo is a Mughal
  arch (the storyteller's niche, a stage, a screen) around the teller's
  lamp, in the interface's one accent colour. Favicons, an iOS icon and a
  link-preview card are generated from the same vector.
- A user guide inside the product at `/docs`: twelve public pages with a
  sidebar, a filter (`/` to focus it), an "On this page" list and
  previous/next links. Every edit phrasing it shows was run through the
  classifier first. FastAPI's interactive reference moved to `/api/docs`.
- The repository's documentation for engineers (architecture, agents,
  frameworks, decisions, API, testing, security).

## M9: Deployed (2026-10-03)

Live at https://139-185-59-132.sslip.io on Oracle Cloud's Always Free ARM VM.

- Production image built and checked in CI: multi-stage (Node builds the UI,
  Python runs it), runs as an unprivileged user, 1.68 GB (265 MB of unused
  OpenCV and MoviePy removed), arm64 and amd64.
- Caddy for HTTPS, a tested `setup-vm.sh`, nightly backups to the VM and
  Cloudflare R2, `restore.sh` proven by a full "machine lost" drill.
- Found by building and running it: Urdu subtitles rendering as empty boxes in
  the container (fonts are now chosen per language and checked by counting
  hollow glyphs), a first-boot schema race that crashed 5 of 6 processes, a
  worker that could never report healthy, an unverified model download.
- Found by using it: previewing "System voices" after a Kokoro sample killed
  the API (espeak calls `exit(1)`); shots rendered one at a time left a core
  idle (now in parallel, **6 min 38 s → 4 min 52 s** for a 43 s film).

## M8: The creator interface (2026-10-02)

- New UI: Next.js 16 static export served by FastAPI. The storyboard streams
  in live; the player has scenes as chapters; edits and versions in the
  creator's own words.
- Edits became jobs, one per film at a time, restoring the saved files on
  failure. A revert no longer records the wrong version number.
- Edits are a closed vocabulary: a live model had invented an intent and the
  edit "succeeded" as a no-op; now every real phrasing maps and "make it
  better" is refused.
- Scene-only voices survive later edits. Sign in with GitHub, never joining
  accounts by email.

## M7: Ship polish (2026-10-01)

- Continuous integration on Ubuntu and Windows; its first run found a bug the
  development laptop could not. A film library. A front page about the
  product.

## M6: Camera moves that mean something (2026-10-01)

- Pans brought back without shiver by computing moves at 3× and scaling down:
  worst frame-to-frame jump 0.19 px against 0.80 px before.
- Moves chosen by the shot's job and the scene's tone; grades from the story's
  visual style. Real motion became a provider chain, free first, Veo opt-in.

## M5: Accounts (2026-10-01)

- Sessions in the database (hashed tokens), argon2id, lockout, uniform login
  timing, router-level authorisation, `404` for other people's projects,
  authorised asset streaming, an authenticated WebSocket, no wildcard CORS.

## M4: Production architecture (2026-10-01)

- Runs became database rows claimed by workers: they survive restarts, can be
  watched from any process, and can be cancelled. SQLite or Postgres through
  one SQL layer; verified with 40 jobs and 4 concurrent workers.
- Optional S3/R2 asset publishing, verified against real R2. A voice picker
  with samples.

## M3: Quality (2026-09-22)

- Music ducks under dialogue; masters normalised to -16 LUFS. The storyboard
  step (plan, review, render). Consistent characters through appearance locks
  and fixed seeds. A benchmark that found films running 17% long, fixed by
  calibrating the speaking rate.
- Then, from watching the first real film: subtitles burned in (players
  ignore MP4 tracks), a visual style per story instead of one look for all,
  and Kokoro as an open-source voice.

## M2: Models as configuration (2026-09-21)

- `config/providers.yaml`: every role has a chain with automatic fallback.
  Gemini, Groq, OpenRouter, Ollama; Cloudflare FLUX images; parallel calls
  bounded per provider; retries that halved the images lost to placeholders.

## M1: Correctness (2026-09-19)

- One timeline for audio, video and subtitles: the voice had led the picture
  by up to 6.1 s; now the film is frame-exact. Films had come out twice their
  requested length; now within a few percent. Subtitles that were silently
  untranslated now fail loudly and are skipped.

## M0: Baseline (2026-09-18)

The project started from a team prototype in spring 2026. M0 measured it
before changing anything: 52 tests; one 30-second film took 779 s (630 s of
it spent on 15 serial image calls) and came out 60.5 s long, with the voice
drifting 2.4 s to 6.1 s ahead of the picture.
