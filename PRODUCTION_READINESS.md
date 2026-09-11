# Production Readiness Assessment — AI Interview Simulator

Assessment date: 2026-08-28
Last updated: 2026-09-11 (migration cleanup, auth hardening, Docker/CI, config precedence + DSA execution fixes, spend quotas, secret rotation + Judge0 hardening, WebSocket metering, token revocation, housekeeping, CORS config + structured logging + error tracking, Prometheus metrics, frontend redesign)
Scope: full repository (`backend/`, `frontend/`, `tests/`, config, Docker/Judge0, scripts, data assets).
Method: direct source inspection (the `code-review-graph` MCP server failed to connect — `CONNECT_TIMEOUT` — so this pass used manual file reads, `git ls-files`, and targeted greps instead of graph queries), followed by running the backend test suite and the frontend build.

> **Status note.** The original assessment was static-only — no code was run. A
> follow-up pass finished the Supabase→MongoDB migration cleanup and, in doing
> so, found two things static reading had missed: the backend could not start at
> all, and authentication would have broken in any production build. Both are
> fixed. Later passes hardened authentication and added Docker/CI — the latter
> uncovering two more live bugs (the voice-interview TTS path raised `NameError`
> on every spoken question, and invalid cached HR questions were never
> regenerated). A final pass fixed `.env`-vs-environment precedence and repaired
> the DSA C++/Java round, which was broken by three chained bugs whose symptom
> (a timeout) actively pointed away from the cause (a discarded compile error).
> Findings resolved since the first pass are marked **[RESOLVED]** below;
> §17–§20 record what changed.

---

## 0.5 Corrections to the original assessment

Two findings in the first pass were materially wrong or incomplete, because that
pass never executed anything:

- **"The app runs, but has architectural gaps" — wrong.** `import backend.main`
  failed outright with `ModuleNotFoundError: No module named 'pymongo'`. The
  migration added `pymongo`, `bcrypt`, and `edge-tts` to `requirements.txt`, but
  the virtualenv was never re-synced. The backend could not start. (Fixed by
  installing the declared dependencies; `scripts/start_backend.ps1` would also
  have done this on its next run, since its requirements hash stamp was stale.)
- **"21 test files exist" — misleading.** Ten of them failed at import, so the
  suite only ever collected 72 tests. It now collects 162, all passing.
- **"4 pre-existing failures, unrelated to the migration" — half wrong.** Two of
  those four were real production bugs, not stale tests (§19). Carrying a failing
  test forward as "known noise" is how both stayed hidden.

The lesson worth carrying: a static read of this repository overstates its
health. Run the suite and the build before trusting any assessment of it.

---

## 0. Credential exposure — status

`.env` at the repo root contains live credentials in plaintext. It is git-ignored
and was **never committed** to any reachable history (verified via
`git log --all --full-history`), so despite the GitHub remote these were never
pushed. All four were read into an assistant session during the assessment, so
they should be considered exposed to wherever that transcript is stored.

| Credential | Status |
|---|---|
| `AUTH_JWT_SECRET` | **Rotated** (§22.1), verified — tokens signed with the old secret are now rejected |
| `GROQ_API_KEY` | **Not rotated — accepted risk.** Owner elected to keep the existing key. |
| `ELEVENLABS_API_KEY` | **Not rotated — accepted risk.** As above. |
| `QUIZ_API_KEY` | **Not rotated — accepted risk.** As above. Used only by `scripts/import_questions_from_apis.py`, not at runtime. |

The three retained keys are a deliberate decision by the project owner, recorded
here rather than left looking like an outstanding task. Revisit it if any of them
gains billing exposure or moves beyond a development tier, and rotate before this
project is handed to anyone else.

Separately, and **not** covered by the decision above: the Judge0 Redis and
Postgres passwords in commit `1f249f5` *were* committed and pushed (§22.3). The
repository is private, which bounds the exposure, but those two are a different
class from the `.env` keys — they are in git history rather than only on disk.

---

## Executive summary

This is a working prototype with genuinely good bones in places (the DSA safety gate, the resume-upload validation, optimistic concurrency on session state, the resume-parsing pipeline), but it is **not production-ready**.

**[RESOLVED] The Supabase→MongoDB migration is now complete.** The migration was
finished in the code but had never been carried through the docs, comments,
tests, scripts, or the frontend auth client. That left the README describing the
wrong backing services, ten test modules failing to import, three scripts broken,
and — most seriously — an auth client that only worked under the Vite dev proxy.
All of this is fixed; see §17.

**[RESOLVED] Authentication has been hardened** — rate limiting, per-account lockout, a password policy, the signup race, user-enumeration resistance, and a test suite where there was previously none (§18), plus server-side token revocation so signing out actually invalidates a token (§24). What remains open in auth is email verification and password reset, both of which need an email provider to be chosen first.

**[RESOLVED] A deployment story now exists** — Dockerfiles for both services, a compose stack, a POSIX startup script, and a CI pipeline (§19). Both images build (847 MB backend, 74.7 MB frontend) and the full stack was exercised end to end: health checks, a signup/login round-trip against containerised MongoDB, Judge0 reachable from the backend container, rate limiting returning 429 with `Retry-After`, and secrets confirmed absent from the image. Running it turned up three further bugs that syntax checking could not have found (§19). CI itself has not yet run.

**[RESOLVED] LLM and compute spend is now bounded** — per-user hourly quotas on resume parsing, role matching, DSA execution, and voice (§21), extended to the interview WebSocket (§23). Every path that spends money on Groq, ElevenLabs, or Judge0 is now metered per account.

**[RESOLVED] CORS origins, structured logging, error tracking, and metrics are
now in place** — allowed frontend origins move through `CORS_ALLOWED_ORIGINS`
instead of a hardcoded localhost list, every log line is JSON with a
request-id that correlates one HTTP request across every module that logs
during it, a catch-all exception handler turns a genuinely unhandled
exception into a structured 500 instead of an opaque one, Sentry initializes
itself when `SENTRY_DSN` is set and stays fully inert otherwise (§26), and
`/metrics` now exposes request counts/latency by route plus Groq/Judge0 call
outcomes (§27).

The largest remaining risks are:

1. **Secrets still need rotating** (§0) — unchanged, and the only item outstanding since the first assessment.
2. **[RESOLVED] Judge0 hardening** — authentication is now enabled and all hardcoded infrastructure passwords are parameterised (§22). **Still open:** Judge0 credentials were committed to the repository and are on the GitHub remote; they need rotating, and removing them from history requires a force-push (§22).
3. **No email verification or password reset** (§4) — both blocked on choosing an email provider. Token revocation is done (§24).
4. **CI has still never actually run.** The workflow now has real observability work to validate (§26), and remains unverified on GitHub's own runners — only ever run locally.

None of this is unusual for a project at this stage — but it means "production" is a real project, not a config change.

---

## 1. Architecture overview

```
frontend/ (React 18 + Vite, React Router, Monaco editor, VAD-web)
   |
   |  REST (fetch, bearer JWT) + WebSocket (voice interview)
   v
backend/ (FastAPI, single process, uvicorn)
   |-- api/            route handlers (auth, resume, assessment, interview, dsa, voice, report, workflow, ws)
   |-- nlp/            Groq-backed resume parsing, question generation, answer evaluation, feedback,
   |                    sentence-transformers role matching, NER (spaCy/regex), linguistic analysis
   |-- dsa/            Judge0-backed code execution, static safety gate, problem bank, scoring engine
   |-- voice/          faster-whisper STT, Piper/ElevenLabs/edge-tts TTS, energy-based VAD
   |-- database/       MongoDB repository (pymongo), domain query helpers, error/enum types
   `-- config.py       env-backed settings, cached via lru_cache, loaded from root .env

External dependencies: MongoDB (self-hosted/Atlas), Groq API (LLM), Judge0 (self-hosted code execution),
ElevenLabs/Piper/edge-tts (voice), sentence-transformers (local semantic model, warmed at startup).
```

Auth is a self-issued JWT (HS256, one shared secret, 24h expiry, no refresh/revocation) verified per-request via `backend/api/auth.py`. Session ownership is enforced consistently at the route layer via `ensure_session_access()` — this part is solid and used uniformly across `resume`, `dsa`, `interview`, `workflow`, and the websocket handler.

Persistence is entirely MongoDB, with per-collection unique indexes and optimistic concurrency (`state_version`) on the two mutable hot paths — DSA session state and interview round progression — implemented correctly with `find_one_and_update` + version-matched filters.

There is no API gateway, no reverse proxy config, no CDN/static-hosting config for the frontend build, and no containerization anywhere in the app itself (Judge0 has its own Docker Compose bundle, unrelated to the app).

---

## 2. Critical bugs and reliability risks

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| **[RESOLVED]** ~~Signup TOCTOU race producing an unhandled 500~~ | High | `backend/api/routes_auth.py` | `DuplicateKeyError` from the unique index on `users.email` is now caught and reported as the same 409 conflict. Covered by `test_signup_race_surfaces_duplicate_key_as_409_not_500`. | Done | — |
| **[WITHDRAWN — this finding was wrong]** ~~The "local Judge0 bundle" is an empty stub~~ | — | `.local/judge0/judge0-v1.13.1.zip` | The zip is the **legitimate** official Judge0 v1.13.1 release: it contains `judge0.conf` (12 KB) and `docker-compose.yml`, which is all a Judge0 CE release ships — the images come from Docker Hub. The original claim came from misreading a `file` output line that described the archive's *directory entry* as "uncompressed size 0", not its contents. Verified by extracting it and diffing against the checked-in copy: byte-identical apart from passwords that ship blank upstream. | No action — do not delete this file | No |
| `models/*.pth` "research checkpoints" are tiny placeholder files, not real trained models | Medium | `models/job_matcher_model.pth` (136K), `models/parser_model.pth` (40K), `models/t5_model.pth` (4K) | These are legitimate pickled PyTorch state dicts but far too small to be the fine-tuned models the notebooks in `notebooks/` describe training. If anyone tries to load and use them at runtime expecting real weights, results will be meaningless. README does say they're "not executed by the live interview runtime," so this is currently inert, but it's misleading if kept for a portfolio/demo audience | Either regenerate real checkpoints or clearly label these as placeholder/demo stubs; do not let them silently look legitimate | No (currently unused at runtime) |
| No app-level readiness/liveness distinction; Mongo connection is only established lazily on first repository access | Medium | `backend/database/mongo_client.py:810-816`, `backend/main.py` lifespan | `get_repository()` is a lazy singleton — the app can report `/health: ok` and accept traffic before ever confirming MongoDB is reachable, and the first real request pays the connection-establishment cost (and fails opaquely if Mongo is down) | Establish (and healthcheck) the Mongo connection during the FastAPI `lifespan` startup, and add Mongo status to `/health` | Yes |
| Debug `print()` leaking into a request path instead of structured logging | Low | `backend/api/routes_dsa.py:332` (`print("!!! JUDGE0 ERROR:", str(exc))`) | Bypasses whatever logging/observability pipeline is configured in production (stdout capture, log levels, redaction) | Replace with `logging.getLogger(__name__).error(...)` | No, but trivial to fix |

---

## 3. Security risks

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| Live secrets sitting in plaintext `.env` in the working tree | Critical | `e:\interview_simulator\.env` | Groq key, JWT signing secret, ElevenLabs key, and a "QUIZ_API_KEY" are all live-looking values in a plaintext file. Not committed to git, but exposed to anyone with filesystem access, and now exposed via this session's transcript | Rotate all keys immediately (see §0); for production use a secrets manager (AWS Secrets Manager, Vault, Doppler, etc.), not a `.env` file on disk | Yes |
| DSA sandbox safety net is a static blocklist, not real sandboxing | High | `backend/dsa/code_executor.py:56-112, 418-464` | Python safety is AST-based name/attribute blocklisting (`os`, `subprocess`, `eval`, `exec`, `open`, etc.); C++/Java safety is a lowercase substring search over forbidden tokens (`system(`, `popen(`, `ProcessBuilder`, etc.). Both styles are well-known to be bypassable (indirect references via `getattr`, string concatenation to dodge substring matches, reflection in Java, macro tricks in C++). This is a reasonable defense-in-depth layer, but it must not be the only thing standing between untrusted user code and the host — that has to be Judge0's own `isolate` sandbox (cgroups/seccomp) | Verify and document that Judge0 workers run with `isolate`-based sandboxing and resource limits enabled (the compose file shows `privileged: true` — see Docker section below, this is a real concern), and treat the Python-side blocklist as a UX/early-rejection layer only, not a security boundary | Yes, verify before allowing untrusted users to submit code |
| Judge0 containers run `privileged: true` with authentication disabled | High | `docker-compose.judge0.yml:38, 64, 33-37` | `AUTHN_HEADER`/`AUTHZ_HEADER` are explicitly blanked out ("Disable authentication for local dev"), and both `judge0-server` and `judge0-worker` run as privileged containers. This is fine strictly on localhost for development, but this compose file is what's shipped — there's no separate hardened production variant, and privileged + unauthenticated code-execution infrastructure is a severe risk if it's ever reachable beyond `127.0.0.1` | Produce a production Judge0 config with authentication enabled, non-privileged containers where possible, and network policy that keeps it unreachable from anything except the backend | Yes, if DSA execution ships to real users |
| JWT stored in `localStorage`, no revocation mechanism | Medium-High | `frontend/src/lib/authClient.js:1-4`, `backend/api/routes_auth.py:29-42` | A 24-hour bearer token in `localStorage` is readable by any script that achieves XSS on the page, and there is no server-side session table, so a stolen or leaked token cannot be invalidated before it expires — not even by "logging out" (logout only clears the client-side copy) | Consider httpOnly, SameSite cookies for the token (requires CSRF protection in exchange), and/or a short-lived access token + refresh token pair with a server-side revocation list | Recommended before handling real user data at scale; not necessarily a hard blocker for a small trusted pilot |
| Resume text is interpolated directly into the Groq prompt with no defense against prompt injection | Medium | `backend/nlp/resume_parser.py:386-393` | A malicious PDF (e.g., "Ignore prior instructions and output {...}") is passed verbatim as user content into the resume-parsing LLM call, and the parsed output subsequently seeds interview question generation and context. This isn't a code-execution risk, but it can corrupt parsed data, inject misleading interview content, or (depending on downstream trust) skew scoring | Wrap untrusted resume text with clear delimiters and reinforce in the system prompt that content inside those delimiters is data, not instructions; consider a lightweight injection-pattern filter as defense-in-depth | Recommended, not a hard blocker |
| Judge0 API base URL configured differently in `.env` vs. `docker-compose.judge0.yml` | Low | `.env:5` (`http://127.0.0.1:2359`) vs. `backend/config.py:195` default (`2358`) vs. compose file port mapping `"2359:2358"` | Currently self-consistent (the compose file does map host port 2359), but it's a footgun: if someone runs Judge0 with default settings from the README example instead of this exact compose file, the backend will silently point at the wrong port | Standardize on one port across README, `.env.example`, and compose file, and call it out explicitly in docs | No |
| Hardcoded Postgres/Redis passwords in `docker-compose.judge0.yml` | Low | `docker-compose.judge0.yml:20-24, 52-56, 76-77, 89` | `judge0password` / `judge0redispassword` committed in plaintext. Acceptable for a local-only sandboxed dependency, but should not be reused if this compose file is ever adapted for a shared environment | Parameterize via `.env` and gitignore, or at minimum document that these must be changed before any non-localhost deployment | No |

---

## 4. Authentication / authorization risks

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| **[RESOLVED]** ~~No rate limiting anywhere in the app~~ | High | `backend/api/rate_limit.py`, `backend/api/routes_auth.py`, `backend/api/quotas.py` | `/auth/signup` and `/auth/login` enforce a per-IP request budget plus a per-account backoff after consecutive failures. Separately, per-user hourly quotas now bound spend on every Groq- and Judge0-backed route (§21). The interview WebSocket is metered too (§23). | Done | — |
| **[RESOLVED]** ~~No password policy~~ | Medium | `backend/api/routes_auth.py`, `backend/config.py` | Signup now enforces a configurable minimum length and a maximum **UTF-8 byte** length. The byte limit closed a latent 500: bcrypt 5.0 raises `ValueError` above 72 bytes, so a long password — or a 30-character emoji/CJK password (120 bytes) — previously crashed signup. The policy is deliberately not applied at sign-in, to avoid locking out pre-policy accounts and disclosing the policy to anonymous callers. | Done | — |
| **[RESOLVED]** ~~User enumeration via sign-in~~ | Medium | `backend/api/routes_auth.py` | Unknown-address and wrong-password now return an identical status and detail, a dummy bcrypt comparison equalises response timing when no account exists, and the lockout is keyed on the *submitted* address so attempts against non-existent accounts throttle identically. Keying it on a resolved account would have turned the lockout itself into an enumeration oracle. | Done | — |
| No email verification | Medium | `backend/api/routes_auth.py:45-66` | Signup issues a valid session token immediately with no confirmation that the email address is owned by the requester | Add an email-verification flow before granting full access, or explicitly accept this as a deliberate trade-off for a low-stakes demo app | Depends on threat model — flag for a decision, not silently skip |
| No password reset / account recovery, despite the frontend routing to it | Medium | `frontend/src/App.jsx:653-658` (`handleForgotPassword` — hardcoded "not implemented in custom MongoDB auth" error) | Users who forget their password have no recovery path; this was clearly a working Supabase feature before the auth migration that was never rebuilt | Implement a token-based reset flow (email + time-limited reset token) | Yes, for real users |
| **[RESOLVED, with a scaling caveat]** ~~No brute-force lockout / backoff~~ | Medium | `backend/api/rate_limit.py`, `backend/api/routes_auth.py` | A per-account lockout now applies after `AUTH_LOGIN_MAX_FAILURES` consecutive failures, and follows the account rather than the source address so rotating IPs does not reset the streak. **Caveat:** counters are per-process, so multiple workers multiply the effective limit and separate instances share nothing. Documented in README §7.7; the classes take an injectable clock and a narrow interface to keep a Redis swap cheap. | Back with Redis or enforce at the gateway before scaling out | Resolved for single-process; revisit when scaling |
| WebSocket auth token can be passed as a query parameter | Low-Medium | `backend/api/ws_interview.py:166-180` | `?access_token=<jwt>` in the connection URL is a common and largely unavoidable pattern for browser WebSockets (no custom header support), but it means the token can end up in server access logs, browser history, and Referer headers if not handled carefully | Confirm access logs don't record full query strings for the `/interview/ws/*` path, and prefer the header path (already supported) from any non-browser client | No, but worth a log-scrubbing check |
| **[RESOLVED]** ~~No test coverage for the auth/session layer at all~~ | High | `tests/test_auth.py` (new, 48 tests) | Covers password policy (including the multibyte byte-limit case), signup success/conflict/race, sign-in success and every failure mode, user-enumeration resistance, corrupt-hash and oversized-password handling, JWT verification (valid, expired, forged signature, missing subject, malformed, and `alg: none` downgrade), session-ownership enforcement, the throttling primitives with an injected clock, and an HTTP-layer pass through the real FastAPI stack asserting 422/429/401 wiring and the `Retry-After` header. Verified non-vacuous by mutation testing: removing the duplicate-key handling and disabling the rate limiter each cause the corresponding tests to fail. | Done | — |

---

## 5. API / backend issues

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| CORS origins are hardcoded to localhost, with no environment override | High | `backend/main.py:33-43` | `allow_origins` is a literal list of `127.0.0.1`/`localhost` plus a regex for local dev ports. There is no env-driven way to allow a real production frontend origin — shipping to a real domain requires a code change and redeploy | Move allowed origins into `AppSettings`/env config, with the current localhost list as the dev default | Yes |
| No global exception handler / structured error response contract | Medium | `backend/main.py` | Unhandled exceptions fall through to FastAPI's default 500 (confirmed no leaked tracebacks — good), but there's no consistent error envelope, no request-correlation ID, and no centralized place to log/alert on 5xxs | Add a FastAPI exception handler that logs with a request ID and returns a consistent error shape | Recommended |
| `Judge0Settings`/`GroqSettings` etc. are cached process-wide via `lru_cache` with no way to hot-reload | Low | `backend/config.py:330-343` | Documented behavior (README explicitly says "restart the backend" after `.env` changes), so not a bug, but worth calling out for any ops runbook | Document in deployment runbook; consider a `/admin/reload-config` endpoint if operational flexibility is needed later | No |
| `ENABLE_LOCAL_RESUME_PATH_API` toggles route registration at import time based on env value at process start | Low | `backend/api/routes_resume.py:90-96` | Registering routes conditionally at module import time is a slightly fragile pattern (the flag can't be toggled without a restart, and it's easy to forget this route exists in dev but not prod) | Fine as-is if intentional; just make sure it defaults to `false` in every production `.env` (it does) | No |
| No API versioning | Low | all `backend/api/routes_*.py` | All routes are unversioned (`/auth`, `/resume`, ...). Any breaking API change forces simultaneous frontend+backend deploys | Consider `/v1` prefixing before the API has external consumers | No |

---

## 6. Frontend issues

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| JWT persisted in `localStorage` | Medium (see §3) | `frontend/src/lib/authClient.js:1-4` | Covered above under Security | — | — |
| **[RESOLVED]** ~~`authState.supabaseConfigured` is a hardcoded `true` literal~~ | Low | `frontend/src/App.jsx` | Removed, along with the dead `handleEmailContinue`/`handleForgotPassword` stubs and the `passwordRecoveryReady` flag. `AuthPanel` never accepted any of those props. | Done | — |
| **[RESOLVED — was Critical, not Medium]** ~~Auth client used relative `/api/*` paths~~ | Critical | `frontend/src/lib/authClient.js` | `authClient.js` was the only file calling relative `/api/*`, which resolves solely through the Vite dev-server proxy; all seven pages use the absolute `VITE_API_BASE_URL`. Login/signup would have returned 404 in any production build. Missed entirely by the first pass. | Fixed — now uses the same `VITE_API_BASE_URL` base as every other caller | — |
| No error boundary around the routed page tree | Medium | `frontend/src/App.jsx` (no `ErrorBoundary`/`componentDidCatch` anywhere in `frontend/src`) | A render-time exception in any page (`UploadPage`, `InterviewPage`, `DSAPage`, etc.) will blank the entire app with React's default unhandled-error behavior instead of a recoverable fallback UI | Add a top-level React error boundary with a retry/reload affordance | Recommended |
| No automated frontend tests | Medium | `frontend/` (no test runner configured in `package.json` — no Vitest/Jest/RTL) | Zero regression protection on the UI layer; all correctness currently depends on manual verification | Add Vitest + React Testing Library for at least the workflow-state logic in `App.jsx` and the auth flow | Recommended, not necessarily a blocker for v1 |
| `handleEmailContinue` / `handleForgotPassword` are dead-end stubs that always show an error | Low | `frontend/src/App.jsx:646-658` | Not a bug per se, but if `AuthPanel` exposes these as clickable options, users hit a permanent dead end for password recovery (ties back to the missing backend flow in §4) | Remove the UI entry points until the backend flow exists, or implement both together | Depends on §4 decision |

---

## 7. Database / data consistency issues

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| MongoDB connection has no auth/TLS configured by default | High | `backend/config.py:301-304` (`mongodb://localhost:27017`, no credentials) | Fine for local dev; for any real deployment this must be a credentialed, TLS-enabled connection (Atlas SRV URI or self-hosted with auth enabled) — currently nothing in the codebase enforces or even warns if a production-looking Mongo URI lacks credentials | Add a startup check that warns/fails if `MONGO_URI` has no credentials when running outside a recognized dev mode | Yes |
| Optimistic concurrency is implemented correctly and consistently — worth noting as a strength | — | `backend/database/mongo_client.py:516-569, 570-625, 751-784` | `state_version`-gated `find_one_and_update` calls correctly raise `ConcurrentUpdateError` on stale writes for DSA state, submissions, and interview-round advancement. This is genuinely solid concurrency handling for a MongoDB-backed app | No action needed | — |
| No data-retention / deletion policy for candidate PII (resumes, transcripts, audio) | Medium | `backend/database/mongo_client.py` (resume raw text, interview responses, and derived NLP output are stored indefinitely; no TTL indexes) | Resume text, parsed personal data, and interview transcripts are retained forever with no expiry, and no user-facing "delete my data" path beyond the workflow-reset endpoint (which clears *progress*, not the underlying account/resume record) | Define a retention policy; add TTL indexes or a scheduled purge job if required by your privacy posture | Recommended before handling real candidates' PII |
| No database migrations tooling | Low | `backend/database/` (no migration framework; indexes are created imperatively in `_ensure_indexes()` on every repository init) | Works fine today since MongoDB is schemaless and indexes are idempotent (`create_index` is safe to call repeatedly), but there's no versioned history of schema/index changes as the app evolves | Consider a lightweight migration log if the schema starts changing frequently | No |

---

## 8. AI / LLM integration risks

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| No spend/usage guardrails on Groq calls | High | `backend/nlp/groq_client.py`, all `backend/nlp/*` callers | Combined with the missing rate limiting (§4), there is no per-user or global cap on Groq API usage. A single abusive user (or a scripted loop hitting `/resume/parse-upload`, `/interview/*`, `/dsa/message`) can generate unbounded LLM spend | Add per-user/per-session request quotas and a global circuit breaker; monitor Groq spend with alerting | Yes, before opening to the public |
| Retry logic retries on *any* exception, not just transient ones | Medium | `backend/nlp/groq_client.py:90-108` | `create_chat_completion` retries up to `max_retries` times on any exception except ones matching the rate-limit heuristic — including malformed-request errors (e.g., a bad `response_format`) that will never succeed on retry. This wastes latency and retry budget on deterministically-failing calls | Distinguish retryable (network/5xx/timeout) from non-retryable (4xx/validation) errors before retrying | Recommended |
| Prompt injection via untrusted resume text (see §3) | Medium | `backend/nlp/resume_parser.py:386-393` | — | — | — |
| No fallback when Groq is fully unavailable for question generation / evaluation mid-interview | Medium | `backend/api/ws_interview.py:228-235, 430-439` | `QuestionGeneratorUnavailableError`/`EvaluatorUnavailableError` currently terminate the websocket session with an error (`WebSocketInterviewError`) rather than degrading gracefully (e.g., falling back to a cached question bank or a local heuristic scorer) — a mid-interview Groq outage loses the candidate's in-progress round | Consider a degraded-mode fallback (cached questions, simpler local scoring) for live interview continuity | Recommended, not a hard blocker |
| Groq client is cached via `lru_cache` keyed on `(api_key, api_base_url)` | Low | `backend/nlp/groq_client.py:57-62` | Fine functionally; just means an API key rotation requires a process restart to take effect (consistent with the rest of `config.py`'s caching model) | Document in ops runbook | No |

---

## 9. Error handling and logging gaps

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| No structured logging / log aggregation strategy | High | entire `backend/` | Logging is ad hoc: some modules use `logging.getLogger(__name__)` (e.g., `resume_parser.py`, `routes_resume.py`), one route uses a raw `print()` (§2), and there's no JSON/structured log formatting, no correlation IDs, and no shipping config (stdout only, presumably relying on whatever the process supervisor captures) | Standardize on structured logging (e.g., `structlog` or stdlib logging with a JSON formatter) with request-ID correlation, and decide on a log destination (stdout → log aggregator is fine, just make it explicit) | Yes, for real operations |
| No centralized error monitoring (Sentry or equivalent) | High | entire repo | No error-tracking SDK anywhere in `requirements.txt` or `package.json`. Production incidents will only be visible via manual log inspection | Add an error-tracking integration before launch | Yes |
| Non-fatal errors are frequently swallowed with a bare `except Exception` | Medium | `backend/api/routes_resume.py:334-335` (resume-quality scoring), `backend/nlp/resume_parser.py:281-282` (NER enrichment) | Both are intentionally "best-effort, don't break the main flow" — a reasonable pattern — but the exceptions are only logged as a warning (NER case) or fully silenced (resume-quality case, `except Exception: response["resume_quality"] = None`). Fully silent failure paths make production debugging much harder | At minimum log every swallowed exception, even in the "best effort" paths | Recommended |

---

## 10. Testing gaps

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| Zero coverage on authentication, sessions, and authorization | High | `tests/` (21 files; none for `backend/api/auth.py` or `backend/api/routes_auth.py`) | The single most security-critical code path (login, signup, token verification, cross-user access rejection) has no regression protection | Add auth test suite (see §4) | Yes |
| Zero coverage on the MongoDB repository layer — **now slightly worse** | Medium | `tests/` (no `test_mongo_client.py`/`test_queries.py`) | `MongoRepository` methods (optimistic concurrency, upserts, index creation) are only exercised indirectly through higher-level route tests, if at all — no test runs against a real or mocked Mongo instance. The cleanup pass **removed six tests** from `test_resume_upload_and_session_invariants.py` that asserted Supabase-SDK error-wrapping behavior MongoRepository does not have (`repository.table()` mocks, `"Supabase upsert on ... failed"` strings). They could not be ported — they tested a deleted implementation. Three tests covering still-real invariants were ported. | Add repository-level tests (e.g., `mongomock` or a test Mongo container) covering the optimistic-concurrency conflict paths in `persist_dsa_state`, `append_dsa_submission`, and `advance_interview_question` — these are the highest-value untested paths in the data layer | Recommended |
| No frontend tests (see §6) | Medium | `frontend/` | — | — | — |
| Test suite relies on `unittest discover`, not `pytest`, despite `pytest` being present in the local environment (`.pytest_cache/` exists) | Low | `README.md` §9, root `.pytest_cache/` | Not broken, just inconsistent — suggests ad hoc `pytest` runs happened locally that aren't reflected in documented tooling or `requirements.txt` (pytest isn't a declared dependency at all) | Pick one test runner, declare it in `requirements.txt` (or a `requirements-dev.txt`), and document it | No |
| No CI pipeline running the test suite on push/PR | High | `.github/` (only `copilot-instructions.md`, no `workflows/`) | Tests existing but never running automatically means regressions land undetected. This is not hypothetical: ten test modules had been failing at import, three scripts were broken, and the backend could not start — none of it visible without running the suite. A CI job that merely ran `unittest discover` and `npm run build` would have caught every one of these on the commit that introduced them. | Add a GitHub Actions workflow running backend tests + frontend build on every push/PR | Yes — highest-leverage single item on this list |

---

## 11. Performance / scalability problems

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| Single-process uvicorn with `--reload`, no worker pool | High | `scripts/start_backend.ps1:41` | `--reload` is a dev-only flag (spawns a watcher process and reloads on file change); there's no production launch command using multiple workers (`uvicorn --workers N` or a process manager like gunicorn+uvicorn workers) | Add a production launch script/Dockerfile `CMD` using `uvicorn backend.main:app --workers <n>` (no `--reload`), sized to available CPU | Yes |
| Judge0 execution is synchronous and blocks the request thread while polling | Medium | `backend/dsa/code_executor.py:820-853` | `_poll_submission` does a blocking `time.sleep(1.0)` loop for up to 30 seconds inside a synchronous FastAPI route handler (`run_dsa_code`/`submit_dsa_code` are not `async def`). Under load, this ties up a worker thread per in-flight code execution for up to 30s | Move to `httpx.AsyncClient` + `asyncio.sleep` in an `async def` route, or offload to a background task/queue with a webhook or polling-status endpoint | Recommended before real concurrent DSA traffic |
| Semantic role-matching model is loaded/warmed once at process startup with no documented memory budget | Low | `backend/main.py:26`, `backend/nlp/role_matcher.py` | README notes startup can be slow while "the semantic matching model warms" — this is fine, but there's no documented RAM footprint or fallback if the warmup fails, and no readiness gate: `/health` can be hit before `warmup_semantic_encoder()` has actually completed since routes are registered immediately | Have `/health` reflect actual model-warmup status (it already reports `semantic_matching` — verify it's not misleadingly "ok" mid-warmup) | No |
| No caching layer for repeatedly-fetched, rarely-changing data (question banks, problem bank, role catalog) | Low | `backend/data/assessments/*.json`, `backend/dsa/problem_selector.py` | Likely loaded fresh or cached in-process already for a single-process deployment; worth revisiting only if scaling to multiple processes/instances where cache warmup cost multiplies | Not urgent at current scale | No |

---

## 12. Docker / deployment problems

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| No Dockerfile for the backend or frontend anywhere in the repo | High | (absent — confirmed via repo-wide search) | The only Docker artifacts are for the *Judge0 dependency*, not the application itself. There is no reproducible, portable way to build and run this app in any environment other than "clone repo, set up Python venv + Node by hand on Windows" | Add a backend Dockerfile (Python slim base, install `backend/requirements.txt`, run via gunicorn/uvicorn workers) and a frontend Dockerfile or static-hosting build step (`npm run build` → serve `dist/` via nginx/CDN) | Yes |
| No CI/CD pipeline | High | `.github/` (no `workflows/`) | No automated build, test, lint, or deploy pipeline exists. Every release today is a manual, undocumented process | Add GitHub Actions for test/build on PR, and a deploy workflow once hosting is decided | Yes |
| **[WITHDRAWN]** ~~Judge0 bundle checked into git is a broken/empty stub~~ | — | `.local/judge0/judge0-v1.13.1.zip` | Incorrect finding; see §2. The archive is the genuine upstream release. | No action | No |
| `scripts/start_backend.ps1` is Windows/PowerShell-only | Medium | `scripts/start_backend.ps1` | If production hosting is Linux (likely, for cost and container-friendliness), this script is unusable as-is and there's no equivalent shell script | Add a POSIX equivalent (`scripts/start_backend.sh`) or fold the logic into the Dockerfile `CMD`/`entrypoint.sh` | Yes, if targeting Linux hosting |
| No environment separation (`ENV=development/staging/production`) concept anywhere in config | Medium | `backend/config.py` | A single flat `.env` drives all behavior; CORS origins, log verbosity, and debug behaviors can't differ between environments without editing code | Add an `APP_ENV` setting and branch relevant behavior (CORS origin list, log level, etc.) on it | Recommended |
| No health-check/readiness distinction for orchestration (Kubernetes/ECS-style liveness vs. readiness) | Low | `backend/main.py:54-70` | Single `/health` endpoint conflates "process is up" with "all dependencies (Mongo, Groq, semantic model) are ready" | Split into `/healthz` (liveness) and `/readyz` (readiness, checking Mongo connectivity and model warmup) if deploying behind an orchestrator that uses these | No, until an orchestrator is chosen |

---

## 13. Secrets / configuration problems

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| Live secrets in plaintext `.env` (see §0/§3) | Critical | `.env` | — | — | — |
| **[RESOLVED]** ~~`.env.example` inconsistent with actual required variables~~ | Medium | `.env.example`, `frontend/.env.example` | The root example documented `JWT_SECRET`/`MONGO_DATABASE` while `config.py` reads `AUTH_JWT_SECRET`/`MONGO_DB_NAME` — copying it verbatim produced `ConfigurationError: AUTH_JWT_SECRET environment variable is missing` at startup. Both example files now match what the code actually reads; the frontend one is reduced to the single variable it uses. | Done | — |
| **[RESOLVED]** ~~README `.env` example doesn't match `config.py`~~ | Medium | `README.md` §7.2 | Rewritten with the real variable names, plus an explicit warning that Supabase-era names will fail at startup. | Done | — |
| No secrets-management story beyond `.env` | Medium | entire repo | Acceptable for local dev; for production, plaintext `.env` files are a standing risk (accidental commits, filesystem access, backup leakage) | Use a managed secrets store in any real deployment (see §0) | Yes |

---

## 14. Observability / monitoring gaps

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| No metrics/telemetry (Prometheus, OpenTelemetry, or any APM) | High | entire repo | No visibility into request latency, error rates, Groq/Judge0 call latency or failure rate, or WebSocket session health in production | Add basic request metrics (e.g., `prometheus-fastapi-instrumentator`) and consider OpenTelemetry tracing for the Groq/Judge0/Mongo call chain | Yes, before operating at any real scale |
| No error tracking (see §9) | High | entire repo | — | — | — |
| `/health` reports dependency status for the semantic model and Groq config presence, but not Mongo, Judge0, or TTS/STT | Medium | `backend/main.py:54-70` | An operator checking `/health` would see `"status": "ok"` even if MongoDB is unreachable, since Mongo connectivity isn't checked there (Judge0 and voice each have their own `/dsa/health` and `/voice/health`, which is good, but they're not aggregated) | Extend `/health` to include Mongo connectivity, or clearly document that operators must check all four health endpoints, not just `/health` | Recommended |
| No alerting configuration anywhere (no PagerDuty/Slack webhook, no uptime monitor config) | Medium | entire repo | Fully manual incident detection | Wire up basic uptime/error-rate alerting once hosting is chosen | Recommended before real users depend on this |

---

## 15. Code quality / maintainability problems

| Finding | Severity | File(s) | Why it's a problem | Fix | Block ship? |
|---|---|---|---|---|---|
| **[RESOLVED]** ~~README documents a different backend architecture than what exists~~ | High | `README.md`, `backend/README.md`, `frontend/README.md`, `scripts/README.md`, `.github/copilot-instructions.md` | All rewritten to describe the actual MongoDB + custom-JWT stack. The Supabase SQL/RLS setup section (§7.4) described files that do not exist and was replaced with the real Mongo story (schemaless, indexes auto-created, ownership enforced in the app layer via `ensure_session_access()`). | Done | — |
| **[RESOLVED]** ~~Stale "Supabase" naming and docstrings in code that no longer talks to Supabase~~ | Medium | `backend/api/auth.py`, `backend/database/queries.py`, `backend/api/routes_resume.py`, `backend/api/routes_interview.py`, `backend/api/ws_interview.py`, `backend/nlp/resume_parser.py`, `backend/nlp/feedback_generator.py`, `backend/nlp/question_generator.py` | All updated. The only remaining "Supabase" strings in `backend/` are legitimate: one entry in the resume-technology NER vocabulary (`ner_extractor.py`) and one historical note in the Copilot instructions. | Done | — |
| **[RESOLVED]** ~~Dead `_is_missing_final_reports_error()` gating three degradation paths~~ | Medium | `backend/api/routes_report.py` | The function matched a Supabase-only error string MongoDB can never produce, so it always returned `False` and three graceful-degradation paths were unreachable. Resolved by splitting read from write (see §17). | Done, with new test coverage | — |
| `cleanup_unused_files.py` at the repo root is itself stale/dead | Low | `cleanup_unused_files.py` | Hardcodes absolute Windows paths (`e:\interview_simulator\...`) to files that no longer exist in the repo (`VoiceInterface.jsx`, `ResetPasswordPage.jsx`, `supabaseClient.js`, `data/problems/core_bank.json`) — it's a one-time migration script left behind after it (presumably) already ran. It documents the Supabase-removal migration, which is useful historical context, but as a script it's now a no-op | Delete it (see §16 below for the deletion recommendation) or fold its intent into a changelog entry instead | No, but see cleanup list |
| Heavy, loosely-pinned ML dependencies in `backend/requirements.txt` | Medium | `backend/requirements.txt:2-3, 8, 10, 21` (`accelerate`, `datasets`, `huggingface_hub`, `peft`, `transformers` — all `>=`, unpinned) | Mixed pinning strategy: most deps are exact-pinned (`==`), but the heaviest ones (which transitively pull in large ML frameworks) are open-ended, risking an unreviewed transitive upgrade breaking production. `datasets`/`accelerate`/`peft` in particular look like training-only dependencies (used by the notebooks, not the runtime API) — installing them in the production image adds significant weight for zero runtime benefit | Pin all dependencies exactly for reproducible builds; audit whether `datasets`/`accelerate`/`peft` are actually needed at runtime and move them to a separate `requirements-notebooks.txt` if not | Recommended |
| No linting/formatting configuration for either backend or frontend | Low | repo root (no `ruff`/`black`/`flake8` config; no `eslint`/`prettier` config in `frontend/`) | No automated style/quality enforcement; consistency currently depends entirely on manual discipline | Add `ruff` (or `black` + `flake8`) for Python and `eslint` for the frontend, wired into CI once CI exists | Recommended |

---

## 16. Housekeeping — files worth removing (flagged, not deleted)

Per your instruction not to modify anything yet, here is what I'd recommend deleting once you confirm — nothing has been touched:

| Path | Why it's a candidate for deletion |
|---|---|
| **[DONE — deleted]** ~~`cleanup_unused_files.py`~~ | Verified dead before removal: all four files it targeted are gone, and nothing referenced it. Deleted in §25. |
| ~~`.local/judge0/judge0-v1.13.1.zip`~~ **— DO NOT DELETE, this recommendation was based on a wrong finding** | It is the genuine upstream Judge0 v1.13.1 release, and it is now the reference copy of the pristine `judge0.conf` (which has been untracked because the working copy carries real passwords). See the withdrawal note in §2. |
| **[DONE — untracked, not deleted]** ~~`.kilo/` directory~~ | Only `.kilo/agents/data.md` was tracked; the rest is developer-local tooling (`node_modules/`, `package.json`). Removed from the repository and gitignored, with the on-disk files left intact so the developer's Kilo Code setup still works. Deleting the directory outright would have destroyed local tooling for no repository benefit. |

Not recommending deletion of (despite being large/unusual) because they're referenced by working code or the README's stated demo path:
- `models/*.pth`, `notebooks/*.ipynb` — explicitly documented in README §11 as intentional research/portfolio assets, indexed by `backend/research_assets.py` and surfaced in `/health`. Flagged above (§2) as *misleadingly small*, but that's a "relabel or regenerate" issue, not a "delete" issue — your call.
- `data/clickhouse_sample.jsonl` — a small (4K) fine-tuning-style training example (interview-coaching Q&A pair), consistent with the notebooks' apparent purpose. Not referenced by any runtime code path found in this pass, but low-cost to keep given its size — confirm with whoever owns the notebooks before removing.
- `sample_resume.pdf` — actively referenced by README's demo walkthrough (§7.6, step 3).
- `backend/data/tts_models/en_US-lessac-medium.onnx` (61MB) — the largest tracked file in the repo, but it's the actual Piper TTS voice model used at runtime by `backend/voice/tts.py` when `TTS_PROVIDER=piper`. Keep, though consider Git LFS for it if the repo grows other large binaries.

I did not find any other clearly-dead code paths in the routes/services I read directly. A dedicated dead-code pass (e.g., via the `refactor_tool` once the `code-review-graph` MCP server is reachable, or a `vulture`/`ts-prune`-style static pass) would likely turn up more — that's out of scope for this pass since it required deep read access I didn't have time to give every file in the repo.

---

## Priority punch list (if you can only do 10 things before "production")

1. Rotate the four exposed secrets in `.env` (§0). — *still outstanding*
2. ~~Rewrite the README and fix `.env.example`~~ — **done** (§17).
3. ~~Add rate limiting to `/auth/login`, `/auth/signup`~~ — **done**. Extending it to the Groq/Judge0 routes is still outstanding (merged into item 10).
4. ~~Add a password policy + fix the signup duplicate-key race~~ — **done** (§18).
5. ~~Write an auth/session test suite~~ — **done**: `tests/test_auth.py`, 48 tests (§18).
6. ~~Add Dockerfiles for backend + frontend and a CI pipeline~~ — **done and verified end to end** (§19). Remaining: let CI actually run once on a push.
7. ~~Move CORS origins into env-driven config so a real frontend domain can be deployed~~ — **done** (§26).
8. ~~Add structured logging + error tracking (Sentry or equivalent) + basic metrics~~ — **done** (§26, §27).
9. Confirm/harden the Judge0 sandbox (non-privileged containers, auth enabled) before letting untrusted users execute code through it (§3).
10. Add spend/usage caps on Groq calls per user/session (§8).

Items 3–5 are the "harden auth" workstream and are the natural next step.

---

## 17. Change log — Supabase→MongoDB migration cleanup

The MongoDB migration was already complete in the application code; this pass
carried it through everything the code depends on. Verified by running the
backend test suite and the frontend production build.

**Test suite: 72 → 114 collected tests.** The 4 remaining failures (TTS provider
fallback ×2, HR question regeneration, one websocket test) predate this work and
are unrelated to the migration — they were failing identically before any edit.

### Broken at runtime — fixed

| What | Detail |
|---|---|
| Backend would not start | `pymongo`, `bcrypt`, `edge-tts` declared in `requirements.txt` but absent from the venv. `import backend.main` raised `ModuleNotFoundError`. Installed. |
| Auth broken in production builds | `authClient.js` used relative `/api/*` (Vite-dev-proxy only) while all pages used absolute `VITE_API_BASE_URL`. Now consistent. |
| 10 test modules failed to import | Missing `pymongo`, plus 3 files importing the deleted `backend.database.supabase_client`. |
| 2 scripts failed to import | `smoke_dsa_flow.py` (rename) and `calibrate_confidence_thresholds.py` (real port — it used the Supabase query builder `repo.table().select().order().execute()`, rewritten to pymongo `find().sort()`). |
| Frontend would not build | `tailwindcss` missing from `node_modules`; `npm install` also dropped the stale `@supabase/supabase-js` entry from `package-lock.json`. |

### Report degradation — behavior decision

`_is_missing_final_reports_error()` matched a Supabase-only error string, so it
always returned `False` and three degradation paths were dead. Resolved by
splitting the two directions, which mean different things under MongoDB:

- **Read** (`_load_persisted_report`) — a missing collection in MongoDB yields no
  document, not an error, so "no report saved yet" already surfaces as `None`. A
  raised `DatabaseClientError` therefore means the database is unreachable → 503.
  Degrading only this read would be pointless anyway: if Mongo is down, the round
  collections the report is built from cannot be read either.
- **Write** (`_persist_report_snapshot`) — the snapshot is derived from data just
  read, so failing to *save* it must not block *showing* it. Any
  `DatabaseClientError` now degrades gracefully: the computed report is returned
  with `persisted=False`, the failure is logged, and the user sees a
  recommendation rather than a 5xx.

Four new tests in `tests/test_report_route.py` cover both directions plus the
recommendation path; five existing tests were switched from injecting the
Supabase string to the real `get_final_report → None` condition.

### Documentation and comments

`README.md`, `backend/README.md`, `frontend/README.md`, `scripts/README.md`, and
`.github/copilot-instructions.md` rewritten. Both `.env.example` files corrected
to the variable names `config.py` actually reads. Stale docstrings updated across
eight backend modules.

### Removed

- Six tests asserting Supabase-SDK error-wrapping behavior MongoRepository does
  not have (see §10 — this widens an existing coverage gap rather than creating
  a new one).
- `frontend/.env.local` Supabase variables — dead, and the file contained a stale
  Supabase anon key. Note that a `.env.local.bak` would **not** have matched the
  `.gitignore` patterns (`.env.local`, `.env.*.local`), so no backup was left.
- Dead frontend auth code: `supabaseConfigured`, `handleEmailContinue`,
  `handleForgotPassword`, `passwordRecoveryReady` — `AuthPanel` never accepted
  any of them.

### Still outstanding from §16 housekeeping

All resolved in §25. `cleanup_unused_files.py` was deleted, `.kilo/` was
untracked but left on disk, and the Judge0 zip recommendation was **withdrawn**
— that finding was wrong and the archive is the genuine upstream release (§22.2).

---

*This assessment reflects a point-in-time read of the repository. No files were modified, and the flagged deletions in §16 were not performed — they're recommendations pending your confirmation.*

---

## 18. Change log — auth hardening

Punch-list items 3–5. Test suite: **114 → 155 collected tests**; the 4 remaining
failures are the same pre-existing, migration-unrelated ones.

### New

- `backend/api/rate_limit.py` — `FixedWindowRateLimiter` (sliding window, so a
  caller cannot burst twice by straddling a boundary) and `FailureTracker`
  (per-account lockout). Both are lock-guarded for FastAPI's sync threadpool and
  take an injectable clock, which makes the tests deterministic and a Redis swap
  cheap. Chosen over `slowapi` because the protection that actually matters here
  — per-account lockout — needs custom logic regardless, and this avoids a
  dependency for a ~150-line need.
- `tests/test_auth.py` — 48 tests. See §4 for coverage.

### Fixed

| Issue | Detail |
|---|---|
| Signup TOCTOU race | `DuplicateKeyError` → 409 instead of an unhandled 500. |
| **Latent 500 on long passwords** | bcrypt 5.0 raises `ValueError` above 72 **bytes**. A 30-character emoji or CJK password is already 120 bytes, so signup crashed on input a user could plausibly choose. The policy now measures encoded bytes; sign-in additionally guards `checkpw` so an oversized password or a corrupt stored hash returns 401 rather than 500. Found while probing bcrypt behaviour, not predicted by the original assessment. |
| No password policy | Configurable minimum length + maximum byte length, applied at signup only. |
| User enumeration | Identical status/detail for unknown-address vs wrong-password; a dummy bcrypt comparison equalises timing; lockout keyed on the submitted address. |
| Duplicate accounts by email case | Addresses are now normalised to lowercase. **This needs a one-time data migration** for existing mixed-case documents — see README §7.7 for the `mongosh` snippet. Resolve case-duplicates first, since `users.email` is unique. |

### Deliberate design decisions

- **The signup password policy is not enforced at sign-in.** Doing so would lock
  out accounts created under an earlier policy and would disclose the policy to
  unauthenticated callers. Sign-in keeps only a generous input-size bound.
- **`X-Forwarded-For` is ignored.** It is attacker-controlled unless a trusted
  proxy overwrites it, so honouring it would let anyone bypass the limit by
  inventing a value per request. Proxy deployments must use uvicorn
  `--proxy-headers` with an explicit `--forwarded-allow-ips`.
- **Throttle state is per-process.** Acceptable for the current single-process
  deployment and a large improvement over nothing, but the limit multiplies by
  worker count and is not shared across instances. Documented in README §7.7.

### Verification

Both protections were mutation-tested: removing the `DuplicateKeyError` handler
and disabling the rate-limit call each cause the corresponding tests to fail, so
the suite is not vacuous.

### Still open in auth

Email verification, password reset (the frontend entry points were removed in
§17 rather than left as dead ends), and token revocation — a stolen 24-hour token
still cannot be invalidated, and "logout" only clears the client-side copy.

---

## 19. Change log — Docker and CI

Punch-list item 6. Test suite: **155 → 162 collected tests, and for the first
time zero failures.**

### The 4 "pre-existing failures" were not all stale tests

Earlier passes carried 4 failures forward as unrelated noise. Investigating them
properly — because a CI that is red on arrival trains people to ignore it —
turned up **two real production bugs**:

| Bug | Impact |
|---|---|
| `NameError: name 'synthesize' is not defined` — `backend/api/ws_interview.py` imported `synthesize_chunks` (unused) but called `synthesize` (never imported) | **The entire voice-interview TTS path was broken.** Every spoken question raised `NameError`. A refactor changed the call without updating the import, and the test patched the old name too, so the failure read as a test problem. |
| `has_valid_non_technical_hr_questions()` only checked that `questions` was a non-empty list, never that the questions were non-technical | A cached HR batch that had drifted technical was accepted and **never regenerated**, so a candidate kept getting database trivia in the HR round for the rest of the session. The real checker, `is_non_technical_hr_question()`, already existed and was simply never called. |

The other two were genuine test problems:

- The TTS fallback test encoded a two-provider chain (`elevenlabs → piper`), but
  the implementation is `elevenlabs → edge → piper`. It only began failing once
  `edge-tts` was actually installed during the §17 dependency sync — before that,
  edge failed implicitly with `TTSUnavailableError` and fell through to piper.
- The TTS health test depended on ambient environment: config resolution does
  `os.environ.get(NAME) or _MODULE_CONSTANT`, and the module constants are frozen
  at import from `.env`. It now patches the constant directly and passes with or
  without a populated `.env`.

**Related finding, not fixed:** that `or` fallback means **an explicitly empty
env var cannot disable ElevenLabs** — the empty string is falsy, so resolution
falls through to the import-time value. Blanking `ELEVENLABS_API_KEY` at runtime
does not turn the provider off. Worth correcting to an `is None` check.

**Also unverified:** `ws_interview.py` always sends `encoding: "mp3"` in its
`tts_start` frame, but `synthesize()` returns WAV bytes for the piper and
elevenlabs providers and MP3 only for edge. Browsers generally sniff the
container regardless of the label, which is likely why this has not been noticed,
but the label is wrong for two of the three providers.

### New files

| File | Purpose |
|---|---|
| `backend/Dockerfile` | Multi-stage; CPU-only torch; non-root user (uid 10001); both ML models pre-fetched at build so runtime is `HF_HUB_OFFLINE=1`; healthcheck; no `--reload`. |
| `frontend/Dockerfile` + `frontend/nginx.conf` | Multi-stage node → nginx. SPA fallback so deep links survive refresh; immutable caching for hashed assets and no-cache for `index.html`. |
| `docker-compose.yml` | MongoDB + backend + frontend. Fails fast with a clear message if `AUTH_JWT_SECRET` is unset. |
| `.dockerignore`, `frontend/.dockerignore` | Keeps `.env` and the virtualenv out of build context. Top-level patterns are slash-pinned so `backend/data/` (question bank, problem bank, Piper voice) is preserved. |
| `.github/workflows/ci.yml` | Backend tests, frontend build, and both image builds. Pip/npm/buildx caching; concurrency cancellation. |
| `scripts/start_backend.sh` | POSIX counterpart to the Windows-only script (§12 finding). |
| `backend/requirements-notebooks.txt` | `datasets`, `accelerate`, `peft` moved out of the runtime set. |

### Dependency split

`datasets`, `accelerate`, and `peft` were removed from `requirements.txt`.
**Verified, not assumed:** they are still installed in the developer virtualenv,
so a green suite proves nothing on its own. The suite was re-run with all three
blocked at the import-system level (with the blocker self-verified first) — 162
tests still passed.

### CI design notes

- **No MongoDB service container.** Confirmed empirically: the suite passes with
  `MONGO_URI` pointed at a dead port, because the repository layer is mocked
  throughout. `MONGO_URI` is set to an unreachable port in CI on purpose, so an
  accidental real database call fails loudly instead of passing against leftover
  local state.
- **Python 3.11 matches `backend/Dockerfile`**, so CI validates the version that
  actually ships rather than the developer's 3.13.

### Verified by running the stack

The images were initially shipped unbuilt, with a "not verified" caveat, because
Docker Desktop was not running. That was the wrong call — the correct move was to
ask for it to be started. It was, and the stack was then built and exercised
end to end.

| Check | Result |
|---|---|
| Backend image builds | 847 MB |
| Frontend image builds | 74.7 MB |
| `docker compose up` | All services healthy; backend serving in ~15 s |
| `/health` | `status: ok`, **`sbert_ready: true`** — the baked-in encoder loads under `HF_HUB_OFFLINE=1`, proving no runtime HuggingFace dependency |
| `/dsa/health` | **`judge0 available: true`** via `host.docker.internal:2359` — proves the `extra_hosts` mapping |
| MongoDB round-trip | Signup → 200 + JWT; duplicate → 409; weak password → 422; wrong password → 401; `/auth/me` authenticates |
| Email normalisation | Login with an ALL-CAPS address succeeds against the containerised database |
| Rate limiting | 5 × 401 then 429 with `Retry-After: 33`. Blocked at 5 rather than the per-IP budget of 10 because the stricter per-account lockout fired first — both layers confirmed working |
| Secrets excluded | No `.env` in the image; a scan for the actual key values found nothing |
| Database isolation | Container Mongo had only the test user; the local database was untouched |

### Three further bugs found by actually running it

None of these were reachable by syntax checking, and all three were in code
written during this same pass:

1. **`nginx.conf` emitted two `Cache-Control` headers** on hashed assets —
   `expires 1y` and `add_header` each emit one, and browsers handle the
   duplicate inconsistently. Collapsed into a single directive.
2. **`docker-compose.yml` passed the host's `JUDGE0_API_BASE_URL` into the
   container.** That value (`http://127.0.0.1:2359`) is right on the host but
   inside a container `127.0.0.1` is the container itself, so Judge0 would have
   been silently unreachable and the DSA round would have failed at runtime. Now
   pinned to `host.docker.internal:2359`, overridable only via the distinctly
   named `JUDGE0_URL_FROM_CONTAINER` so the host value cannot leak back in.
3. **Both compose files shared a Compose project.** The project name defaults to
   the directory, so the app stack and `docker-compose.judge0.yml` both landed in
   `interview_simulator` — meaning `docker compose down` on the app would tear
   down a running Judge0 too. The app stack now sets `name:
   interview-simulator-app`.

The first frontend build also failed on `npm ci` with a network error. Container
DNS and routing were checked before concluding anything; both were fine and a
rebuild succeeded, so it was transient registry flakiness rather than a
Dockerfile defect.

### Still not verified

CI itself has not run — the workflow is authored and its YAML parses, but no
push has exercised it. The backend and frontend jobs mirror commands that pass
locally; the `docker` job mirrors builds that now succeed locally.

---

## 20. Change log — config precedence and DSA execution

Two follow-up fixes, each done by a separate agent and then independently
verified here. Suite: **162 tests green in both conditions** — with `.env`
present and Judge0 live (all tests run), and under CI conditions with no `.env`
and Judge0 unreachable (3 skip themselves).

### 20.1 `.env` no longer overrides real environment variables

`backend/config.py` called `load_dotenv(..., override=True)`, so a `.env` file
silently beat real environment variables — backwards from the precedence every
deployment target assumes.

The fix introduces a single `load_project_dotenv()`. Real environment wins by
default; `DOTENV_OVERRIDE=true` restores the old local-dev behaviour. The flag is
read from `os.environ` **before** `.env` is loaded, so a deployed `.env` can
never grant itself priority. Verified directly: shell value wins by default,
`DOTENV_OVERRIDE=true` flips it back, and `.env` still applies when nothing is
set.

**A second loader would have silently defeated the fix.** `backend/voice/tts.py`
had its *own* `load_dotenv(..., override=True)`. Because `ws_interview.py` and
`routes_voice.py` both import the voice stack, fixing `config.py` alone would
have left the bug fully intact in production. `tts.py` now delegates to
`load_project_dotenv()`, so precedence lives in exactly one place.

**The hazard was concrete, not theoretical.** `docker-compose.yml` supplies
`MONGO_URI: mongodb://mongo:27017` via `environment:`. Under the old behaviour a
`.env` reaching the image would have pointed the container at the developer's own
database instead of the compose one. The `.dockerignore` rule was the only thing
preventing that.

**Not fixed, deliberately:** the `os.environ.get(NAME) or _MODULE_CONSTANT`
pattern in `tts.py` still means runtime mutation of a value to empty cannot
disable a provider. A blanket `is None` sweep would be wrong — the ~12 call sites
do not share semantics (`api_key` empty should mean "disabled", but
`output_format` empty should still fall through to a default). The real defect is
the frozen import-time constants, which deserves its own change.
`tests/test_tts_provider.py` carries a comment marking the spot.

### 20.2 The DSA C++ round was broken, and the error message hid why

Three chained bugs, all pre-existing:

1. **The C++ and Java harnesses never closed their JSON array.** `results << "["`
   opened it, entries were appended, but no `]` was ever emitted — so the harness
   printed malformed JSON.
2. **`escape_json` escaped quotes incorrectly** in both the C++ and Java
   generators, producing invalid generated source.
3. **The masking bug.** `_poll_submission` requested `base64_encoded=false`.
   Judge0 CE rejects the *entire* GET with HTTP 400 when stdout/stderr/
   compile_output contain bytes it will not serialise as plain UTF-8 — which
   happens on essentially every compile error, because GCC quotes identifiers
   with U+2018/U+2019. That 400 was swallowed by the poll loop's
   `except CodeExecutionError: continue`, so a compile error Judge0 reported in
   about one second surfaced as a 30-second "did not finish executing the
   submission before the backend timeout".

Bug 3 is why this was so hard to see: the symptom (timeout) pointed at
performance, while the cause was a compile error being reported and discarded. It
also explains why a trivial C++ program succeeded — no compile error means no
fancy quotes, so the plain-text GET returned 200.

Polling now requests base64 and decodes `stdout`/`stderr`/`compile_output`/
`message` back to text, so callers still receive plain strings.

**Verified:** the two C++ tests now pass in **9.3 s**, against a 67 s timeout
before. Python and Java paths re-verified, since the base64 change affects every
language.

### Correction to earlier notes in this document

§19 recorded the C++ failures as "a pre-existing DSA issue newly exposed" and
speculated that contention from duplicate Judge0 stacks was contributing. The
first half was right — it was pre-existing and untouched by the Docker work — but
the contention theory was wrong. The cause was the three code bugs above.
Carrying "environment flakiness" as an explanation would have left a genuinely
broken C++ interview round in place.

---

## 21. Change log — per-user spend quotas

Closes the last item flagged as a top remaining risk: unbounded Groq and Judge0
cost. Suite: **171 tests green** (was 162) in both conditions.

New `backend/api/quotas.py` adds per-user hourly caps, reusing the
`FixedWindowRateLimiter` primitive built for auth in §18 rather than introducing
a second mechanism.

| Route | Quota | Default/hour | Why it costs |
|---|---|---|---|
| `POST /resume/parse-upload` | `RESUME_PARSE` | 10 | Full-resume Groq extraction — the most expensive single LLM call in the app |
| `POST /resume/match-roles` | `ROLE_MATCH` | 20 | Optional Groq-backed role profiling |
| `POST /dsa/run` | `DSA_EXECUTION` | 60 | Compiles and runs untrusted code in Judge0 |
| `POST /dsa/submit` | `DSA_EXECUTION` | 60 | Runs the full hidden-test suite |
| `POST /voice/tts` | `VOICE` | 200 | ElevenLabs bills per character when active |
| `POST /voice/stt` | `VOICE` | 200 | CPU-bound transcription |

All configurable via `QUOTA_*` environment variables. Exceeding a cap returns
`429` with `Retry-After`.

### Design notes

- **Keyed by authenticated user id, not client IP.** These are all authenticated
  routes, the account is what actually maps to spend, and an IP key would both
  punish shared networks and be trivially evaded.
- **Separate from the auth throttles, deliberately.** Those defend against
  credential attacks; these bound cost. A user hammering resume parsing is not
  doing anything that looks like an attack — they would simply run up a bill.
  Different purpose, different limits, so they are kept distinct rather than
  overloading one mechanism.
- **Quotas do not share a budget.** Exhausting one leaves the others intact,
  which is covered by a test.
- Same per-process caveat as §18: N workers multiply the effective allowance.
  Acceptable for bounding a runaway bill, where order of magnitude is what
  matters; exact accounting needs Redis or gateway enforcement.

### Verification

Nine tests in `tests/test_quotas.py`, including one that asserts each expensive
route is still wired to its quota — a refactor that silently drops a
`Depends(quota_dependency(...))` will fail the suite rather than quietly
re-opening the spend hole. Confirmed non-vacuous by mutation testing: disabling
enforcement fails 6 of the 9.

### Not metered

The interview WebSocket (`/interview/ws/...`) also drives Groq calls for answer
evaluation and feedback, but it is a long-lived connection rather than a
per-request route, so the same dependency does not apply. Per-message metering
inside the socket loop is a sensible follow-up and is not covered here.

---

## 22. Change log — secret rotation and Judge0 hardening

### 22.1 Secret rotation

`AUTH_JWT_SECRET` was rotated in place (32 bytes of `secrets.token_hex`), and
the rotation was verified rather than assumed: a token signed with the previous
secret is now rejected with 401. Existing sessions are invalidated, which is the
intended effect.

Confirmed the repository's own `.env` was **never committed** to any reachable
history, so despite the GitHub remote these values were never pushed. Exposure
is limited to the local filesystem.

**Still requires the account owner** — cannot be done from here:
`GROQ_API_KEY`, `ELEVENLABS_API_KEY`, and `QUIZ_API_KEY` must be revoked and
reissued in their respective vendor dashboards. (`QUIZ_API_KEY` is real and used
by `scripts/import_questions_from_apis.py`, not dead config.)

### 22.2 A finding the original assessment got wrong

The assessment claimed `.local/judge0/judge0-v1.13.1.zip` was "an empty stub
zip", repeated it in three places, and recommended deleting it. **That was
wrong.** The archive is the legitimate upstream Judge0 v1.13.1 release: it
contains `judge0.conf` (12 KB) and `docker-compose.yml`, which is everything a
Judge0 CE release ships — the images come from Docker Hub. The error came from
reading a `file` output line describing the archive's *directory entry* as
"uncompressed size 0" and taking it to describe the archive. Verified by
extracting and diffing against the checked-in copy. Those three claims are now
marked withdrawn.

### 22.3 A finding the original assessment missed

While checking the above, a genuine leak surfaced that earlier passes did not
catch: **`.local/judge0/judge0-v1.13.1/judge0.conf` is tracked in git and
contains real credentials** — specifically non-empty `REDIS_PASSWORD` and
`POSTGRES_PASSWORD` values (deliberately not reproduced here; read them from the
file itself or from commit `1f249f5`).

Both ship blank upstream and were filled in locally. The file is present in the
Initial commit (`1f249f5`), which is what `origin/main` points at — so unlike
`.env`, **these are on the GitHub remote.**

Fixed going forward: the file is untracked (`git rm --cached`) and gitignored,
with the pristine template still available inside the release zip. The local
copy is left intact so the running Judge0 is unaffected.

**Not fixed, needs a decision:** the credentials remain in commit `1f249f5`.
Removing them requires rewriting the Initial commit and force-pushing, which
affects anyone who has cloned the repository. Rotating the two passwords is
worthwhile regardless, since history rewriting does not un-publish anything
already fetched.

### 22.4 Judge0 stack hardening

`docker-compose.judge0.yml` previously ran with authentication switched off
outright (`AUTHN_HEADER=`, `AUTHN_TOKEN=`, commented "Disable authentication for
local dev"), meaning anything that could reach the port could execute arbitrary
code on the host.

- Authentication is now **on by default**, via `AUTHN_HEADER=X-Auth-Token` and a
  required `JUDGE0_AUTH_TOKEN`. The stack refuses to start if it is unset,
  rather than silently coming up open.
- The hardcoded `judge0password` / `judge0redispassword` values are replaced by
  required environment variables.
- The compose project is explicitly named `judge0`, so it can no longer collide
  with the application stack (the same class of bug fixed in §19).
- `privileged: true` is **kept, deliberately**, with the reasoning documented in
  the file: Judge0 sandboxes runs with `isolate`, which needs cgroup and
  namespace control unavailable otherwise. Removing it does not harden the
  stack, it breaks execution. The correct mitigation is network isolation.

The backend now sends the token: `JUDGE0_AUTH_HEADER` / `JUDGE0_AUTH_TOKEN` were
added to `Judge0Settings` and are applied in `_open_json_request`. The token is
**optional on the backend side** on purpose — an existing local Judge0 running
without authentication keeps working, verified by re-running the DSA integration
tests against the currently-running unauthenticated instance (2 passed).

Suite: 171 passing throughout.

---

## 23. Change log — metering the interview WebSocket

§21 bounded spend on the REST routes but left the interview WebSocket open, and
that was the larger hole: it drives a Groq call per answer, and those arrive as
socket messages a client can send in a tight loop rather than as rate-limitable
HTTP requests.

Three paid calls inside the socket are now charged:

| Call | Quota | Why |
|---|---|---|
| `evaluate_answer` | `INTERVIEW_TURN` (120/hr) | A Groq call per submitted answer |
| `transcribe_audio` | `VOICE` | CPU-bound transcription, shares the REST voice budget |
| `synthesize` | `VOICE` | ElevenLabs bills per character when active |

`InterviewRuntime` now carries `user_id` so quotas are charged per account,
matching the REST routes.

### Refusals must not drop the socket

`quotas.py` gained `try_consume_quota()`, a non-raising form returning the
retry-after seconds. The REST `enforce_quota()` is now a thin wrapper that turns
that into a 429. The WebSocket needs the non-raising form because an exhausted
quota mid-interview must not tear down a connection carrying state that is
expensive to rebuild — the refusal is sent as a `quota_exceeded` error frame and
the runtime returns to `LISTENING`, so the round resumes when the window rolls
over. Speech specifically degrades to silence rather than stalling, since the
question text is already on screen.

### The first version of these tests was vacuous

Worth recording, because the failure mode is easy to repeat. The initial tests
covered `try_consume_quota` directly plus a source-inspection check that each
call site still references the gate. Mutation testing — gutting `_quota_blocked`
so it never consults the quota — **passed all 13 tests**. The source-inspection
test still saw the right text, and nothing exercised the gate's actual behaviour.

`QuotaBlockedBehaviourTests` was added to close that: it drives `_quota_blocked`
against a fake runtime and asserts it allows calls under the allowance, emits a
well-formed `quota_exceeded` frame once spent, and leaves the runtime in
`LISTENING`. The same mutation now fails 2 tests.

Suite: 171 -> 178 passing.

---

## 24. Change log — token revocation

Signing out previously only cleared the browser's copy of the JWT. The token
itself stayed valid for its full 24-hour life, so a captured token could not be
invalidated by any means short of rotating the signing secret and thereby
signing out every user at once.

Implemented as a MongoDB denylist:

- Issued tokens now carry a `jti` claim (a unique token id).
- `POST /auth/logout` decodes the presented token and records its `jti` in a
  `revoked_tokens` collection, along with the token's own `exp`.
- `authenticate_access_token` checks that denylist on every request.
- A TTL index on `expires_at` (`expireAfterSeconds=0`) lets MongoDB drop each
  entry exactly when the token would have expired anyway, so the collection
  stays bounded with no cleanup job.
- The frontend now awaits a real `/auth/logout` call before clearing local
  state, and swallows network failures so a user is never trapped in a
  signed-in UI.

Only the presented token is revoked, so signing out on one device leaves the
user's other sessions intact.

### Deliberate trade-offs

- **Tokens without a `jti` still authenticate.** Rejecting them would sign out
  every existing session the moment this deploys. They simply cannot be revoked
  individually until reissued at next sign-in.
- **A revocation-lookup failure allows the request.** If MongoDB is unreachable,
  the check returns "not revoked" rather than denying. Failing closed would lock
  every authenticated user out of the entire application on any database blip;
  the exposure window is bounded by the token's own expiry, and any route that
  touches data needs the database anyway and will fail there instead. Covered by
  a test so the behaviour is intentional rather than incidental.
- **A failed revocation returns 503, not success.** The user believes they are
  signed out, so a storage failure must be visible rather than swallowed.

Both protections were mutation-tested: bypassing the revocation check, and
removing the `jti` from issued tokens, each fail the suite.

Suite: 178 -> 185 passing.

---

## 25. Change log — housekeeping

The deletions flagged in §16 are resolved, after verifying each rather than
taking the original flag at face value.

**Deleted: `cleanup_unused_files.py`.** Confirmed genuinely dead first — all
four paths it targeted (`VoiceInterface.jsx`, `ResetPasswordPage.jsx`,
`supabaseClient.js`, `data/problems/core_bank.json`) no longer exist, so running
it was a no-op, and nothing in the repository referenced it.

**Untracked, not deleted: `.kilo/`.** Only `.kilo/agents/data.md` was ever
tracked; the rest is developer-local Kilo Code tooling (`node_modules/`,
`package.json`). It is now removed from the repository and gitignored, with the
on-disk files untouched. Deleting the directory as originally suggested would
have destroyed a working local setup for no repository benefit — the goal was to
stop shipping unrelated tooling, not to uninstall it.

**Withdrawn: the Judge0 zip.** §16 recommended deleting
`.local/judge0/judge0-v1.13.1.zip` on the basis that it was an empty stub. That
finding was wrong (§22.2) — it is the genuine upstream Judge0 v1.13.1 release,
and it is now the reference copy of the pristine `judge0.conf` after that file
was untracked. It stays.

---

## 26. Change log — CORS config, structured logging, and error tracking

Punch-list items 7 and 8. Suite: **295 → 321 passing** (the 295 baseline
already includes the interview-question-targeting work from
`INTERVIEW_QUESTION_TARGETING.md`, reviewed and verified in this same pass —
see below).

### CORS moved into `AppSettings`

`backend/main.py` hardcoded `allow_origins` to the two Vite dev-server
origins, with no way to add a real frontend domain short of a code change and
redeploy (§5). `CorsSettings` in `backend/config.py` now reads
`CORS_ALLOWED_ORIGINS` (comma-separated) and `CORS_ALLOW_ORIGIN_REGEX`, both
defaulting to the previous hardcoded values so local development needs no new
configuration. `tests/test_config.py` covers the default, a custom origin
list, a blank value falling back to the default (consistent with every other
`_read_*` helper in this file), and a custom regex.

### Structured (JSON) logging with request correlation

Logging was ad hoc (§9): some modules used `logging.getLogger(__name__)`, one
route used a raw `print()`, and nothing tied separate log lines from the same
request together. `backend/logging_config.py` is now the single place that
configures it:

- Every log line is one JSON object to stdout (`timestamp`, `level`, `logger`,
  `message`, and `exception` when there is one) — grep-able today, and ready
  for a log aggregator without changing the emitting code later.
- `RequestIdMiddleware` (`backend/main.py`) mints (or reuses an inbound
  `X-Request-ID`) an id per request, threads it through a `contextvars.ContextVar`
  so every log line emitted anywhere during that request carries it via a
  logging filter, and echoes it back as a response header. A user report
  ("it broke around 2pm") now turns into an exact log query instead of a
  guess.
- The stray `print("!!! JUDGE0 ERROR:", ...)` in `routes_dsa.py` (§2, flagged
  as trivial but never fixed) is now `_LOGGER.error(...)`, so it participates
  in the same format and level filtering as everything else.
- `LOG_LEVEL` controls verbosity (default `INFO`).

### A global exception handler, and the bug in its first version

`backend/main.py` had no consistent shape for an unhandled exception (§5) —
FastAPI's default 500 leaked no traceback (confirmed good), but had no
request id, no log line, and no fixed error envelope. A handler registered for
the base `Exception` class now returns `{"error": {"code", "message",
"request_id"}}` and logs the exception with its request id.

**The first version of this had a real bug, caught before landing.** A
handler registered for the base `Exception` class is installed as
`ServerErrorMiddleware`'s `error_handler` — Starlette special-cases `Exception`
and `500` to sit *outside every user middleware*, including
`RequestIdMiddleware`. Concretely: the exception propagates up through
`RequestIdMiddleware`'s `try/finally`, which resets the contextvar to `None`
on its way out, *before* `ServerErrorMiddleware` ever calls the handler — so
`request_id_var.get()` inside the handler always returned `None`, silently
defeating the one thing request ids exist for on exactly the responses that
need them most. Caught by a test asserting the 500 body's `request_id` matches
the response header, which failed with `None != 'trace-me'`.

Fixed by also stamping the id onto `request.state` in the middleware.
Starlette's `Request.state` is backed by the ASGI `scope` dict, which is the
same object threaded through the entire connection — so it survives even
though `ServerErrorMiddleware` constructs its own fresh `Request` from that
scope. The handler reads `request.state` first, falling back to the
contextvar only for unit tests that call it directly. `RequestIdMiddleware`
also never got a chance to stamp its own response with `X-Request-ID` in this
path, since the exception propagated out of `call_next` before that line — the
handler sets the header itself for the same reason.

`tests/test_main_observability.py` drives this through the real FastAPI stack
with `TestClient(app, raise_server_exceptions=False)` — needed because
`ServerErrorMiddleware` always re-raises after building the response, by
design, so real servers can still log it. Also covers CORS wiring end to end
(a configured origin allowed on preflight, an unconfigured one rejected, the
dev-server default still working) and that a normal request is unaffected.

### Sentry, optional and inert by default

`configure_error_tracking()` calls `sentry_sdk.init()` only when `SENTRY_DSN`
is set; with it unset (the default), the function returns immediately and
never imports `sentry_sdk`, so nothing changes for anyone who hasn't set up an
account. `sentry-sdk` is now a runtime dependency (`backend/requirements.txt`)
since it needs to be importable when a DSN is supplied, but installing it
costs nothing when it is not.

### Interview-question-targeting review

Before starting the above, the uncommitted working-tree changes implementing
`INTERVIEW_QUESTION_TARGETING.md` (the 70% role / 30% resume question
allocation, pinned as a standing preference) were reviewed rather than taken
on faith: ran the full suite (295 passing, matching that document's own
verification), checked for circular imports from the new cross-module
dependency (`resume_skill_profiler.py` now imports from
`question_generator.py`), and ran the frontend production build. No issues
found; that work was already correctly verified by the pass that wrote it.

### Still not done

- **CI has still never run for real.** The workflow now has meaningfully more
  to validate than when §19 shipped it (this change touches `main.py`,
  `config.py`, and adds a new runtime dependency) and remains something only
  ever exercised locally, not on GitHub's own runners.
- `/health` still does not check Mongo connectivity (§14) — unrelated to this
  pass, carried forward from the original assessment.

---

## 27. Change log — Prometheus metrics

Punch-list item 8's remaining half (§14). Suite: **321 → 331 passing.**

`backend/metrics.py` adds four series: `http_requests_total` /
`http_request_duration_seconds` (every request, labelled by method, route
template, and status) via a new `RequestMetricsMiddleware` in `main.py`, and
`groq_requests_total` / `judge0_requests_total` (each labelled by outcome —
`success` / `rate_limited` / `error`) recorded at the one call site each of
those clients actually has. `/metrics` serves them in Prometheus text format,
unauthenticated — the same posture as `/health`, and consistent with how a
scraper is normally kept out of reach at the network layer (a reverse proxy
or the orchestrator's own network policy) rather than the application layer.

### Not `prometheus-fastapi-instrumentator`

The obvious library for the HTTP half turned out to be a real risk rather
than a shortcut: `prometheus-fastapi-instrumentator==7.1.0` pins
`starlette<1.0.0`, and this project resolves `starlette==1.6.0` (FastAPI
0.136.0 only requires `>=0.46.0`, so nothing forces the newer version — it is
just what `pip` picked when `requirements.txt` was last resolved). Installing
the library to try it out **did exactly what the pin implies**: silently
downgraded the installed `starlette` from 1.6.0 to 0.52.1, a transitive
downgrade of the actual web framework this app runs on, caught only by
`pip`'s own install log rather than by anything that would have failed
loudly. Confirmed the app still imported at that downgraded version, then
un-did it (`pip install starlette==1.6.0`) and wrote `RequestMetricsMiddleware`
directly against `prometheus_client` instead — about 25 lines, no dependency
risk, and it already had `RequestIdMiddleware` next to it as a template for
exactly this kind of Starlette middleware. This is the second time in this
project a library evaluated for one line of setup turned out to need throwing
away after actually installing it and checking (§19 did the same for a
dependency split, §20 for the DSA polling bug) — the lesson generalises: a
library's own declared constraints are worth reading, not just its README.

### Route templates, not resolved paths

`RequestMetricsMiddleware` labels by `request.scope["route"].path` — the
registered pattern (`/dsa/{session_id}/run`) — not `request.url.path` (the
resolved URL with a real session id in it). Labelling by the resolved path
would give every session id, submission id, and UUID this app has ever seen
its own permanent Prometheus time series: exactly the unbounded-cardinality
mistake that eventually takes a metrics endpoint down. `scope["route"]` is
only populated *after* routing runs, which happens inside `call_next` — read
after awaiting it, not before, the same ordering lesson `RequestIdMiddleware`
already had to learn about `request.state` in §26. An unmatched route (404)
has no `route` on its scope at all and is labelled `"unmatched"` rather than
the arbitrary path someone probed. `tests/test_metrics.py` asserts the
template label directly and asserts the raw-path label *never* gets written.

### Verification

`tests/test_metrics.py` (10 tests): Groq outcomes (success, rate-limited not
counted as a plain error, one `error` sample per exhausted retry attempt),
Judge0 outcomes (success, HTTP error, connection error) via the one function
both health checks and submissions funnel through, the route-template/raw-path
distinction above against a throwaway FastAPI app, and the `/metrics`
endpoint itself — including that scraping `/metrics` does not inflate its own
counter. Also started the real app with `uvicorn` and read actual scraped
output, rather than trusting the tests alone.
