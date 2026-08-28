# AI Interview Simulator
The project is an AI-powered interview simulator for fresher roles. It supports resume parsing, role matching, assessment, technical interview, project discussion, HR interview, a role-conditional DSA round, and final report generation.

## 1. What The Project Does

The application flow is:

1. Upload a PDF resume
2. Extract text from the resume
3. Parse the resume into structured JSON using Groq
4. Match the candidate to suggested roles
5. Run the assessment round
6. Run the technical interview round
7. Run the DSA round for coding-screen roles
8. Run the project discussion round
9. Run the HR round
10. Generate the final report

## 2. How To Run The Project

There are two practical ways to run this repository.

### Option 1: Quick local check

This path checks the local project setup and the main service surfaces.

This checks:

- backend startup
- backend health endpoints
- frontend build
- backend tests
- general project wiring

This does not require:

- a real Supabase project
- a Groq API key
- Judge0

### Option 2: Full live run

This path runs the full browser application with live parsing, authentication, persistence, interview progression, and report generation.

This requires:

- a real Supabase project
- a valid Groq API key
- frontend Supabase configuration
- microphone access for voice features
- Judge0 only if DSA code execution must be demonstrated

## 3. Main Tech Stack

- Backend: FastAPI
- Frontend: React + Vite
- Database: Supabase
- Resume parsing: PyMuPDF + Groq
- Role matching: sentence-transformers + BM25
- STT: faster-whisper
- TTS: Piper by default, ElevenLabs optional
- DSA execution: Judge0
- Editor: Monaco

## 4. Repository Structure

```text
interview_simulator/
|- backend/           FastAPI backend, NLP, DSA, voice, database helpers
|- frontend/          React + Vite frontend
|- scripts/           startup, smoke, verification, and maintenance scripts
|- tests/             backend regression tests
|- models/            optional research checkpoint files
|- notebooks/         optional research notebooks
|- sample_resume.pdf  sample resume for demo purposes
|- smoke_test.py      quick endpoint smoke snapshot
```

## 5. Minimum System Requirements

Recommended environment:

- Windows host machine
- PowerShell
- Node.js 18 or later
- Python 3.11 recommended
- internet access for full live execution
- Docker Desktop only if DSA code execution must be demonstrated

## 6. Quick Local Run

Follow these steps to start and check the project locally without preparing cloud services first.

### Step 1: Open the repository root

```powershell
Set-Location E:\interview_simulator
```

### Step 2: Create a virtual environment if `.venv` is missing

```powershell
py -3.11 -m venv .venv
```

If `.venv` already exists, skip this step.

### Step 3: Install backend dependencies

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### Step 4: Install frontend dependencies

```powershell
Set-Location E:\interview_simulator\frontend
npm install
```

### Step 5: Start the backend

```powershell
Set-Location E:\interview_simulator
.\scripts\start_backend.ps1
```

What this script does:

- checks that `.venv` exists
- checks `backend/requirements.txt`
- installs missing backend packages if required
- starts FastAPI with reload on port `8000`

Important note:

- on first startup the backend may remain at `Waiting for application startup` for some time while the semantic matching model warms
- wait for `Application startup complete` before treating the backend as failed

### Step 6: Check backend health

Open a second PowerShell window and run:

```powershell
Set-Location E:\interview_simulator
Invoke-RestMethod -Uri http://127.0.0.1:8000/health | ConvertTo-Json -Depth 6
```

Expected result:

- JSON response with `"status": "ok"`

Useful additional checks:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/resume/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/assessment/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/interview/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/dsa/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/voice/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/auth/status
```

### Step 7: Build the frontend

```powershell
Set-Location E:\interview_simulator\frontend
npm run build
```

Expected result:

- Vite completes successfully
- `frontend/dist/` is produced

### Step 8: Run the backend test suite

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

### Step 9: Run the quick smoke snapshot

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe smoke_test.py
```

This prints a JSON summary of major backend endpoints.

### Expected result

After completing these steps:

- the backend starts
- `/health` returns `ok`
- the frontend build passes
- the backend test suite runs
- the repository is confirmed to install and execute locally even without external service credentials

## 7. Full Live Run

Follow these steps to run the complete application in the browser.

### 7.1 Required external services

For the full live flow, the evaluator needs:

- a Supabase project
- a Groq API key
- frontend Supabase anon key
- optional Judge0 instance for DSA code execution

### 7.2 Backend `.env` configuration

Create or update the root `.env` file.

Example:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

GROQ_API_KEY=your-groq-api-key
GROQ_RESUME_MODEL=llama-3.3-70b-versatile
GROQ_EVALUATOR_MODEL=llama-3.1-8b-instant
GROQ_FEEDBACK_MODEL=llama-3.1-8b-instant

JUDGE0_API_BASE_URL=http://127.0.0.1:2358

TTS_PROVIDER=piper
PIPER_VOICE=en_US-lessac-medium

ENABLE_LOCAL_RESUME_PATH_API=false
```

Meaning of the main variables:

- `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` enable persisted backend flows
- `GROQ_API_KEY` enables live resume parsing and live question generation
- `JUDGE0_API_BASE_URL` enables DSA execution
- `TTS_PROVIDER` defaults to Piper; ElevenLabs is optional, not required
- after changing `.env`, restart the backend because settings are cached at startup

### 7.3 Frontend `.env.local` configuration

Create `frontend/.env.local` using `frontend/.env.example` as the template.

Example:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_EMAIL_REDIRECT_TO=http://127.0.0.1:5173
```

After changing this file, restart the frontend dev server.

### 7.4 Supabase schema requirement

Important note:

- the full live product expects a Supabase project that already contains the main application tables
- this repository includes SQL for later-stage tables and security helpers, but it does not include a complete fresh-project SQL rollout for every base table

The live application expects these core tables to exist in the target Supabase project:

- `users`
- `sessions`
- `resume_data`
- `role_matches`
- `interview_round_contexts`
- `interview_responses`
- `assessment_sessions`
- `dsa_sessions`
- `final_reports`

SQL files included in this repository:

- `backend/database/assessment_sessions.sql`
- `backend/database/dsa_sessions.sql`
- `backend/database/final_reports.sql`
- `backend/database/supabase_auth_rls.sql`
- `backend/database/sessions_owner_required.sql`
- `backend/database/cleanup_ownerless_sessions.sql`
- `backend/database/cleanup_and_enforce_sessions_owner.sql`

How to use them:

- for an existing compatible Supabase project, apply the included SQL files that are missing
- for a completely new Supabase project, create the base application schema first, then apply the included assessment, DSA, report, and RLS SQL files
- the cleanup scripts are migration helpers and are not part of a normal fresh setup

### 7.5 Start the backend and frontend

Backend:

```powershell
Set-Location E:\interview_simulator
.\scripts\start_backend.ps1
```

Frontend:

```powershell
Set-Location E:\interview_simulator\frontend
npm run dev
```

Expected frontend URL:

```text
http://127.0.0.1:5173
```

### 7.6 Full live demo steps

1. Open the frontend in the browser.
2. Sign in with Supabase authentication.
3. Upload `sample_resume.pdf` or another valid PDF resume.
4. Wait for structured parsing and role suggestions.
5. Select a suggested role.
6. Complete the assessment round.
7. Open the Technical Interview page and answer questions.
8. If the role is DSA-enabled, complete the DSA round.
9. Complete the Project Discussion round.
10. Complete the HR round.
11. Open the Report page.

## 8. Optional Judge0 Setup For DSA Execution

Judge0 is only required for the live DSA coding round.

The local Judge0 bundle is present in:

```text
E:\interview_simulator\.local\judge0\judge0-v1.13.1
```

To start Judge0 locally:

```powershell
Set-Location E:\interview_simulator\.local\judge0\judge0-v1.13.1
docker compose up -d
```

To check Judge0 directly:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:2358/languages
```

To check DSA health through the backend:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/dsa/health
```

If Judge0 is not running:

- the DSA page can still load
- code execution and scoring will be degraded or unavailable

## 9. Exact Commands For Execution And Checking

These are the main commands an evaluator can run.

### Backend setup

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### Frontend setup

```powershell
Set-Location E:\interview_simulator\frontend
npm install
```

### Backend start

```powershell
Set-Location E:\interview_simulator
.\scripts\start_backend.ps1
```

### Frontend start

```powershell
Set-Location E:\interview_simulator\frontend
npm run dev
```

### Backend health

```powershell
Set-Location E:\interview_simulator
Invoke-RestMethod -Uri http://127.0.0.1:8000/health | ConvertTo-Json -Depth 6
```

### Frontend build

```powershell
Set-Location E:\interview_simulator\frontend
npm run build
```

### Full backend test suite

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

### Focused DSA test suites

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest tests.test_dsa_intelligence_layer tests.test_dsa_progression_and_persistence
```

### Endpoint smoke snapshot

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe smoke_test.py
```

### DSA bank verification

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe scripts\run_verification.py
```

### Full DSA integration smoke

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe scripts\smoke_dsa_flow.py
```

Requirements for the full DSA integration smoke:

- real Supabase credentials in `.env`
- `DSA_SMOKE_USER_ID`
- running Judge0
- `dsa_sessions` table applied

## 10. Expected Results

### After the quick local run

You should see:

- backend starts locally
- `/health` returns `status: ok`
- frontend build succeeds
- tests run
- some live auth or persistence routes may remain limited without real secrets

### After the full live run

You should see:

- sign-in works through Supabase
- resume upload and parsing work with a valid Groq key
- role matching returns suggestions
- assessment and interview stages progress in the UI
- report data is available after round completion

### After the full DSA run

You should see:

- DSA problems load for DSA-enabled roles
- Monaco editor appears
- Judge0-backed sample run and final submit work
- DSA report metrics appear after completion

## 11. Research Assets Note

The `models/` and `notebooks/` folders are present for academic and presentation support.

Current behavior:

- they are indexed by `backend/research_assets.py`
- they appear in the backend `/health` response under `research_assets`
- they are not executed by the live interview runtime

## 12. Troubleshooting

### Backend health does not answer immediately

Check the backend terminal first. On some runs the app pauses at `Waiting for application startup` while the semantic model warms.

### Backend does not pick up a newly pasted API key

Restart the backend. Backend settings are cached at process startup.

### Frontend auth appears disabled

Check `frontend/.env.local` and then restart `npm run dev`.

### Resume parsing fails on a file

Common reasons:

- scanned or image-only PDF
- encrypted PDF
- too little extractable text
- missing or invalid Groq API key

### DSA health reports Judge0 unavailable

Common reason:

- Docker is not running, so nothing is listening on `http://127.0.0.1:2358`

### Voice output is unavailable

Check `/voice/health`. The UI can still continue in a degraded path because prompt text remains visible and typed fallback exists for failures.

## 13. Submission Checklist

Before sharing the folder, verify the following:

1. This `README.md` is present at the repository root.
2. The backend starts with `.\scripts\start_backend.ps1`.
3. `http://127.0.0.1:8000/health` returns `status: ok`.
4. The frontend builds with `npm run build`.
5. The backend tests run with `unittest`.
6. Secrets are not shared in `.env` or `frontend/.env.local`.
7. If a full live demo is expected, the target Supabase project and Groq key are ready.
8. If a DSA demo is expected, Judge0 is running.

## 14. Final Note

Use the quick local run if you only need install, startup, build, health, and test verification.

Use the full live run if you want the end-to-end browser flow with live services.
