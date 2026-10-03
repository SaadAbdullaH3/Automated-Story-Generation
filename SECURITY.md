# Security

How the product protects accounts, films and the server it runs on, what it
deliberately does not do yet, and how to report a problem.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** (the repository's
*Security* tab → *Report a vulnerability*) rather than a public issue. Include
what you did, what you expected, and what happened; a fix and an advisory
follow as soon as it is confirmed.

## Threat model in one table

| Who | Could try to | Stopped by |
|---|---|---|
| Anyone on the internet | Read someone's films, guess project ids, drive the API | Every `/api/*` router requires a session; assets are an authorised route, not a static folder; another user's project answers `404` |
| Anyone on the internet | Guess or enumerate passwords | argon2id; identical answers and timing for "wrong password" and "no such account"; 8 failures lock an account for 15 minutes |
| A malicious site the user visits | Ride the user's session (CSRF), or log them into the attacker's account (login CSRF) | `SameSite=Lax` cookie; no cross-origin requests allowed by default; the GitHub OAuth `state` is bound to an HttpOnly cookie and checked before the code is spent |
| Whoever registers an email first | Take over an account through "Sign in with GitHub" | GitHub identities never join an existing account by email |
| A database leak | Replay sessions, crack passwords cheaply | Only the SHA-256 of each session token is stored; passwords are argon2id |
| Model output or a hostile prompt | Exploit the media pipeline with crafted input | ffmpeg runs as an unprivileged user in a container; images are decoded and verified before ffmpeg sees them; every shot has a time limit |

## Authentication

- **Sessions, not JWTs.** The cookie holds a random token; the database keeps
  its SHA-256. Sessions last 14 days. Signing out deletes the row; disabling
  an account ends its sessions immediately; changing a password ends every
  other session.
- **Cookie flags.** `HttpOnly`, `SameSite=Lax`, and `Secure` whenever the
  request arrived over HTTPS (forced on in production with `COOKIE_SECURE=1`).
- **Passwords.** argon2id via argon2-cffi, 10 to 1,024 characters. Unknown
  accounts get a dummy verify so response time does not reveal who is
  registered.
- **Sign-ups.** The first account becomes the administrator; after that
  sign-ups are closed unless `ALLOW_SIGNUPS=1`. `python main.py users` is the
  recovery path (there is no password reset by email).
- **GitHub sign-in.** Authorization-code flow. Identities are keyed by
  GitHub's numeric user id, not the renameable login. Only a verified primary
  email is accepted. GitHub-only accounts store an unusable password hash, so
  there is nothing to guess.

## Authorisation

- **One place a request becomes a user** — `auth/deps.py`. Routers declare
  `Depends(require_user)` where they are mounted, so an endpoint added later is
  protected the moment it exists; project routes take `require_project`.
- **`404`, not `403`,** for a project the user does not own.
- **Files** are served by `/assets/{project_id}/{path}` after an ownership
  check; the resolved path must stay inside that project's folder, so `../`
  cannot walk out of it. Range requests are supported for seeking.
- **The progress WebSocket** authenticates on the handshake and closes with
  code `1008` for anyone else.
- **Admin-only:** listing, creating, promoting and disabling users. An admin
  cannot demote or disable themselves.

## Transport and the server

- HTTPS by **Caddy** with an automatic Let's Encrypt certificate; plain HTTP
  redirects. Headers: `Strict-Transport-Security`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `X-Frame-Options: DENY`, and the `Server` header removed.
- The API publishes **no port** in production — it is reachable only through
  Caddy, which is what makes trusting forwarded headers safe.
- **CORS** allows no cross-origin request unless `CORS_ORIGINS` names one; the
  web app is served from the API's own origin.
- Containers run as **uid 10001**, never root. The VM's firewall opens only
  22, 80 and 443 (TCP, plus UDP for HTTP/3), in both Oracle's security list
  and the VM's iptables.

## Secrets

- Credentials live in `.env` (gitignored; mode 600 on the server). Which model
  does what lives in `config/providers.yaml`, which holds no secrets.
- The test suite sets `PIPELINE_SKIP_DOTENV=1` and scrubs every credential, so
  tests never read the real `.env` or spend real quota. CI needs no secrets.
- The Kokoro voice model is downloaded with its SHA-256 pinned and written via
  a temporary file, so a truncated or tampered download is never used.
- Paid providers need an explicit opt-in beyond their key (`VIDEO_BUDGET_OK`
  for Veo), so adding a key cannot start a bill by itself.

## Data

- Films are private to their owner. Backups (database dump + film snapshots)
  stay on the VM and in a private R2 bucket; restores are tested end to end.
- The version history keeps every version's files. Deleting a film's history
  is not yet exposed in the UI.

## Not done yet

| Gap | Mitigation today |
|---|---|
| No two-factor authentication | Strong hashing, lockout, GitHub sign-in available |
| No email verification or reset | Sign-ups closed by default; admin CLI resets passwords |
| No per-route rate limiting beyond login lockout | Accounts are invitation-only; provider quotas cap spend at $0 |
| No Content-Security-Policy header | Same-origin app with no third-party scripts; fonts self-hosted |
| Python dependencies are minimum versions, not a lock file | npm uses a lock file; pinning Python is on the [roadmap](docs/ROADMAP.md) |
| Sessions can't yet be revoked per device from the UI | The API lists them; sign-out and password change revoke |
