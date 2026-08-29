# Backend README

This folder contains the FastAPI backend for the AI Interview Simulator.

## 1. What Lives Here

- `api/` REST and WebSocket routes
- `nlp/` resume parsing, question generation, answer evaluation, feedback, role matching
- `dsa/` problem selection, execution, analysis, scoring, reports
- `voice/` STT and TTS integration
- `database/` MongoDB repository, domain query helpers, shared error types
- `main.py` FastAPI entrypoint
- `config.py` environment-backed runtime settings

## 2. Backend Startup

Recommended command from the repository root:

```powershell
Set-Location E:\interview_simulator
.\scripts\start_backend.ps1
```

This starts:

```text
uvicorn backend.main:app --reload --port 8000
```

Important note:

- the first startup can pause at `Waiting for application startup` while the semantic matching model warms
- do not treat `/health` as failed until the startup phase finishes

## 3. Dependency Installation

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## 4. Required Configuration

The backend reads the root `.env` file.

Required for persisted live flows:

- `MONGO_URI`
- `MONGO_DB_NAME`
- `AUTH_JWT_SECRET` (the backend refuses to start without it)
- `GROQ_API_KEY`

Needed for DSA execution:

- `JUDGE0_API_BASE_URL`

Useful voice settings:

- `TTS_PROVIDER`
- `PIPER_VOICE`
- `PIPER_EXECUTABLE`
- `PIPER_MODELS_DIR`
- `ELEVENLABS_API_KEY` when using ElevenLabs instead of Piper

After changing `.env`, restart the backend process.

## 5. Important Health Checks

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/resume/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/assessment/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/interview/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/dsa/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/voice/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/auth/status
```

If the backend has just been started and these commands fail immediately, wait for startup completion in the backend terminal and then run them again.

## 6. Common Backend Validation Commands

Run the full backend test suite:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Run focused DSA suites:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest tests.test_dsa_intelligence_layer tests.test_dsa_progression_and_persistence
```

Run the endpoint snapshot smoke test:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe smoke_test.py
```

## 7. Main Runtime Areas

### Resume and role matching

- route file: `api/routes_resume.py`
- parser: `nlp/resume_parser.py`
- role matcher: `nlp/role_matcher.py`

### Interview runtime

- REST routes: `api/routes_interview.py`
- WebSocket runtime: `api/ws_interview.py`
- evaluation: `nlp/answer_evaluator.py`
- feedback: `nlp/feedback_generator.py`

### DSA runtime

- routes: `api/routes_dsa.py`
- executor: `dsa/code_executor.py`
- analyzer: `dsa/code_analyzer.py`
- evaluator: `dsa/dsa_evaluator.py`
- report engine: `dsa/report_engine.py`

### Voice runtime

- routes: `api/routes_voice.py`
- STT: `voice/stt.py`
- TTS: `voice/tts.py`

## 8. Database Schema

MongoDB is schemaless, so there are no SQL migrations to apply. Collections are
created on first write, and the required indexes are created automatically by
`MongoRepository._ensure_indexes()` the first time the backend connects.

Access ownership is enforced in the application layer rather than by database
policies: every session-scoped route resolves the parent session and calls
`ensure_session_access()` (see `api/auth.py`) before reading or writing.

## 9. Notes for Reviewers

- The backend exposes both simple health routes and full live interview/DSA routes.
- Some features degrade gracefully when optional services are unavailable.
- `research_assets.py` only indexes local models and notebooks; it does not execute them.