# Production Deployment Plan

Branch: `feat/conversational-interview` · Updated: 2026-10-07 · Test suite: **533 passing**

This is the short, current-state version. The full per-phase change log (what was
wrong, how it was measured, how each fix was proven) lives in git history — see
this file at commit `5d04e8d`, and the commit messages themselves.

---

## Target architecture (decided 2026-09-17)

```
Cloudflare Pages (frontend, VITE_API_BASE_URL baked at build)
        |  HTTPS + WSS
        v
Hugging Face Spaces — Docker, port 7860 (16 GB RAM: ML models stay in-process)
        |                         |
        v                         v
MongoDB Atlas M0 (512 MB)     Judge0 CE via RapidAPI (~50 submissions/day)
                 Groq free tier (LLM)
```

| Decision | Choice |
|---|---|
| Backend host | Hugging Face Spaces (Docker SDK) |
| Judge0 | RapidAPI free tier — HF Spaces cannot run Judge0's privileged `isolate` |
| Git history | Rotate leaked secrets, no history rewrite, no force-push |
| Python | 3.11 everywhere |
| TTS | edge-tts (piper binary was never installed; model removed) |

Upgrade path if needed: an Oracle Always Free arm64 VM running app + Mongo + Judge0.

---

## Status

| Phase | State |
|---|---|
| 0 — Fix CI/test tooling | Done (CI command fixed, `scripts/run_tests.ps1`, Python 3.11, frontend tests in CI). **CI has never run on GitHub — needs the PR opened** |
| 1 — Audit all 14 subsystems | Done. ~15 real defects fixed with regression tests |
| 2 — Hardening | 10 of 14 done — see below |
| 3 — Deployment | Blocked on accounts/credentials from you |
| 4 — Operations docs | Not started |

### Phase 2 — done

| # | Item |
|---|---|
| 2.1 | Rate limits/quotas in MongoDB (`RATE_LIMIT_BACKEND=mongo` in image; CI runs suite with both backends) |
| 2.2 | `/health` (liveness) split from `/ready` (pings Mongo, 503 on failure) |
| 2.3 | `/metrics` behind `METRICS_TOKEN` bearer auth, 404 when unauthorized |
| 2.4 | SMTP verifies TLS certs, supports port 465, warns on misconfig at startup |
| 2.5 | Security headers via nginx template; CSP report-only until a browser pass |
| 2.7 | Dead 63 MB piper model removed; TTS default → edge |
| 2.8 | Sentry wired (no-op without `SENTRY_DSN`, no PII sent) |
| 2.9 | DSA submissions capped at 20 per session (16 MB doc limit); `scripts/prune_old_sessions.py` for retention |
| 2.11 | Interview turn commit drains instead of being cancelled on disconnect |
| 2.12 | pymongo errors translated at one boundary → Atlas failover is 503, not 500 |

### Phase 2 — remaining

| # | Item | Owner |
|---|---|---|
| 2.10 | Load-shed / timeout review: Groq 20 s × 3 retries, Judge0 4 s; a few concurrent interviews can saturate one worker | Me |
| 2.13 | Access token rides in the WebSocket URL (`?access_token=`) and lands in proxy logs; valid 24 h. Replace with a short-lived single-use ticket | Me |
| 2.14 | Monaco loads from `cdn.jsdelivr.net`; self-host via `loader.config()` + Vite assets | Me |
| 2.9b | Backup/export before pruning (Atlas M0 has no free backups) | Me, after you pick a retention window |
| 2.6 | Rotate the Judge0 Postgres/Redis passwords committed in `1f249f5` | **You** |

---

## Phase 3 — Deployment

| # | Task |
|---|---|
| 3.1 | Add RapidAPI's two headers (`X-RapidAPI-Key`, `X-RapidAPI-Host`) to `backend/dsa/code_executor.py` — currently supports only one auth header; set `QUOTA_DSA_EXECUTION_PER_HOUR` to fit ~50/day |
| 3.2 | Provision Atlas M0: user, network access, indexes; connection test from outside |
| 3.3 | Make the image Spaces-compatible: port 7860, secrets from Space settings, no writes to read-only paths |
| 3.4 | Deploy backend; record real cold-start time and peak memory |
| 3.5 | Deploy frontend to Cloudflare Pages; set `CORS_ALLOWED_ORIGINS`, `AUTH_PASSWORD_RESET_URL_BASE` |
| 3.6 | One real Judge0 submission each in Python, C++, Java (Java heap is memory-sensitive under isolate) |
| 3.7 | Full live smoke test: signup → resume → role match → scripted + voice interview over WSS → DSA → report → password reset → sign-out revokes token. Zero console errors |
| 3.8 | Confirm quotas/rate limits survive a restart on the live instance |

## Phase 4 — Operate

`RUNBOOK.md` (redeploy, rollback, rotate a secret, read logs) · uptime check on `/ready` ·
Groq / Judge0 / Atlas quota monitoring · update `README.md` to the deployed reality.

---

## What I need from you

1. **Open the PR** so CI runs for the first time:
   https://github.com/karthik011105/AI_Interviewer/compare/main...feat/conversational-interview?expand=1
2. **Rotate the Judge0 credentials** (2.6).
3. **Accounts for Phase 3:** MongoDB Atlas, Hugging Face, RapidAPI (Judge0 CE), Sentry DSN, an SMTP provider (Brevo / Resend / Gmail app password).
4. **Decisions:**
   - `sample_resume.pdf` contains a real person's name, phone, and email. Replace with a synthetic one? (Stays in history either way unless rewritten.)
   - `data/clickhouse_sample.jsonl` is orphaned — OK to delete?
   - Session retention window for `prune_old_sessions.py`.

## Known trade-offs (deliberate, not bugs)

- Workflow reset is not atomic across collections; it is idempotent, so a retry completes it. Transactions would need a replica set.
- Audio from a disconnect *before* transcription starts is discarded; recovering it would mean persisting raw audio.
- A partial round's score is the mean over answered questions; the UI shows answered/total beside it.
