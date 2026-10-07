# Runbook

How to run, check, fix, and recover the deployed Interview Simulator.

**Deployment (planned, see `PRODUCTION_DEPLOYMENT_PLAN.md`):**

| Piece | Where | Free tier |
|---|---|---|
| Frontend | Cloudflare Pages | Static site |
| Backend | Hugging Face Spaces (Docker) | 2 vCPU / 16 GB, sleeps when idle |
| Database | MongoDB Atlas M0 | 512 MB, **no automatic backups** |
| Code execution | Judge0 CE via RapidAPI | ~50 submissions/day |
| LLM | Groq | Rate limited per minute/day |

> Steps marked **(confirm in Phase 3)** depend on the real accounts and are
> filled in when the deployment is done. Everything else is verified against the
> code as it stands.

---

## 1. Is it working?

| Check | Command | Healthy answer |
|---|---|---|
| Process alive | `curl https://<backend>/health` | `200` |
| Ready to serve (database reachable) | `curl https://<backend>/ready` | `200`. **`503` = MongoDB unreachable** |
| Frontend | open `https://<frontend>` | Page loads, sign-in works |
| Metrics | `curl -H "Authorization: Bearer $METRICS_TOKEN" https://<backend>/metrics` | Prometheus text. `404` = wrong/missing token (deliberate, not a bug) |

`/health` never touches dependencies; `/ready` pings MongoDB. Neither checks
Judge0 or Groq — test those with a real DSA submission or interview turn.

**Uptime monitor:** point a free monitor (UptimeRobot, Better Stack) at
`/ready` every 5 minutes. Note: on Spaces the backend sleeps when idle, so the
first request after a quiet period is slow (model loading) — that is a cold
start, not an outage. Record the real cold-start time here after Phase 3.4:
**(confirm in Phase 3)**.

---

## 2. Reading logs

The backend logs **one JSON object per line** to stdout. Every HTTP response
carries an `X-Request-ID` header, and every log line for that request has the
same `request_id` — so a user's error report with that ID leads straight to
the relevant lines.

Where to read them: Space page → **Logs** tab **(confirm in Phase 3)**.

Startup warnings worth reading after every deploy (they mean a setting is
wrong, not that the app crashed):

| Warning mentions | Meaning | Fix |
|---|---|---|
| email / SMTP | Password-reset mail will not be delivered. Users still see "check your email" — on purpose, so accounts can't be probed | Fix `SMTP_*` / `EMAIL_PROVIDER` |
| metrics public | `METRICS_PUBLIC` is set; anyone can read `/metrics` | Unset it, use `METRICS_TOKEN` |
| TTS fallback | Configured voice provider unavailable, using edge-tts | Expected unless you set piper/elevenlabs |

**Errors with stack traces** go to Sentry if `SENTRY_DSN` is set. No request
bodies, cookies, or user identity are sent (resumes and transcripts pass
through this process — keep it that way).

---

## 3. Deploying

**Before any deploy:** CI on the pull request must be green (backend tests
twice — in-memory and MongoDB rate-limit stores — frontend tests + build,
Docker images build).

**Backend:** push to the Space's repository / trigger a rebuild **(confirm in
Phase 3)**. Watch the logs until startup finishes, then check `/ready` → `200`.

**Frontend:** Cloudflare Pages builds from the repo. `VITE_API_BASE_URL` is
**baked in at build time** — changing the backend URL means rebuilding the
frontend, not just changing a setting.

**After every deploy, a 2-minute smoke test:** sign in → open an interview
round (checks the WebSocket ticket path) → submit one DSA solution (checks
Judge0) → open a report.

---

## 4. Rolling back

The fastest fix for a bad deploy is to redeploy the last good commit:

```bash
git log --oneline            # find the last good commit
git revert <bad-commit>      # preferred: keeps history, goes through CI
git push
```

Then redeploy as in §3. Cloudflare Pages can also roll back instantly from its
dashboard (Deployments → older deployment → Rollback) **(confirm in Phase 3)**.

**Database changes are not rolled back by code rollbacks.** There are no
schema migrations today, so this has not mattered yet.

---

## 5. Rotating a secret

All secrets live in the platform's secret settings (Space → Settings →
Secrets; Cloudflare → Settings → Environment variables), never in the repo.
After changing one, restart/redeploy so the process picks it up.

| Secret | What happens when you rotate it |
|---|---|
| `AUTH_JWT_SECRET` | **Every user is signed out** (all tokens become invalid). Do this if it may have leaked. Must be long and random: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `MONGO_URI` (password) | Change the password in Atlas → Database Access first, then update the secret. App returns 503 between the two steps |
| `GROQ_API_KEY` | Create the new key in Groq console, update secret, then delete the old key |
| Judge0 / RapidAPI key | Same order: new key, update, revoke old |
| `SMTP_PASSWORD` | Same order. Check startup log for email warnings afterwards |
| `METRICS_TOKEN` | Update the uptime/metrics scraper at the same time |
| `SENTRY_DSN` | Harmless to rotate; errors stop reporting until updated |

**If a secret was committed to git:** rotate it immediately. Removing it from
the file is not enough — it stays in history. (Project decision: rotate, don't
rewrite history.)

---

## 6. When something breaks

| Symptom | Likely cause | What to do |
|---|---|---|
| `/ready` returns 503, API calls return 503 | MongoDB unreachable: Atlas paused, IP allowlist, password changed | Atlas dashboard: is the cluster running? Network Access allows the Space? Credentials match? |
| Brief burst of 503s, then fine | Atlas failover (normal on M0) | Nothing — clients retry. These are 503 by design, not 500 |
| Interview questions/feedback slow or failing | Groq rate limit or outage | Logs show `Groq completion attempt …` / `rate limit`. Wait, or lower `QUOTA_INTERVIEW_TURN_PER_HOUR` |
| DSA "run code" fails for everyone | Judge0 daily cap (~50/day on RapidAPI) or key problem | RapidAPI dashboard usage. Lower `QUOTA_DSA_EXECUTION_PER_HOUR` to spread the cap |
| Users get `429` | Per-user quota or login throttle hit — working as intended | Raise the matching `QUOTA_*` / `AUTH_RATE_LIMIT_*` only if legitimate users are hitting it |
| "Connection ticket expired/already used" on interview start | Ticket is 60 s, single use. Clock skew or a client reusing a URL | Click Reconnect (fetches a new ticket). If constant, check server clock |
| Interview socket drops on idle | Platform proxy idle timeout | Reconnect resumes the round; answers already being saved are finished, not lost |
| Code editor doesn't load | Browser blocked a bundled asset (CSP) | Browser console. CSP is in `frontend/Dockerfile` (`CSP_POLICY`) |
| Password-reset email never arrives | SMTP misconfigured (users still see success, on purpose) | Startup log email warning; provider dashboard; sender address verified with the provider? |
| Sign-in impossible for everyone | `AUTH_JWT_SECRET` changed or missing | Config error at startup if missing; if changed, users simply sign in again |

---

## 7. Data and retention

- **Atlas M0 has no automatic backups.** Before anything destructive, export:
  `mongodump --uri "$MONGO_URI" --out backup-$(date +%F)`.
- **Pruning old sessions** (dry run is the default; nothing is deleted without `--apply`):

  ```bash
  python scripts/prune_old_sessions.py --days 90           # shows what would go
  python scripts/prune_old_sessions.py --days 90 --apply   # deletes
  python scripts/prune_old_sessions.py --days 0 --user-id <id> --apply   # one user's data, e.g. on request
  ```

  Accounts (`users`) are never deleted by this script. The retention window
  is a product decision still open.
- **Storage:** Atlas dashboard shows usage against the 512 MB limit. DSA
  submissions are capped at 20 per session so no single document can hit
  MongoDB's 16 MB limit.

---

## 8. Cost and quota watch (monthly)

| Service | Where to look | Act when |
|---|---|---|
| Groq | Groq console → usage | Regular 429s in logs |
| Judge0 / RapidAPI | RapidAPI dashboard | Nearing the daily cap |
| Atlas | Cluster → Metrics → storage | Over ~400 MB (80%) |
| Hugging Face | Space settings | Space paused or hardware changed |

Per-user limits are the `QUOTA_*` settings (per hour, per user), and they are
stored in MongoDB, so they survive restarts.

---

## 9. Configuration reference

The full list with explanations is in `.env.example`. The ones that matter in
production:

| Variable | Production value |
|---|---|
| `APP_ENV` | `production` |
| `AUTH_JWT_SECRET` | long random secret |
| `MONGO_URI`, `MONGO_DB_NAME` | Atlas connection |
| `RATE_LIMIT_BACKEND` | `mongo` (already set in the image) |
| `CORS_ALLOWED_ORIGINS` | the exact frontend URL |
| `AUTH_PASSWORD_RESET_URL_BASE` | frontend URL of the reset page |
| `GROQ_API_KEY` | Groq key |
| `EMAIL_PROVIDER`, `SMTP_*` | real provider, not `console` |
| `METRICS_TOKEN` | random secret; leave `METRICS_PUBLIC` unset |
| `SENTRY_DSN` | optional, recommended |
| `UVICORN_FORWARDED_ALLOW_IPS` | the platform proxy, so per-user rate limits see real client IPs **(confirm in Phase 3)** |
| `ENABLE_LOCAL_RESUME_PATH_API` | unset / false |
| `TTS_PROVIDER` | `edge` |
