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
| 1.13 | Frontend | `frontend/src/**` | 13.6k | `ScriptedInterview.jsx` is 75 KB and `ReportPage.jsx` 52 KB in single files. Error boundaries, token handling, the WSS upgrade, and the baked-at-build `VITE_API_BASE_URL`. **Plus:** Phase 1.8 established that a partial round reports the mean over *answered* questions, so the report is only honest if the UI shows `response_count` against `total_questions` next to the score. Verify that it does |
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
| 2.11 | **Make the interview turn commit survive a disconnect** | Found in Phase 1.7. `_transcribe_captured_audio` clears the captured audio before transcribing, and the socket's `finally` cancels the commit task, so a disconnect mid-answer loses it with nothing to retry from. A cancel between persisting the response and advancing the index can also re-ask an already-answered question. Needs the commit shielded once transcription starts and the persist step made idempotent |
| 2.12 | **Translate driver errors into the domain hierarchy at one boundary** | Found in Phase 1.8, and systemic. **46 handlers across 7 route modules** catch `DatabaseClientError`, but `MongoRepository` calls pymongo directly in most methods, so a raw `PyMongoError` escapes all of them. Measured: `AutoReconnect` (what an Atlas failover produces) and `ExecutionTimeout` (a slow query on a shared-tier cluster) each become a **500 instead of a 503**. Both are expected events on Atlas M0, which is the chosen deployment. The fix is one translation boundary in the repository, not 46 edits — extending the `DuplicateKeyError` translation added in Phase 1.2 to the rest of the driver's error tree |

---

## 5. Phase 3 — Deployment

| # | Task |
|---|---|
| 3.1 | Confirm Track A vs Track B, and the Judge0 option (a/b/c) |
| 3.2 | Provision MongoDB Atlas M0; create the user, network access, and indexes; run a real connection test from outside |
| 3.3 | Make the backend image Spaces-compatible: listen on `7860`, read secrets from Space secrets, confirm nothing writes to a read-only path |
| 3.4 | Deploy the backend; watch a cold start end to end and record real startup time and peak memory |
| 3.5 | Build and deploy the frontend to Cloudflare Pages with the production `VITE_API_BASE_URL`; set `CORS_ALLOWED_ORIGINS` and `AUTH_PASSWORD_RESET_URL_BASE` to the real domain |
| 3.6 | Wire Judge0 per the chosen option and execute one real submission in each of the three languages. **Java especially:** Phase 1.9 found JVM heap reservation under isolate is memory-pressure sensitive, so confirm Java runs on RapidAPI's hosted tier, whose memory config differs from local. Revisit `JUDGE0_MEMORY_LIMIT_KB` (default 256 MB, at the JVM's edge) if it is flaky |
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

### Why CI has never run: it structurally could not

`PRODUCTION_READINESS.md` records "CI has still never run for real" three separate
times (§19, §20, §26) without establishing why. The reason is not that nobody
pushed. It is that the workflow cannot fire:

| Trigger in `ci.yml` | Why it never fires |
|---|---|
| `push: branches: [main]` | **`.github/workflows/ci.yml` does not exist on `origin/main`.** The workflow only exists on `feat/conversational-interview`, which is 17 commits ahead. A push to `main` cannot run a workflow that is not on `main` |
| `workflow_dispatch` | GitHub only offers manual dispatch for workflows present on the **default branch**. Same root cause, so the button has never existed |
| `pull_request` | **This one works** — a pull request runs the workflow from the PR's head branch |

So the only way to get a CI run today is to **open a pull request from
`feat/conversational-interview` into `main`**. Pushing the branch on its own does
nothing. This is worth knowing before spending time wondering why a push produced
no run.

Two consequences for the plan:

1. Phase 0.5 is "open a PR", not "push a branch".
2. Once that PR merges, the workflow reaches `main` and the other two triggers
   start working for the first time. Until then, every CI run must come from a
   pull request.

---

## 10. Change log — Phase 1.1, config / logging / metrics

Completed 2026-09-17. Suite: **341 → 348 tests**, all passing.

Method: read `config.py`, `logging_config.py`, `metrics.py`, and `main.py`; read
`tests/test_main_observability.py`, `test_config.py`, `test_logging_config.py`, and
`test_metrics.py`; then wrote a throwaway probe that drove the real ASGI stack and
inspected the Prometheus registry directly, rather than trusting either the source
or the existing tests.

The existing tests here are good and not vacuous — they go through `TestClient`
against the real app rather than calling functions directly, and they correctly
document why `raise_server_exceptions=False` is needed. They simply did not ask
the questions below.

### Two real defects, both from one root cause

`@app.exception_handler(Exception)` is invoked by Starlette's
`ServerErrorMiddleware`, which wraps the **entire** user middleware stack from the
outside. A 500 built there has bypassed every middleware on its way out. Measured,
not reasoned:

| Probe | Before | After |
|---|---|---|
| A successful request increments `http_requests_total{status="200"}` | 0.0 → 1.0 | unchanged |
| An unhandled exception increments `http_requests_total{status="500"}` | **0.0 → 0.0** | 0.0 → 1.0 |
| A 500 carries `Access-Control-Allow-Origin` | **absent** | present |

**Defect 1 — failures were invisible in the metrics.** `http_requests_total` never
recorded a 500 from an unhandled exception, and the latency histogram never
observed it. The practical effect is worse than a missing number: a route failing
on every single request looked **identical to a route receiving no traffic at
all**. The metrics went quiet exactly when they mattered. §27 added these metrics
specifically to see failures, so this defeated the feature's purpose.

**Defect 2 — 500s reached the browser with no CORS headers.** Because
`CORSMiddleware` had been added first and was therefore *innermost*, it never saw
the error response. A browser receiving it reports an opaque CORS failure rather
than surfacing the JSON body — so the `request_id` that body exists to carry,
the entire mechanism for correlating a user report to a log line (§26), never
reached the user. This is not hypothetical for this deployment: Track A puts the
frontend on Cloudflare Pages and the API on Hugging Face Spaces, so they are on
different origins by definition and **every** 500 would have been unreadable.

### The fix

A new `ErrorHandlingMiddleware`, placed innermost, converts a route exception into
the structured 500 from *inside* the stack, so the response then travels back out
through metrics and CORS like any ordinary response. `RequestMetricsMiddleware`
also now records around a `try/except` and re-raises, so a failure is counted even
if something escapes. The middleware order was inverted so that CORS is outermost:

```
CORSMiddleware                  <- outermost: decorates every response, errors included
  RequestIdMiddleware           <- sets the contextvar the two below log with
    RequestMetricsMiddleware
      ErrorHandlingMiddleware   <- innermost: turns a route exception into a response
        ...routes
```

`add_middleware` prepends, so this order reads backwards in the source. That is
precisely why the bug existed, so the ordering now carries a comment saying so.
The original `@app.exception_handler(Exception)` is kept as a genuine fallback for
anything raised by the middleware between `ServerErrorMiddleware` and
`ErrorHandlingMiddleware`, and both paths now share one `_internal_error_response`
builder so the two bodies cannot drift apart.

### Tests added

Seven, in `tests/test_main_observability.py`:

- `ErrorPathObservabilityTests` (5): a 500 is counted, a 200 is counted, a 500
  carries CORS headers for an allowed origin *and* a readable body with a
  request id, a 500 carries the `X-Request-ID` header, and the exception detail
  is not leaked to the client.
- `MiddlewareOrderTests` (2): the stack order is asserted directly, since order
  is the actual mechanism and asserting only the symptoms would let a reorder
  pass. Counter assertions read the registry before and after and compare a
  delta, because Prometheus counters are process-global and a label combination
  that has never been observed has no sample at all rather than a sample of zero.

### Smaller items

- `backend/metrics.py` omitted `http_requests_total` and
  `http_request_duration_seconds` from `__all__`, although `main.py` imports both
  by name. Nothing was broken, but the module misreported what it exports. Fixed.
- `config.py` was read closely and **no defect was found**. The `.env` precedence
  work from §20 is correct, the validation is thorough, and the bcrypt 72-byte
  ceiling is enforced against the right unit (bytes, not characters).
- `logging_config.py` was read closely and **no defect was found**. Sentry stays
  inert without a DSN and does not enable `send_default_pii`.

### Carried forward, not fixed here

- **`/metrics` is still unauthenticated.** Confirmed by probe: an anonymous
  request returns 200 with ~5.9 KB that enumerates every route template and its
  traffic volume. This is Phase 2.3 and is deliberately left there rather than
  conflated with the error-path work.
- **`/health` still does not touch MongoDB.** Phase 2.2.
- `starlette` 1.6.0 warns on every test run that `starlette.testclient` with
  `httpx` is deprecated in favour of `httpx2`. A pinned-dependency decision, not
  a defect.

---

## 11. Change log — Phase 1.2, the database layer

Completed 2026-09-17. Suite: **348 → 371 tests**, all passing, no skips.

### This layer had never been executed by a test

`tests/README.md` and the CI workflow both said it outright: the suite mocks the
repository layer throughout and passes against an unreachable MongoDB. So no
test had ever run a real query, checked that an index exists, or exercised the
optimistic concurrency that two hot paths depend on. The CI comment even said a
service container should be added "if integration tests are introduced".

MongoDB 8.3.4 was already running locally, so this pass drove the real thing.
**Four defects surfaced the first time it was actually run.** That ratio is the
argument for the rest of Phase 1.

### Defect 1 — the concurrency version counter could be moved backwards

The most serious finding in this phase. `persist_dsa_state` accepts
`expected_state_version=None` as its unguarded path, and on that path it derived
the next version from **the caller's own `state_json`** rather than from the
database. Measured against a real MongoDB:

| Step | Stored `state_version` |
|---|---|
| After four guarded writes | 5 |
| After one blind write carrying a stale `state_json` (`state_version: 1`) | **2** |
| A writer still holding version 2 then attempts a write | **accepted** |

Once the counter rewinds, every later writer's guard is defeated and newer state
is silently overwritten. The counter is now read from the database and is the
only source of truth, so the same sequence gives 5 → 6 and the stale writer is
still rejected.

All four production call sites pass the version explicitly, so this was **latent
rather than live** — but `queries.py` exposes the parameter with a default of
`None`, so the next caller who omitted it would have got silent corruption with
no error anywhere.

### Defect 2 — `DuplicateKeyError` escaped the domain error hierarchy

`issubclass(DuplicateKeyError, DatabaseClientError)` is `False`. The route layer
catches `DatabaseClientError` in more than a dozen places, so a unique-index
violation bypassed all of them and became an **unhandled 500** rather than a
handled response.

This is reachable. `POST /dsa/start` reads with `get_dsa_session`, returns the
record if it exists, and otherwise inserts — a check-then-create against a
unique index on `(session_id, question_number)`. Two concurrent starts, which a
double-clicked button or a client retry produces, both see nothing and both
insert. The loser got a traceback.

A new `DuplicateRecordError(DatabaseClientError)` is now raised by `insert_one`
and `insert_many`. The `insert_many` message says explicitly that the batch was
only partially inserted, because pymongo's ordered insert stops at the offending
document and leaves the earlier ones in place.

`POST /dsa/start` now catches it and returns the record the winner created,
which makes the endpoint idempotent — what the check-then-create was reaching
for. Failing one of two identical requests gave the user nothing they could act
on. If the record still cannot be found after a duplicate error, the error is
re-raised rather than masked, since that combination means something else is
wrong.

### Defect 3 — one malformed row broke a whole read path

`get_all_interview_contexts` guarded `round` and then indexed `context_json`
unguarded, so a single partially written document raised `KeyError` and took out
report generation for the entire session, even though every other row was
usable. It now skips incomplete documents.

### Defect 4 — a test-visible client leak

The probe and then the new test module both triggered pymongo's warning about a
`MongoClient` being garbage collected while open. Noise rather than a defect,
but it was on every run. Closed explicitly.

### What was verified as already correct

Worth recording, because these are the parts that carry the most risk and they
hold up:

- **Every declared index exists**, with the right uniqueness, including the two
  TTL indexes with `expireAfterSeconds=0` that are the only thing bounding
  growth of `revoked_tokens` and `password_reset_tokens`.
- **Both optimistic-concurrency guards reject stale writers correctly**
  (`advance_interview_question`, `persist_dsa_state` with an explicit version).
- **Password reset tokens are genuinely atomic.** With nine threads racing the
  same token, exactly one consumed it. Expired tokens are not consumable.
- **`revoke_token` is idempotent**, so a retried logout does not 500.
- **`update_one` raises `RecordNotFoundError`** rather than silently doing
  nothing.
- **`upsert_final_report` survives concurrency.** I expected its read-then-upsert
  to race against the unique index on `session_id`; six concurrent callers
  produced exactly one document and zero errors, because MongoDB serialises
  upserts on the same filter. **My hypothesis was wrong**, and the test now
  records the real behaviour rather than the assumption.
- **`queries.py` is a pure pass-through** with no logic of its own. No defect.

### A design weakness, recorded rather than changed

`append_dsa_submission` reads the submissions array, appends in Python, and
writes the whole array back under a version guard. Under six concurrent appends,
two were stored and four were rejected with `ConcurrentUpdateError`. That is
safe — nothing is torn or duplicated, verified by test — but it is **lossy**: the
losers must retry, and serial retries do keep every submission. A `$push` would
be conflict-free and lose nothing. Not changed here because it trades away the
whole-document version guard that the rest of the record relies on, which is a
design decision rather than a bug fix.

### A guard I first misread

`_load_or_create_round_session` rebuilds a round when
`existing_mode == "dynamic" or wants_dynamic`, which resets
`current_question_index` to 0 and `state_version` to 1 — verified against the
real database as `(4, 2) → (0, 1)`, after which a writer holding version 1 was
accepted. I initially read this as a live bug that would wipe a reconnecting
conversational interview.

It is not. An earlier branch returns when `existing_mode == "dynamic" and
wants_dynamic`, so by the time that condition is reached the two are known to
differ, and it fires only on a genuine mode change. The comment is accurate. The
residual risk is narrow: a mode change concurrent with an in-flight turn could
let a version-1 writer win once. Left alone.

### Tests added

`tests/test_database_integration.py`, 23 tests in six classes: indexes,
duplicate inserts, optimistic concurrency, token paths, read-path robustness,
and persistence round trips. Each class creates a throwaway database and drops
it in `tearDownClass`, so a failing test cannot change another test's result and
real data is never touched.

They **skip when no MongoDB is reachable**, so a contributor without one still
gets a green suite. CI now runs a `mongo:7` service container — replacing the
comment that said one would be needed "if integration tests are introduced".

**And CI asserts the database is reachable before running the suite.** A service
container that failed to start would make these tests skip themselves and the
run would still be green with zero database coverage. That is the same failure
mode as the Node glob in Phase 0: a green run that tested nothing. The guard was
verified both ways locally — it passes against a live MongoDB and exits non-zero
against a dead port.

---

## 12. Change log — Phase 1.3, auth and abuse controls

Completed 2026-09-17. Suite: **371 → 378 tests**, all passing.

### First, a regression I introduced in Phase 1.2 and this phase caught

`POST /auth/signup` does a check-then-create against the unique index on
`users.email`, and it handled the resulting race by catching
`pymongo.errors.DuplicateKeyError`. Phase 1.2 changed `insert_one` to translate
that into `DuplicateRecordError` — which made the signup handler **unreachable**.
The concurrent-signup race would have produced an unhandled 500 instead of a 409.

The full suite stayed green throughout, because
`test_signup_race_surfaces_duplicate_key_as_409_not_500` mocks the repository
and sets `insert_one.side_effect = DuplicateKeyError(...)`. The mock asserted a
contract the repository no longer had. **A mocked boundary is only as good as the
exception it mocks**, and this is the same failure mode §23 of
`PRODUCTION_READINESS.md` recorded for vacuous tests — the test passes while the
thing it claims to protect is broken.

Fixed three ways, not one:

1. The route catches both `DuplicateRecordError` and the raw `DuplicateKeyError`.
   This is the one place where letting a duplicate through would create a second
   account for an address that already has one, so a caller reaching the driver
   by another path must not bypass it.
2. The existing test is parameterised over **both** error types.
3. A new test pins the repository's side of the contract, so removing or
   renaming the translation fails a test rather than silently making the signup
   race a 500 again.

The regression test was verified to be non-vacuous by reverting the route fix and
confirming it fails with `error='DuplicateRecordError'`, then restoring it.

### Defect 1 — both throttles grew without bound

`FixedWindowRateLimiter._events` and `FailureTracker._failures` are long-lived
process state, and neither ever released a key. A deque is only pruned when that
same key is checked again, and an expired lockout is only forgotten when that
same identifier is seen again — so a caller who never returns was retained
forever, still holding their timestamps.

| Scenario | Keys retained before | After |
|---|---|---|
| 10,000 one-off callers, entire window lapsed | **10,000** | 1 |
| 10,000 failed sign-ins, every lockout expired | **10,000** | 0 |

The auth limiter keys on client address and the quota limiters key on user id,
so ordinary traffic drove this, not just abuse. The quota window is an hour and
the voice quota allows 200 events, so a retained entry could hold up to 200
timestamps per user indefinitely.

Both classes now sweep expired keys, at most once per window, under the lock
already held. Bounding the sweep to once per window keeps it O(n) but amortises
to negligible per check. Six tests cover it, including the two that matter most:
a key still inside its window is never swept (so the sweep cannot become a way
to escape the limit), and an active lockout survives a sweep.

### Defect 2 — behind a proxy, every user shared one rate-limit bucket

This one follows directly from the deployment decision, and it would have
shipped.

`_client_key` uses the direct peer address and **deliberately refuses to read
`X-Forwarded-For`**, because that header is attacker-controlled and honouring it
blindly lets anyone mint a fresh budget per request. Verified: three requests
carrying three different forged `X-Forwarded-For` values all mapped to one key.
That part is correct, and well reasoned.

The consequence is what was missed. Behind a reverse proxy, `request.client` is
the **proxy**, so every user collapses into a single bucket:

- one 10-attempts-per-minute auth budget for the entire deployment
- one user tripping the limit locks out everybody
- the per-account lockout still works, but the per-IP layer becomes useless

`backend/Dockerfile` ran uvicorn with no `--proxy-headers`, and Track A puts the
backend on Hugging Face Spaces, behind exactly such a proxy. Cloud Run and any
ingress are the same.

The Dockerfile now passes `--proxy-headers --forwarded-allow-ips` driven by a new
`UVICORN_FORWARDED_ALLOW_IPS`, defaulting to `127.0.0.1` — uvicorn's own default,
so nothing changes for a direct or compose run. A proxied deployment sets it to
the proxy's address, or `*` where the container is only reachable through the
platform's ingress.

The trust semantics were verified directly against uvicorn's
`ProxyHeadersMiddleware` rather than assumed:

| Trusted hosts | Peer | `X-Forwarded-For` | Resulting client |
|---|---|---|---|
| `127.0.0.1` | `127.0.0.1` | `203.0.113.55` | `203.0.113.55` |
| `127.0.0.1` | `10.9.9.9` | `203.0.113.55` | `10.9.9.9` (ignored) |
| `*` | `10.9.9.9` | `203.0.113.55` | `203.0.113.55` |

So the default is safe and the opt-in is explicit. Documented in `.env.example`
and wired through `docker-compose.yml`, which validates with
`docker compose config`. The backend was then started with the exact new command
and served `/health` with a clean startup log, so the flags are real rather than
plausible.

**Phase 3 depends on this:** the Spaces deployment must set
`UVICORN_FORWARDED_ALLOW_IPS=*`, or per-IP rate limiting is global.

### What was verified as already correct

- **`_client_key` does not trust `X-Forwarded-For`** — verified with forged
  headers through the real ASGI stack.
- **The lockout is keyed on the submitted email, not a resolved account**, so
  attempts against non-existent addresses throttle identically. Keying on a
  found user would make the lockout a user-enumeration oracle, and the code
  says so.
- **A rejected attempt is not recorded**, so a caller hammering while blocked
  does not extend their own lockout.
- **The limiter uses a sliding window**, not a wall-clock bucket, so a caller
  cannot burst twice by straddling a boundary. Still correct after the sweep: 3
  of 5 attempts allowed against a budget of 3, and a fresh attempt allowed once
  the window passes.
- **`_verify_password` treats an over-long secret and a malformed hash as a
  failed comparison** rather than a 500.
- **The login password field is deliberately not subject to the signup policy**,
  which would otherwise lock out older accounts and disclose the policy to
  unauthenticated callers. Only a 1024-byte input guard, well above the 72 bytes
  bcrypt considers.
- **JWTs carry a `jti`**, so one token can be revoked without invalidating the
  user's other sessions.
- **Quotas key on user id, not IP** — correct, since every quota-guarded route
  is authenticated and the account is what maps to spend.

### Carried forward

The in-process counter problem itself is unchanged and remains **Phase 2.1**:
with N workers every limit is multiplied by N, nothing is shared across
instances, and a restart resets every counter. The sweep fixes unbounded growth,
not the distribution problem. On a free tier running a single worker this is
tolerable; it is the reason the deployment must stay at one worker until the
counters move into MongoDB.

---

## 13. Change log — Phase 1.4, the resume pipeline

Completed 2026-09-18. Suite: **378 → 387 tests**, all passing.

This is the application's main untrusted-input boundary: an anonymous-ish
authenticated caller hands the server an arbitrary file. The upload guard turned
out to be as good as `PRODUCTION_READINESS.md` claimed, and the defects were
just past it.

### Defect 1 — a 9-byte upload caused an unhandled 500

The upload guard validates only the first five bytes, so anything beginning
`%PDF-` reaches PyMuPDF. PyMuPDF raises its own `FileDataError`, which is not in
`resume_parser.py`'s `ResumeParserError` hierarchy — and `routes_resume.py`
catches exactly that hierarchy. So a body of `b"%PDF-1.4\n"` passed validation
and then escaped every handler as a 500.

| Upload body | Before | After |
|---|---|---|
| `%PDF-1.4\n` | `pymupdf.FileDataError` → 500 | `ResumeExtractionError` → 400 |
| `%PDF-1.4\n1 0 obj<</Type/Catalog` | `FileDataError` → 500 | 400 |
| `%PDF-` + 4096 null bytes | `FileDataError` → 500 | 400 |
| 5000 nested arrays | already 400 | 400 |
| structurally valid, no pages | already 400 | 400 |

Two aggravating details. PyMuPDF's message embeds the **absolute temp-file
path**, so the replacement message is deliberately not interpolated from it — a
test asserts the path does not appear. And because the quota is consumed by the
dependency before the handler runs, each malformed upload also burned one of the
user's ten hourly resume parses on a 500 that told them nothing.

### Defect 2 — out-of-range `max_roles` returned 500 instead of 422

`parse_resume_upload` takes `max_roles` as a bare `Form(5)` and then constructs
`ResumeParseRequest` **inside its own body**. That model declares
`Field(ge=1, le=10)`, but a pydantic `ValidationError` raised in a handler body
is not the request-parsing error FastAPI converts into a 422 — it propagates as
an unhandled exception.

| `max_roles` | Before | After |
|---|---|---|
| 0 | 500 | 422 |
| 11 | 500 | 422 |
| 99999 | 500 | 422 |

Fixed by declaring the same bounds on the Form parameter, where FastAPI can
reject during parsing. The model keeps its own `Field` as the backstop for
callers that construct it directly, and a test covers both layers. This was the
only Form parameter in the codebase with this shape — every other one is a
`bool` or an unconstrained `str`.

### What was verified as already correct

The upload guard is genuinely well built, and all six boundaries behave:

| Case | Result |
|---|---|
| `.txt` filename | 400, only PDFs supported |
| `image/png` content type | 400, must be a PDF content type |
| empty file | 400, empty |
| PDF name and type, no `%PDF-` header | 400, not a valid PDF |
| 6 MiB body against a 5 MiB cap | 413, with the limit named |
| no content type at all | 400, caught by the magic bytes |

Also confirmed:

- **The size cap is enforced while streaming**, chunk by chunk, so an oversized
  upload is abandoned rather than buffered whole.
- **No temp file leaks.** Five consecutive rejected uploads left nothing behind
  in the temp directory; cleanup happens in both the `except` and the `finally`.
- **`/resume/parse` does not exist** unless `ENABLE_LOCAL_RESUME_PATH_API` is
  set. The route is registered inside an `if` at import time, so it is absent
  rather than merely guarded — a stronger property, and now pinned by three
  tests including one asserting the setting defaults to false.
- **The happy path still works**, anchored by a test that parses the
  repository's own `sample_resume.pdf` (2,229 characters extracted). Every other
  new test here asserts a rejection, so without that anchor the fix could have
  rejected everything and still passed.
- **Every division in the scoring modules is guarded** — `resume_quality_scorer`
  checks `total == 0`, empty text, and zero vector norms before dividing, and
  falls back to keyword density if the TF-IDF path raises at all;
  `resume_skill_profiler` and `ner_extractor` both use `max(n, 1)` denominators.
  No defect found in any of the three.
- **No mutable default arguments** anywhere in `backend/nlp/`.

### Both fixes were verified non-vacuous

Reverting them both and re-running produced 4 errors and 1 failure in exactly
the new tests, then restoring returned the module to green. A regression test
that has never been seen to fail is not yet a regression test.

### A decision for you: third-party personal data in git

`sample_resume.pdf` is tracked, and it is not a synthetic fixture — it contains
a **real person's full name, phone number, and email address**. It is referenced
by the README demo walkthrough (§7.6 step 3), so §16 of
`PRODUCTION_READINESS.md` was right to keep it and my own note in §3 above was
wrong to call it unused.

That makes it a privacy question rather than a housekeeping one, and it is
sharpened by the Phase 2.6 decision to rotate secrets without rewriting
history: the file stays in every clone regardless. Options, for you to pick in
Phase 2:

1. Replace it with a synthetic resume and delete the original going forward.
   Simple, and the demo keeps working. The PII stays in history.
2. Replace it **and** purge it from history, which reverses the earlier
   no-force-push decision for this one file.
3. Keep it, on the basis that the repository is private and the person
   consented.

I have not changed it either way, because deleting a documented demo asset and
choosing how to handle someone else's personal data are both yours to decide.

---

## 14. Change log — Phase 1.5, role matching and research assets

Completed 2026-09-18. Suite: **387 → 393 tests**, all passing (re-run twice to
confirm; a 155s outlier was machine load, the steady figure is ~49s).

### Defect — a documented startup guarantee that did not exist

`backend/Dockerfile` sets `HF_HUB_OFFLINE=1` and carried this comment:

> Drop this if you change model names without also updating the pre-fetch step,
> **or startup will fail rather than silently download.**

It does not fail. Verified by pointing `_SBERT_MODEL_NAME` at a non-existent
model with `HF_HUB_OFFLINE=1` set:

| Step | Result |
|---|---|
| `get_semantic_encoder()` | raises `OSError`, caught and logged, returns `None` |
| `warmup_semantic_encoder()` | returns `False` |
| `create_app()` | **succeeds** |
| `/health` | 200, with `semantic_matching.active_backend: "token_overlap"` |

So changing the model name — or any packaging slip that leaves it out of the
image — produces a deployment that starts cleanly, passes its health check, and
serves **materially worse role matches indefinitely**. The only evidence is one
field inside a JSON body, and nothing alerts.

This is the most consequential kind of defect for a free-tier deployment,
because there is no second instance to compare against and no one watching a
dashboard. It fails open, quietly, on the feature the product is built around.

The token-overlap fallback itself is a good design choice and was kept: it is
what lets the test suite and a local run work with no model download. What
changed is that it can no longer happen unnoticed:

- It logs a warning that names the consequence and the flag, rather than only
  emitting a swallowed traceback.
- `REQUIRE_SEMANTIC_ENCODER=true` makes it a hard startup failure, raising
  `SemanticEncoderUnavailableError` with a message an operator can act on.
- `backend/Dockerfile` now **sets that flag**, because the models are baked into
  the image there, so a load failure is a packaging error rather than an
  expected condition.
- The false comment is corrected and says what actually happens.

The default stays permissive, so nothing about local development or CI changes.
Documented in `.env.example` alongside the existing `DISABLE_SEMANTIC_ENCODER`,
which is the other half of the same switch.

One deliberate inconsistency: `REQUIRE_SEMANTIC_ENCODER` is read from the
environment inside `role_matcher.py` rather than added to `AppSettings`. It sits
next to `DISABLE_SEMANTIC_ENCODER`, which is already read that way, and
splitting two halves of one switch across two modules would be worse than the
small departure from the project's usual settings home.

Six tests cover it, including that the flag does **not** fail when the encoder
is fine — otherwise setting it would make every deployment unbootable — and the
truthy-spelling table, since an operator typing `True` or `1` must get the
protection they think they asked for.

### What was verified as already correct

- **Every division is guarded.** `_safe_ratio` clamps and checks the
  denominator; the built-in BM25 uses `max(len(documents), 1)` and
  `max(len(document), 1)`; the average document length is floored at 1.0.
- **BM25 degrades cleanly.** `rank_bm25` is optional and there is a pure-Python
  Okapi implementation behind it, with `/health` reporting which is live.
- **`research_assets.py` is genuinely inert.** It indexes filenames and sizes
  and never loads or executes a checkpoint or notebook, exactly as its docstring
  claims. No defect. Note that `/models/` and `/notebooks/` are excluded by
  `.dockerignore`, so in the deployed container this registry is legitimately
  empty.

### Carried forward to Phase 2.3

`/health` is unauthenticated and includes `registered_model_names` and
`registered_notebook_names` — real filenames from the server's filesystem.
Low severity, and empty in the container, but it is unnecessary disclosure on an
anonymous endpoint and belongs with the `/metrics` hardening rather than here.

### A dependency on Phase 3

Because the Dockerfile now sets `REQUIRE_SEMANTIC_ENCODER=true`, the image will
**refuse to start** if the pre-fetch step and `_SBERT_MODEL_NAME` ever disagree.
That is the intended behaviour, and it is strictly better than degrading — but
it means the first real container run in Phase 3.4 is now also the test of that
flag. CI only builds the images, it does not run them, so this cannot be
confirmed before then.

---

## 15. Change log — Phase 1.6, question generation

Completed 2026-09-18. Suite: **393 → 404 tests**, all passing.

The largest subsystem so far (~2,700 lines across the generator, coverage
director, interview director, interview context, and conversation memory), and
the one carrying the standing 70/30 role-to-resume targeting preference.

### Defect 1 — the 70/30 split could silently collapse to 100% role

`_resolve_skill_origin` and `_resolve_skill_tier` both looked their skill up
with `skill_scores.get(skill)`, an **exact dict lookup**, while every other
comparison in the module casefolds. That mismatch is reachable rather than
theoretical: the profiler writes `skill_scores[skill]` and appends the same
`skill` to its tier lists, but `_normalise_string_list` **strips** those lists
on the way into the allocator. So a skill name arriving from the LLM with a
stray leading space is stored under `" Verilog"` and looked up as `"Verilog"`.

| Stored key | Looked up | Origin before | Tier before |
|---|---|---|---|
| `'Verilog'` | `'Verilog'` | `resume` | `strong` |
| `' Verilog '` | `'Verilog'` | **`role`** | **`general`** |
| `'verilog'` | `'Verilog'` | **`role`** | **`general`** |

And a miss was not loud. `_resolve_skill_origin` had **no fallback at all** and
returned `"role"`, so enough misses collapse the allocation toward 100% role —
losing the exact guarantee `INTERVIEW_QUESTION_TARGETING.md` exists to provide,
with no error anywhere. `_resolve_skill_tier` was less exposed because it has a
casefolded fallback scan over the tier lists, but it still misses when the skill
is not in one of them.

Both now go through a shared `_skill_score_entry` that tries the exact key first
(so the common path stays one dict hit) and falls back to a normalised scan. All
five key variants now resolve correctly.

Care was taken not to overcorrect: a genuinely absent skill must still default
to `"role"`, because that is what lets an untagged or older cached profile
degrade to tier-only allocation instead of starving the role bucket. A blank
`focus_skill` must not fuzzy-match the first entry either. Both are tested.

### Defect 2 — the prompt token budget was advisory

`conversation_memory.py` opens by describing itself as a "Compact, **bounded**
transcript". The dialogue is bounded at `MAX_DIALOGUE_TURNS = 40`. The digest
was not bounded at all: turns past that limit fold into digest lines and nothing
ever dropped them.

That matters more than it first looks, because of how `build_messages` spends
its budget. It treats the **entire digest as fixed overhead** and only ever
trims the *recent* turns. So past a certain digest length, no amount of trimming
can bring the prompt back under `budget_tokens`, the function returns an
over-budget prompt anyway, and the budget quietly becomes a suggestion. Every
director turn resends the transcript, so that is paid for on every single turn
of every session — against Groq, which is metered spend.

It is reachable inside the existing quota: `QUOTA_INTERVIEW_TURN_PER_HOUR` is
120, so 300 turns is a few hours of ordinary use.

| After 300 turns | Before | After |
|---|---|---|
| Dialogue turns retained | 40 | 40 |
| Digest lines retained | **260 and growing** | 30 |
| Assembled prompt (budget 2500) | over budget | ~1,389 tokens |
| A 500-line digest read from stored state | rendered in full | capped to 30 lines |

Capped at `MAX_DIGEST_LINES = 30`, oldest dropped first, in **both** paths:
`append_turn` bounds what gets persisted, and `build_messages` bounds what gets
rendered — the second because a digest read back from round state may predate
the cap or have been written by an older build, and `build_messages` will never
trim it.

Dropping the oldest lines is safe. Nothing downstream reads the digest: the
report is built from the `interview_responses` collection, so no candidate data
is lost. And the director needs continuity with what just happened more than it
needs the opening exchange. A normal round is unaffected — a dynamic round plans
about six questions, so the digest stays empty, which is also tested.

### What was verified as already correct

- **The 70/30 split behaves across every round size.** Measured at counts 1
  through 12 with both buckets full: the role share ranges 50%–80% and averages
  close to 70%, hitting exactly 70/30 at count 10 and the 4:2 the spec cites at
  count 6. The variance is integer rounding on small counts, and the spec says
  "roughly 70/30". Not a defect.
- **Bucket exhaustion fills the round rather than under-filling it.** With no
  resume-origin skills at all, a 6-question round still gets 6 questions, and
  the same in reverse. The leftover budget is spent from whichever bucket still
  has candidates.
- **The §19 cached-question fix is real and content-based.** A cached HR batch
  that has drifted technical ("What is the difference between SQL and NoSQL
  databases?") is correctly rejected for regeneration, while a properly
  generated batch is reused. Every malformed shape — not a mapping, empty list,
  missing `question` key, item not a mapping — returns False rather than
  raising.
- **`build_messages` cannot loop forever.** The trimming pass is a single
  forward pass, so an unsatisfiable budget returns an over-budget prompt rather
  than hanging. That is the right failure mode, and with the digest capped the
  situation should no longer arise.
- **Candidate speech is treated as data, not instructions.**
  `sanitize_candidate_text` strips the protocol's own markers, so a spoken
  "ignore previous instructions" wrapped in `<candidate_answer>` tags cannot end
  a turn early or fake metadata. This is the prompt-injection boundary and it is
  handled deliberately.

### A deliberate trade-off, recorded not changed

`interview_runtime.py` reuses a cached batch whenever `current_index > 0`,
**without** revalidating its content. So the §19 protection only applies to a
round that has not started. A session that received a drifted batch and answered
one question keeps it for the rest of the round.

That is the better trade. Regenerating mid-round would replace questions the
candidate has already answered and orphan their recorded responses. Left alone.

### Both fixes verified non-vacuous

Reverting them produced 5 failures in `test_conversation_memory` and 5 in
`test_question_generator_skill_profile`, in exactly the new tests, before
restoring.

---

## 16. Change log — Phase 1.7, interview runtime and WebSocket

Completed 2026-09-18. Suite: **404 → 414 tests**, all passing.

### The finding: no middleware protects the interview socket

Every middleware in this application is a `BaseHTTPMiddleware` subclass, and
those **never see a websocket scope**. Verified against the live stack:

| Middleware | Sees websockets? |
|---|---|
| `CORSMiddleware` | yes |
| `RequestIdMiddleware` | no |
| `RequestMetricsMiddleware` | no |
| `ErrorHandlingMiddleware` | **no** |

So the catch-all added in Phase 1.1 does not apply here, and neither do the
request metrics from §27. The handler was solely responsible for reporting and
for being observable — and it did neither.

It caught exactly two exception types. For anything else, driving the real
handler with a fake socket showed:

| Exception | Frame sent | `close()` called | Escaped handler |
|---|---|---|---|
| `WebSocketInterviewError` | yes, with detail | 1008 | no |
| `DatabaseClientError` | **none** | **never** | **yes** |
| `ConcurrentUpdateError` | **none** | **never** | **yes** |
| `ValueError` (an ordinary bug) | **none** | **never** | **yes** |

The socket had already been accepted, so mid-interview a MongoDB blip, two tabs
racing one round, or any bug dropped the candidate with an abrupt 1006 and
nothing to show them. `DatabaseClientError` and `RecordNotFoundError` were even
imported into the module, which reads like an intention to handle them that was
never finished.

And because the metrics middleware cannot see this route, **the single most
important user-facing flow in the application was the one flow with no metrics
at all.** A week of failing interviews would leave no trace in
`http_requests_total`.

Fixed with a catch-all that logs the exception with a traceback, sends a generic
error frame, and closes with **1011** rather than 1008 — "internal error", not
"policy violation", which is what 1008 means and is right for a rejected round
type but wrong for an outage. The message is deliberately generic, with the
detail going to the log: a test asserts a connection string in the exception
does not reach the candidate.

Plus a new `interview_ws_sessions_total{round, outcome}` counter, with outcomes
`completed`, `client_disconnect`, `rejected`, `already_complete`, and `error`.
Rejections and errors are counted separately on purpose — otherwise a spike in
"my session was not found" would be indistinguishable from a spike in real
failures. `round` is closed to three values by the route, so it cannot become
the unbounded label that makes a metrics endpoint fall over.

One subtlety worth recording: the `already_complete` outcome is counted
explicitly rather than left to the `else` clause, because a `return` inside a
`try` skips `else` entirely, and reconnecting to a finished round would
otherwise have been missing from the counter.

### Dead code removed, and what that surfaced

14 of the module's 34 imports were unused — residue from the refactor that moved
transport into `interview_runtime.py` and per-round behaviour into
`interview_engines/`.

Removing them **broke three tests**, which is the interesting part. They did
`from backend.api.ws_interview import _quota_blocked`, reaching through this
module for a function that actually lives in `interview_runtime.py`. The unused
import was acting as an accidental re-export, and the tests had come to depend
on it.

The tests were repointed at the real home rather than the import being restored.
Worth noting as a hazard: "unused import" is a claim about the module, not about
the repository, and a test can quietly make dead code load-bearing.

### What was verified as already correct

- **The §23 WebSocket quota metering is real and complete.** `INTERVIEW_TURN` is
  charged at all three engine turn paths (two dynamic, one scripted), and
  `VOICE` at both the synthesis and the transcription paths — six sites, sharing
  one allowance with the equivalent REST routes.
- **An exhausted quota does not tear down the socket.** It sends an error frame
  and returns the runtime to listening, so a candidate mid-interview is not
  disconnected because a budget rolled over. That was §23's stated intent and it
  holds.
- **An unsupported round type is refused before `accept()`.** Nothing is
  accepted for a round that does not exist.
- **A clean client disconnect is not reported as an error**, and now has its own
  counter outcome.
- **Cleanup runs on every path.** The `finally` cancels any in-flight turn
  commit and stops TTS regardless of how the handler exits.
- **Reconnect and resume work** for a conversational round — established in
  Phase 1.2, where the `_load_or_create_round_session` guard was confirmed to be
  a genuine XOR that returns the existing record rather than rebuilding it.

### A real risk, documented rather than half-fixed

`_transcribe_captured_audio` consumes the captured audio **immediately**:

```python
pcm_blob = b"".join(runtime.pending_pcm_frames)
runtime.reset_audio_capture()
```

And the socket handler's `finally` cancels the turn-commit task. Together that
means a disconnect during the 1.5-second end-of-turn grace, or during
transcription and scoring, **loses that answer**: the audio is already cleared,
so there is nothing to retry from. If the cancellation lands between persisting
the response and advancing the question index, the candidate can also be
re-asked a question that already has a recorded answer.

I have not changed this, deliberately. Fixing it properly means choosing between
disconnect latency and durability — most likely shielding the commit once
transcription has begun, the way `_stop_tts` already shields its task, and making
the persist step idempotent so a retry cannot double-record. Both are design
decisions about what a dropped connection should mean mid-answer, not bug fixes,
and they want the engine code in scope. It belongs in Phase 2 alongside the other
state-durability work, and it is listed there now.

---

## 17. Change log — Phase 1.8, evaluation, feedback, reporting

Completed 2026-09-18. Suite: **414 → 419 tests**, all passing.

### Defect — the "degrade gracefully" path did not degrade

`_persist_report_snapshot` exists for one reason, stated in its own docstring:
the snapshot is derived from round collections that were **just read
successfully**, so a failure to *store* it must not block *showing* it. The user
gets the computed report with `persisted=False` and a note, never a 5xx.

It caught `DatabaseClientError` only. But `upsert_final_report` reaches
`find_one_and_update` directly, with no translation layer, so a driver-level
failure arrives raw:

| Write failure | Before | After |
|---|---|---|
| `DatabaseClientError` | degraded | degraded |
| `DuplicateRecordError` | degraded | degraded |
| `OperationFailure` | **escaped → 500** | degraded |
| `WriteError` | **escaped → 500** | degraded |
| `ExecutionTimeout` | **escaped → 500** | degraded |

These are not exotic. The chosen deployment is a MongoDB Atlas **M0 cluster
capped at 512 MB**, and a full cluster rejects writes with `OperationFailure`.
So the single failure this function most needed to survive was the one it did
not, and the candidate lost a report that had already been computed correctly.

Now catches `PyMongoError` alongside the domain error. A test asserts the
handler did **not** become a bare `except`: a `TypeError` from a genuine bug in
the snapshot code still propagates, because that is not a persistence failure
and must not be reported to the user as "we could not save this".

### The bigger finding: this gap is systemic, and it is Phase 2 work

The same shape exists everywhere. `MongoRepository` calls pymongo directly in
most of its methods, so raw driver errors escape every handler that catches the
domain type. Measured on a read path:

| Error | Result |
|---|---|
| `DatabaseClientError` | correct 503 |
| `AutoReconnect` (an Atlas failover) | **escapes → 500** |
| `ExecutionTimeout` (a slow shared-tier query) | **escapes → 500** |

And the blast radius is the whole API:

| Route module | `except DatabaseClientError` handlers |
|---|---|
| `routes_dsa.py` | 15 |
| `routes_interview.py` | 13 |
| `routes_resume.py` | 7 |
| `routes_assessment.py` | 5 |
| `routes_report.py` | 3 |
| `routes_workflow.py` | 2 |
| `routes_auth.py` | 1 |
| **total** | **46** |

Every one of those means a transient database event on a shared-tier cluster
surfaces as "internal error" rather than "temporarily unavailable" — which
changes whether a client retries, and which is exactly wrong for Atlas M0 where
failovers are expected.

**I did not fix this here, deliberately.** The right fix is one translation
boundary in the repository, extending the `DuplicateKeyError` translation added
in Phase 1.2 to the rest of the driver's error tree. Editing 46 handlers is the
wrong shape, and rewriting the data layer's error contract at the end of a long
session is how a regression gets introduced into every data path at once. It is
tracked as **Phase 2.12** with this evidence.

The report-persist path was fixed now because it is the one whose entire
documented purpose is to degrade, and because it is the last step of the
candidate's whole journey — losing a finished report is the most expensive
possible moment to fail.

### Partial-round scoring: examined, and not a defect

I set out to check whether an abandoned round produces a misleading score. A
round planned for six questions where the candidate answered one, scoring 0.95:

| Basis | Reported score |
|---|---|
| Mean over **answered** questions (current behaviour) | 0.95 |
| Mean over **planned** questions | 0.16 |

Averaging over answered questions is the defensible choice — a candidate should
not be scored down for questions never asked — **provided the report also says
how few were answered**. It does: `response_count`, `total_questions`,
`completed_question_count`, `status`, `scored_stage_count`, and
`completed_stage_count` are all present in the builders.

The overall score averages non-null round scores, so a round never started
contributes nothing rather than a zero. Also right.

So the backend is sound here, and the honesty of the report now rests entirely
on the frontend actually displaying those counts next to the score. That is a
real question, and it is now explicitly part of **Phase 1.13**.

### What was verified as already correct

- **Every division is guarded.** Four aggregation sites: `_average` returns
  `None` on an empty list, the per-round mean is inside an `if responses`
  branch, the DSA pass-rate checks `total_count <= 0`, and the overall mean
  checks `if not values`. No zero-division anywhere.
- **A zero score is distinguished from an absent score.** `_coerce_numeric_score`
  returns `None` rather than `0.0` for unscored input, and there is explicit
  handling for the case where a stored `total_score` of `0.0` came from a
  progress fallback rather than a real evaluation. Conflating those two would
  make an unstarted round look like a failed one.

---

## 18. Change log — Phase 1.9, the DSA subsystem

Completed 2026-09-18. Suite: **419 → 435 tests**, all passing.

### First, a correction to this plan's own framing

Section 3 called the static safety gate "the highest-risk code in the repo — it
is what stands between a user and arbitrary execution." That is **wrong**, and
getting it right changes the whole audit.

The gate (`code_executor.safety_gate`) is a **defense-in-depth pre-filter**. The
real isolation boundary is **Judge0**, which runs every submission in an
ephemeral, isolated container. The `exec(USER_CODE)` that looked alarming is
inside the harness *string* submitted to Judge0, not run in the backend process.
So a gap in the gate lets a submission reach a sandbox that is already designed
to contain it — an erosion of a secondary layer, not a remote-code-execution
hole.

That reframing matters because it inverts the risk priority. With Judge0 as the
real boundary, the gate's more damaging failure mode is not a missed attack — it
is a **false positive that rejects a legitimate candidate's correct solution**.
And that is exactly what was found.

### Defect 1 — the gate rejected everyday DSA operations (Python)

`_FORBIDDEN_ATTRIBUTE_CALLS` matched attribute-call *names* on any object, so
these all failed:

| Legitimate code | Blocked with |
|---|---|
| `nums.remove(x)` | "Calling attribute remove is not allowed" |
| `seen.remove(x)` (a set) | same |
| `s.replace(' ', '')` | "Calling attribute replace is not allowed" |
| `[w.replace('a','b') for w in words]` | same |

`list.remove`, `set.remove`, and `str.replace` are bread-and-butter for the
exact problems a DSA round poses ("remove duplicates", "clean a string"). The
entries were meant to catch `os.remove` / `Path.replace`, but **those dangerous
forms all require importing `os`, `pathlib`, or `shutil` — every one of which is
already in `_FORBIDDEN_MODULES`.** You cannot reach a dangerous `.remove` /
`.replace` without a banned import, so the two entries added nothing but false
rejections. Both were removed.

### Defect 2 — safe C++ string formatting was rejected

The C++ token list is substring-matched, and it contained `printf(` and
`scanf(`. So `std::sprintf` (which contains `printf(`), `std::snprintf`,
`std::fprintf`, and `std::sscanf` (which contains `scanf(`) were all blocked —
pure in-memory string formatting that cannot escape anything. `printf`/`scanf`
themselves are not dangerous either: they do console I/O against the same
stdin/stdout the harness already feeds, exactly like `cin`/`cout`. Both tokens
were removed; the genuinely dangerous ones (`system(`, `popen(`, `fork(`,
`socket(`, `freopen(`, and the file/thread `#include`s) stay.

### Both fixes were proven not to open a hole

The security direction was tested explicitly, not assumed. After the fixes,
every dangerous form is still rejected:

| Attempt | Still blocked? |
|---|---|
| `import os; os.remove(...)` | yes — at the import |
| `from os import remove` | yes |
| `import pathlib; Path(...).unlink()` | yes |
| `import subprocess` | yes |
| `eval` / `exec` / `open` / `__import__` | yes |
| C++ `system(` / `popen(` / `fork(` / `socket(` | yes |
| Java `Runtime.getRuntime` / `ProcessBuilder` / `System.exit` | yes |

16 tests in `tests/test_dsa_safety_gate.py` cover both directions, verified
non-vacuous by reverting both fixes (7 failures) and restoring. This is the
first direct test coverage the gate has had.

### Execution verified end to end against a live Judge0

A local Judge0 stack was running, so the three-language path (the one §20 of
`PRODUCTION_READINESS.md` repaired) was exercised for real, not mocked:

| Language | Result |
|---|---|
| Python | **Accepted, 2/2** cases |
| C++ | **Accepted, 2/2** cases |
| Java | passes via the project's own `test_dsa_multilanguage_execution` (stable across repeated runs) |

### An honest caveat on Java, carried to Phase 3.6

While verifying Java, a standalone reproduction hit `Could not reserve enough
space for … object heap` — a JVM startup failure — **consistently**, even though
the project's own multilanguage test passes Java against the same Judge0 URL with
the same memory limit and near-identical code. The application code path is
identical for all three languages, so this is a Judge0/`isolate` host-level
heap-reservation characteristic on this machine, not an application defect, and I
could not make it fail through the project's own contract.

I am **not** claiming a fix for something I cannot reproduce through the real
interface, and I am **not** claiming Java is bulletproof. The truthful state:
Java passes the project's execution test locally; JVM heap reservation under
`isolate` is memory-pressure sensitive; and the chosen deployment runs Judge0 on
**RapidAPI's hosted tier**, which has entirely different memory configuration.
**Java execution must be verified against the actual deployed Judge0 in Phase
3.6**, and that step is flagged there. The default `JUDGE0_MEMORY_LIMIT_KB` of
256 MB sits at the JVM's lower edge and is worth revisiting if hosted Java
proves flaky.

### What was verified as already correct

- **The gate fails safe.** Unparseable Python raises `SafetyViolationError`, not
  an unhandled crash, and an oversized submission is rejected before parsing.
- **`safety_gate` runs before every execution.** It is called at the single
  `_execute_cases` chokepoint that both `run_sample` and `run_submission` pass
  through, so no execution path skips it.
- **The C++/Java "no main()" checks are correct** — the harness supplies `main`,
  and a submission declaring its own is refused rather than silently colliding.

---

## 19. Change log — Phase 1.10, the voice subsystem

Completed 2026-09-18. Suite: **435 → 446 tests**, all passing.

### Defect 1 — the deployment does not use the TTS engine it is configured for

`docker-compose.yml` sets `TTS_PROVIDER=piper`, and `piper` is the documented
default in `backend/voice/tts.py`. But **no stage of `backend/Dockerfile`
installs the piper binary**, and `piper-tts` is not in `requirements.txt`. So
`_resolve_piper_executable()` returns `None` and every containerised synthesis
falls through to edge-tts.

That fallback is correct behaviour. The defect is that it was **invisible**: the
"provider unavailable" branch logged at `DEBUG`, which is below the documented
default `LOG_LEVEL=INFO`. Measured with the real chain, zero log lines were
emitted at INFO. The operator's explicit configuration was being ignored with no
record anywhere.

Confirmed it is not container-only: `_PIPER_EXE` is `None` on this development
machine too, so local runs have also been silently using edge-tts all along.

A second, worse case sat underneath it. When *every* provider reports itself
unavailable, `last_error` is never set, so the chain returned `None` with no log
at all — making "voice is entirely dead for this deployment" indistinguishable
from "this turn had nothing to say".

| Situation | Before | After |
|---|---|---|
| Configured provider unavailable, fallback works | silent (DEBUG) | **WARNING** naming the consequence |
| Every provider unavailable | silent `None` | **WARNING** that voice is disabled |
| Configured provider works | silent | silent (unchanged) |
| A *fallback* provider unavailable | DEBUG | DEBUG (unchanged) |

Only the **first** entry in the chain warns, because only that one is the
operator's choice; a later provider being unconfigured is routine and warning
about it would be noise that gets filtered and stops being read. Both the
happy-path silence and the fallback-stays-quiet behaviour are tested, so the
warning cannot degrade into background chatter.

### An asymmetry that looked like a bug and is not

The fallback chains are not symmetrical:

| `TTS_PROVIDER` | Chain |
|---|---|
| `edge` | edge → elevenlabs → piper |
| `elevenlabs` | elevenlabs → edge → piper |
| `piper` | piper → edge **(no elevenlabs)** |

This is deliberate and now says so in the code. Piper is the local, offline,
zero-cost engine; an operator selecting it has implicitly declined paid API
calls, and silently failing over to a metered service would spend money they did
not ask to spend. edge-tts is free, so it stays as the safe fallback. A test
pins it, because the asymmetry reads like an oversight and is exactly the kind
of thing a later cleanup would "fix".

### Defect 2 — a truncated audio frame would raise

`EnergyVAD.process` called `np.frombuffer(pcm_bytes, dtype=np.int16)`, which
raises `ValueError` on an odd byte count. PCM frames arrive from a browser over
a network, where a truncated frame is a normal consequence of a flaky
connection rather than a programming error. A trailing half-sample carries no
information, so it is now dropped instead of raising. Detection itself is
unchanged, verified by tests covering both the onset and offset windows.

**Reachability, stated honestly:** this is currently **not reachable**, because
`EnergyVAD` has no callers at all.

### `backend/voice/vad.py` is dead code

A repository-wide search, including the tests, found **zero imports** of
`EnergyVAD`. It was superseded by client-side detection: the browser runs Silero
VAD and sends each whole utterance as one binary frame, with turn boundaries
arriving as explicit control messages. `_handle_audio_frame` says so outright —
"there is nothing for the server to detect here".

I did not delete it. Phase 1.7 was a lesson in exactly that: removing 14 "unused"
imports broke three tests that were reaching through the module. This one is
genuinely unreferenced, but a server-side VAD is the natural fallback if a client
ever cannot run Silero, and the module is small and self-contained. Instead its
docstring now states plainly that it is unused, why, and that it has therefore
**never processed a real audio stream** — so nobody mistakes it for proven code.

Deleting it is a reasonable call and is yours to make.

### A decision for you: the 63 MB voice model

`backend/data/tts_models/en_US-lessac-medium.onnx` is **63.2 MB**, tracked in
git, copied into the backend image by `COPY backend/ /app/backend/` — and
unusable, because the binary that would read it is not installed. It is the bulk
of the 60 MB `.git` noted in §0.

Three coherent options:

1. **Install piper in the image.** Makes the configuration true and removes a
   network dependency from voice output — edge-tts relies on an undocumented
   free Microsoft endpoint, which is fragile for production. `onnxruntime` is
   already installed as a transitive dependency. Costs image size and CPU
   synthesis time on a 2-vCPU free tier.
2. **Make `edge` the honest default and drop the model.** Immediately smaller
   image, configuration matches reality, one less moving part. Accepts the
   external-endpoint dependency.
3. **Leave it**, now that the mismatch at least announces itself in the logs.

I recommend **(1)** for the production path and **(2)** if the free tier's CPU
budget matters more than voice reliability — but this trades cost, latency, and
voice quality against each other, which is a product decision rather than a bug.

### What was verified as already correct

- **The synthesis-failure path already logged at WARNING** (added in commit
  `361b90e`). The gap was only the *unavailable* path, which is a different
  branch — a provider that is configured and fails is not the same as one that
  was never there.
- **Returning `None` rather than raising when no audio is available is right.**
  Callers continue the round silently because the question text is already on
  screen; stalling a live interview over missing audio would be worse.
- **`EnergyVAD`'s detection logic is correct** — onset and offset hysteresis
  both behave as specified, and RMS on int16 cannot overflow float32.
