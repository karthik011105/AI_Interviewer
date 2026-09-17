# Production Readiness & Free-Cloud Deployment Plan

Plan date: 2026-09-17
Branch: `feat/conversational-interview`
Scope: whole repository — every backend subsystem, the frontend, tests, CI, config,
Docker, data assets, and a live deployment on free infrastructure.

This plan is the successor to `PRODUCTION_READINESS.md`. That document is a running
record of what has already been fixed (§17–§28). This one is forward-looking: what is
still wrong, how we verify every subsystem, and how this actually gets deployed.

---

## 0. What I verified before writing this plan

Everything in this section was executed, not read.

| Check | Result |
|---|---|
| Full suite, `unittest discover -s tests -t . -p "test_*.py"` | **341 tests, all pass**, 49s |
| Full suite, `unittest discover -s tests -p "test_*.py"` (CI's command) | **Hard crash.** Access violation `0xC0000005`, exit 139, zero output |
| Each of the 34 test modules run individually | All 34 pass |
| Cumulative import of all 34 test modules in one process | All import cleanly |
| Local interpreter version | Python 3.13.7 |
| Dockerfile / CI interpreter version | Python 3.11 |
| Largest tracked file | `backend/data/tts_models/en_US-lessac-medium.onnx`, 63.2 MB |
| `.git` directory size | 60 MB |
| `piper` binary in backend image | **Absent** — not in `Dockerfile`, not in `requirements.txt` |
| `TTS_PROVIDER` default in `docker-compose.yml` | `piper` |
| GitHub Actions run history | Cannot confirm — `gh` is not installed locally |

### The three findings that matter most

**1. The CI test command is the one that crashes.**
`.github/workflows/ci.yml:54` runs `python -m unittest discover -s tests -p "test_*.py" --verbose`.
Without `-t .`, unittest treats `tests/` as the top-level directory instead of the
repository root. Locally that produces an immediate access violation with no output at
all — not a test failure, a process death. Adding `-t .` makes the same suite report
341 passing tests. Since CI has never run (per `PRODUCTION_READINESS.md` §19, §20, and
the executive summary), nothing has ever caught this. **This is why Phase 0 comes
before the audit: the suite is the instrument, and the instrument is mis-wired.**

**2. The backend image ships a 63 MB voice model it cannot use.**
`docker-compose.yml` sets `TTS_PROVIDER=piper`, and `piper` is the documented default
in `backend/voice/tts.py`. But no stage of `backend/Dockerfile` installs the `piper`
binary, and `piper-tts` is not in `requirements.txt`. `_resolve_piper_executable()`
returns `None`, so every containerised synthesis falls through to edge-tts — correct
behaviour, since commit `361b90e` added the fallback logging, but it means the 63 MB
ONNX voice is dead weight in both the image and git history. That one file is the bulk
of the 60 MB `.git`.

**3. Rate limits and spend quotas live in process memory.**
`backend/api/rate_limit.py` and `backend/api/quotas.py` keep counters per process.
Both are documented as such. On a free tier that scales to zero or restarts on idle,
**every cold start resets every quota and lockout**. An attacker does not need to
defeat the lockout; they need to wait for a redeploy. Since the whole point of the
quotas is to bound real spend on Groq, ElevenLabs, and Judge0, this needs to move to
MongoDB — which is already a hard dependency, so it costs no new infrastructure.

---

## 1. The free-tier constraint, stated honestly

This shapes everything else, so it goes before the work items.

### What the backend actually needs

At startup `create_app()` calls `warmup_semantic_encoder()` and
`warmup_research_asset_registry()`, and the lifespan hook warms `faster-whisper`. So a
single backend process holds, concurrently:

- PyTorch CPU runtime plus `all-MiniLM-L6-v2` (sentence-transformers)
- `faster-whisper base.en` int8 via CTranslate2
- The 677 KB question bank and the research-asset registry

Expect roughly **0.9–1.4 GB resident** and an **847 MB image**. Cold start is model
load, not process start.

### Free tiers measured against that

| Platform | Free allowance | Verdict |
|---|---|---|
| **Hugging Face Spaces** (Docker SDK) | 2 vCPU, **16 GB RAM**, 50 GB ephemeral | **Fits comfortably.** Supports Docker and WebSockets. Sleeps only after prolonged inactivity |
| **Oracle Cloud Always Free** | 4 ARM cores, **24 GB RAM**, always-on VM | **Fits, and is the only free option that can also host Judge0.** Needs a card; ARM means arm64 images; regional capacity is often exhausted |
| **Google Cloud Run** | 180k vCPU-s + 360k GiB-s/month, scale-to-zero | Fits on paper at 2 GB. A 40–90 s cold start per wake is poor for a live interview |
| Render free | 512 MB, sleeps at 15 min idle | **Does not fit** without removing the local ML models |
| Koyeb free | 512 MB, sleeps | **Does not fit** |
| Fly.io | No dependable free allowance for new orgs | Not free |

### Judge0 is the real blocker

Judge0 runs untrusted code with `isolate`, which needs privileged containers and
cgroup access. **Hugging Face Spaces, Cloud Run, Render, and Koyeb all forbid this.**
There are three honest options, and we should pick one now rather than discover it
during deployment:

- **(a) Judge0 CE via RapidAPI free tier.** ~50 submissions/day. `Judge0Settings`
  already carries `auth_header`/`auth_token`, but RapidAPI requires *two* headers
  (`X-RapidAPI-Key` and `X-RapidAPI-Host`), so this needs a small change in
  `backend/dsa/code_executor.py`.
- **(b) Self-host Judge0 on an Oracle Always Free VM.** Full capability, no request
  cap, and `docker-compose.judge0.yml` already exists and has been hardened (§22.4).
- **(c) Ship without the DSA round**, behind a feature flag that degrades the UI
  cleanly instead of erroring.

### Recommended architecture

**Track A — fastest genuinely-free path, no credit card:**

```
Cloudflare Pages (static frontend, VITE_API_BASE_URL baked at build)
        |  HTTPS + WSS
        v
Hugging Face Spaces — Docker, backend on Space port 7860
        |                       |
        v                       v
MongoDB Atlas M0 (512 MB)   Judge0 CE via RapidAPI  [option (a)]
        ^
        |
    Groq free tier (LLM)
```

**Track B — one always-on box, full DSA, if a card is acceptable:**
An Oracle Always Free arm64 VM running `docker compose` for app, Mongo, and Judge0,
with Caddy terminating TLS. More capable, more operational surface, needs arm64 builds.

I recommend **Track A with Judge0 option (a)**, treating Track B as the upgrade path.
Track A needs no payment instrument, no capacity lottery, and has enough RAM that we
never have to strip the ML models out of the backend.

---

## 2. Phase 0 — Repair the instruments (do this first)

No audit result is trustworthy until these are done.

| # | Task | Why |
|---|---|---|
| 0.1 | Add `-t .` to the CI test command in `.github/workflows/ci.yml:54` | It currently invokes the form that dies with an access violation |
| 0.2 | Add a `scripts/run_tests` wrapper and a `tests/README` note with the correct invocation | Stops the wrong form being retyped from memory |
| 0.3 | Decide one Python version and enforce it everywhere | Local venv is 3.13.7; Docker and CI are 3.11. We are testing a different interpreter than we ship |
| 0.4 | Add `npm test` to the frontend CI job | `frontend/src/interview/interviewReducer.test.js` and the `test` script exist, but CI only runs `build`, so the reducer tests never execute |
| 0.5 | **Push a branch and make CI actually run, green, once** | Outstanding since §19. Everything below assumes a working pipeline |
| 0.6 | Reproduce the 341-test baseline in CI and record the number | Gives every later phase a regression floor |

Exit criterion: a green GitHub Actions run whose backend job reports 341 tests, and
whose frontend job runs both the reducer tests and the build.

---

## 3. Phase 1 — Subsystem-by-subsystem correctness audit

This is the "every sub-part" pass. Fourteen subsystems, roughly 37k lines. Each gets
the same treatment, and none is marked done on a graph query or a skim:

1. Narrow scope with `code-review-graph` (callers, dependents, existing coverage).
2. Read the implementation.
3. Read its tests, and specifically look for **vacuous tests** — §23 of
   `PRODUCTION_READINESS.md` records a case where the first version of a test suite
   asserted nothing meaningful. That failure mode is the main thing an audit is for.
4. Exercise the real path, not just the unit test.
5. Fix what is broken; add tests for what is untested.
6. Record findings in an append-only log, one section per subsystem.

Order is dependency-first, so a defect found early does not invalidate later work.

| # | Subsystem | Files | ~LOC | Specific things I already suspect |
|---|---|---|---|---|
| 1.1 | Config, logging, metrics | `config.py`, `logging_config.py`, `metrics.py`, `main.py` | 1.2k | `/metrics` is exposed **unauthenticated and unconditionally** — it publishes every route template and traffic volume. `/health` still does not check Mongo (§14, §20) |
| 1.2 | Database layer | `database/*` | 1.3k | Verify the `state_version` optimistic-concurrency paths and every unique index. Confirm connection handling survives an Atlas failover |
| 1.3 | Auth & abuse controls | `api/auth.py`, `routes_auth.py`, `rate_limit.py`, `quotas.py` | part of 8.2k | The in-process counter problem from §0.3. Also token-revocation storage growth, and whether `EMAIL_PROVIDER=console` silently no-ops password reset in production |
| 1.4 | Resume pipeline | `nlp/resume_parser.py`, `resume_quality_scorer.py`, `resume_skill_profiler.py`, `ner_extractor.py`, `routes_resume.py` | ~3k | Upload validation is called out as genuinely good; verify it holds for malformed and adversarial PDFs. `ENABLE_LOCAL_RESUME_PATH_API` must be off in production |
| 1.5 | Role matching | `nlp/role_matcher.py`, `research_assets.py` | ~1.2k | `HF_HUB_OFFLINE=1` means a model-name change fails at startup rather than downloading. Confirm that is intended and that it fails loudly |
| 1.6 | Question generation | `nlp/question_generator.py`, `coverage_director.py`, `interview_director.py`, `interview_context.py`, `conversation_memory.py` | ~4.5k | Confirm the 70/30 role-vs-resume weighting from `INTERVIEW_QUESTION_TARGETING.md` holds end to end, not just in unit tests. §19 found invalid cached HR questions were never regenerated |
| 1.7 | Interview runtime & WebSocket | `api/interview_runtime.py`, `interview_engines/*`, `routes_interview.py`, `ws_interview.py` | ~4k | A long-lived socket, per-process quotas, and a platform that may idle out the connection. Reconnect and resume behaviour needs verifying against real disconnects |
| 1.8 | Evaluation, feedback, reporting | `nlp/answer_evaluator.py`, `feedback_generator.py`, `linguistic_analyzer.py`, `routes_report.py`, `dsa/report_engine.py` | ~3k | §17 recorded a deliberate report-degradation decision; verify that path is still reachable and still sensible |
| 1.9 | DSA subsystem | `dsa/*`, `routes_dsa.py` | 3.1k | The static safety gate in `code_analyzer.py` is the highest-risk code in the repo — it is what stands between a user and arbitrary execution. §20 found three chained bugs whose symptom pointed away from the cause. Re-verify Python, C++, and Java against a live Judge0 |
| 1.10 | Voice | `voice/stt.py`, `tts.py`, `vad.py`, `routes_voice.py` | 1.2k | The piper finding from §0.2. Decide: install piper, or drop the 63 MB model and make edge-tts the honest default |
| 1.11 | Assessment | `assessment/question_bank.py`, `routes_assessment.py` | 0.8k | A 677 KB bank loaded at startup; validate schema and dedupe |
| 1.12 | Workflow / reset | `routes_workflow.py`, `lib/workflowReset.js` | ~0.5k | A destructive endpoint. Verify ownership enforcement and that it cannot cross accounts |
| 1.13 | Frontend | `frontend/src/**` | 13.6k | `ScriptedInterview.jsx` is 75 KB and `ReportPage.jsx` 52 KB in single files. Error boundaries, token handling, the WSS upgrade, and the baked-at-build `VITE_API_BASE_URL` |
| 1.14 | Scripts & data assets | `scripts/*`, `data/`, `models/*.pth`, `sample_resume.pdf` | ~1k | `models/*.pth` and `sample_resume.pdf` are tracked but appear unused at runtime. Confirm, then remove |

Deliverable: `AUDIT_LOG.md`, one section per subsystem, each recording what was read,
what was run, what was found, and what was changed.

---

## 4. Phase 2 — Production hardening

Cross-cutting work that is not any one subsystem's fault.

| # | Task | Notes |
|---|---|---|
| 2.1 | **Move rate limits and quotas into MongoDB** | Removes the reset-on-restart hole and the multi-worker multiplication caveat in `backend/Dockerfile`. No new infrastructure — Mongo is already required |
| 2.2 | **Split `/health` from `/ready`** | `/health` stays a liveness check. `/ready` pings Mongo and reports Groq and Judge0 reachability. Platform health checks point at `/ready` |
| 2.3 | **Protect `/metrics`** | Bearer token or network restriction. Today anyone can enumerate the API surface and read traffic volumes |
| 2.4 | **Choose and wire a real email provider** | `EMAIL_PROVIDER=console` means password reset writes to a log instead of sending mail. The reset flow is built (§28) but is not functional in production until this is set |
| 2.5 | **Security headers and TLS** | Review `frontend/nginx.conf` for CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`. Confirm the deployed frontend is HTTPS-only and the socket is WSS |
| 2.6 | **Secrets** | Rotate the Judge0 Postgres/Redis passwords committed in `1f249f5` (§22.3) before anything is public. Decide explicitly whether to rewrite history; that needs a force-push, and a `backup-before-rewrite` branch already exists |
| 2.7 | **Slim the image** | Drop the 63 MB piper voice if piper is not installed (0.2), and purge it from history in the same rewrite as 2.6 if we do one |
| 2.8 | **Turn on error tracking** | Sentry is wired and inert without `SENTRY_DSN` (§26). Set it for the deployment so production errors are visible |
| 2.9 | **Data lifecycle** | Atlas M0 is 512 MB. Resume text, transcripts, and reports accumulate. Needs TTL indexes or a retention policy, plus a backup/export path |
| 2.10 | **Load-shed and timeout review** | Groq timeout is 20 s with 3 retries; Judge0 wall limit is 4 s. On a free-tier single worker, a few concurrent interviews can saturate the event loop |

---

## 5. Phase 3 — Deployment

| # | Task |
|---|---|
| 3.1 | Confirm Track A vs Track B, and the Judge0 option (a/b/c) |
| 3.2 | Provision MongoDB Atlas M0; create the user, network access, and indexes; run a real connection test from outside |
| 3.3 | Make the backend image Spaces-compatible: listen on `7860`, read secrets from Space secrets, confirm nothing writes to a read-only path |
| 3.4 | Deploy the backend; watch a cold start end to end and record real startup time and peak memory |
| 3.5 | Build and deploy the frontend to Cloudflare Pages with the production `VITE_API_BASE_URL`; set `CORS_ALLOWED_ORIGINS` and `AUTH_PASSWORD_RESET_URL_BASE` to the real domain |
| 3.6 | Wire Judge0 per the chosen option and execute one real submission in each of the three languages |
| 3.7 | **Full live smoke test as a real user**: sign up, upload a resume, get a role match, run a scripted interview, run a voice interview over WSS, complete a DSA problem, generate a report, reset a password, sign out and confirm the token is revoked |
| 3.8 | Verify quotas and rate limits behave on the deployed instance, including across a restart |

Exit criterion for "production": every item in 3.7 performed against the live URLs,
with screenshots or transcripts, and zero browser console errors.

---

## 6. Phase 4 — Operate

| # | Task |
|---|---|
| 4.1 | A `RUNBOOK.md`: how to redeploy, roll back, rotate a secret, read logs, and what each alert means |
| 4.2 | An uptime check against `/ready` |
| 4.3 | Cost and quota monitoring for Groq, the Judge0 free tier, and Atlas storage |
| 4.4 | Update `README.md` and `PRODUCTION_READINESS.md` to describe the deployed reality |

---

## 7. Sequencing and what I need from you

```
Phase 0  ──> Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4
(instruments) (audit)   (hardening)  (deploy)   (operate)
```

Phase 0 is a prerequisite for everything. Phases 1 and 2 partly interleave: a defect
found in 1.3 is fixed in 1.3, not deferred to 2.1.

### Decisions — settled 2026-09-17

| Decision | Choice | Consequence |
|---|---|---|
| Backend host | **Track A — Hugging Face Spaces** | Docker SDK on Space port 7860. Frontend to Cloudflare Pages, database to Atlas M0. No credit card, 16 GB RAM, so the ML models stay in-process |
| Judge0 | **RapidAPI free tier** | Needs the two-header change in `backend/dsa/code_executor.py` (`X-RapidAPI-Key` plus `X-RapidAPI-Host`). Caps the DSA round at roughly 50 submissions/day, which must be reflected in `QUOTA_DSA_EXECUTION_PER_HOUR` |
| Git history | **Rotate only, no rewrite** | Judge0 Postgres/Redis passwords get rotated so the committed ones are worthless. The 63 MB voice model is untracked going forward but stays in history; `.git` remains ~60 MB. No force-push |
| Python version | **3.11** | Matches what Docker and CI already ship. The local virtualenv gets rebuilt on 3.11 |

Still open, deferred to Phase 2.4: **which email provider** backs password reset.
Until it is chosen, `EMAIL_PROVIDER=console` means reset mail is logged, not sent.

### Things I will need you to turn on or supply

I will ask at the point each one is reached, rather than working around it and
labelling the result unverified:

- **Docker Desktop running**, for image builds and the local stack in Phases 2 and 3
- **A GitHub push**, so CI can run for real (Phase 0.5)
- **A MongoDB Atlas account**, for Phase 3.2
- **A Hugging Face account** (or Oracle Cloud), for Phase 3.4
- **Judge0 credentials** for whichever option we choose
- **An email provider API key**, for Phase 2.4

---

## 8. Honest assessment of effort

The codebase is in better shape than most projects at this stage. The prior passes
recorded in `PRODUCTION_READINESS.md` did real work and documented it unusually well.
What is left is not a config change.

- **Phase 0** is small and mechanical, but it is where the CI bug gets fixed.
- **Phase 1** is the bulk of the work: 37k lines, fourteen subsystems, and at least one
  subsystem (`code_analyzer.py`) where a miss has security consequences.
- **Phase 2** is well-defined; 2.1 and 2.9 are the two that involve real design.
- **Phase 3** is where estimates go wrong, because it is the first time the whole
  system meets infrastructure it does not control.

The largest single risk to the "free" goal is Judge0. The largest single risk to the
"production-grade" goal is that the safety gate on user-submitted code has never been
exercised against a live Judge0 by this audit.

---

## 9. Change log — Phase 0, repairing the instruments

Completed 2026-09-17. Every claim here was executed and its exit code checked.

### 0.1 The CI test command was the crashing form — fixed

`.github/workflows/ci.yml` now runs the suite with `-t .`. The comment in the
workflow explains why the flag is load-bearing, so it does not get "tidied" away
later.

### 0.2 A test wrapper, so the broken invocation cannot be retyped

`scripts/run_tests.ps1` and `scripts/run_tests.sh`, matching the existing
`start_backend.ps1`/`.sh` convention. Both set `AUTH_JWT_SECRET` (without it
`config.py` refuses to build settings and every module fails at import) and point
`MONGO_URI` at a dead port, exactly as CI does. Both support a single module and a
verbose mode. The PowerShell one warns explicitly if it ever sees exit
`-1073741819`, naming the missing flag as the likely cause.

`tests/README.md` previously recommended the crashing command as "the safest
repo-level test command to include in submission instructions" — that file was the
source the CI workflow inherited the mistake from. It has been rewritten, and now
also documents what the suite does **not** cover, since a green run is weaker
evidence than it looks: the repository layer is mocked throughout, Judge0 is never
called, Groq is never called, and no test drives the interview WebSocket.

`README.md` and `backend/README.md` already used the correct form, so only the
tests README and CI were wrong.

### 0.3 Python standardised on 3.11

The local virtualenv was 3.13.7 while `backend/Dockerfile` and CI ship 3.11 — and
`README.md` line 104 had said `py -3.11 -m venv .venv` all along, so the
environment had simply drifted from its own documentation.

Rebuilt as `.venv311` alongside the old one rather than over it, verified, and only
then swapped in. Both interpreters pass all 341 tests, so this was drift rather than
a live defect:

| Interpreter | Result |
|---|---|
| Python 3.13.7 (previous) | 341 tests, OK, 59.4s |
| Python 3.11.9 (now current) | 341 tests, OK, 55.9s |
| Python 3.11.9 via `scripts/run_tests.ps1` | 341 tests, OK, 50.1s, exit 0 |

The superseded environment is at `.venv313-retired` (1.6 GB). It is gitignored and
fully reproducible; delete it whenever convenient.

### 0.4 Frontend tests wired into CI

`frontend/src/interview/interviewReducer.test.js` and the `test` script both
existed, but the CI job only ran `build`, so **19 tests across 6 suites had never
executed in CI**. A `Run tests` step now runs before `Build`, so a unit failure
surfaces faster than a bundle failure.

This exposed a second version drift. The `test` script passes a glob
(`src/**/*.test.js`) to `node --test`, and glob expansion inside the test runner
only arrived in Node 21. CI pinned Node 20, where the pattern is taken as a literal
path and **no tests run at all** — it would have reported success while testing
nothing. CI is now pinned to Node 24, the version the 19 tests and the production
build were both verified green on.

### Three findings from actually running things

**1. The suite is not hermetic — it downloads a model from HuggingFace.**
Importing the app calls `warmup_semantic_encoder()`, which fetches
`all-MiniLM-L6-v2`. One local run made **35 requests to huggingface.co**, and the
Hub itself warned twice that they are unauthenticated and therefore rate limited.
Without intervention, every CI run re-downloads the model and a Hub rate limit or
outage fails the build for reasons unrelated to the code. An `actions/cache` step
keyed on the model name now caches `~/.cache/huggingface`. This is the most likely
cause of a flaky first CI run, so it is worth having fixed before 0.5 rather than
after.

**2. A suffixed virtualenv was neither gitignored nor dockerignored.**
`.gitignore` had `.venv/` and `.dockerignore` had `/.venv/` — both exact. The
`.venv311` directory I created was therefore untracked **but not ignored**, so a
`git add -A` would have committed a 1.6 GB torch install, and a Docker build would
have shipped it into the build context. Both files now use a glob. This was a
latent trap that only surfaced because something happened to create a differently
named environment.

**3. My own wrapper had this bug, and running it is what found it.**
Windows PowerShell 5.1 wraps every stderr line from a native executable in an
ErrorRecord. `unittest` writes its whole progress report and summary to stderr, so
with `$ErrorActionPreference = "Stop"` in force the script aborted on the first
deprecation warning and never reached the test result. Fixed by dropping to
`Continue` around the interpreter call and deciding pass/fail on the exit code.
Worth recording because it is the same failure mode as the CI bug: a wrapper that
looks correct, has never been run, and fails in a way that does not resemble a test
failure.

### Also noted, not yet acted on

- `starlette` 1.6.0 warns that `starlette.testclient` with `httpx` is deprecated
  and wants `httpx2`. Not urgent, but it is a pinned-dependency decision that
  belongs in Phase 1.1.
- `frontend/nginx.conf` sets **no security headers at all** — no CSP, HSTS,
  `X-Content-Type-Options`, `Referrer-Policy`, or `X-Frame-Options`. This confirms
  Phase 2.5. One trap to plan for: nginx `add_header` is not inherited into a
  child block that declares its own, so adding headers at `server` level will
  silently **not** apply to `location /assets/` or `location = /index.html`, both
  of which already declare `add_header`. They will need repeating, or restructuring.
- `backend/metrics.py` omits the two HTTP metrics from its `__all__`. Harmless,
  since `main.py` imports them by name, but inconsistent.

### 0.5 and 0.6 — blocked on a push

Still outstanding, and the reason is external: CI has never run, and it cannot run
until a commit reaches GitHub. The workflow changes above are exactly the kind that
only a real run can validate. Phase 0 is therefore complete locally but **not
closed**; 0.6 records the 341-test baseline in CI, which requires 0.5 first.
