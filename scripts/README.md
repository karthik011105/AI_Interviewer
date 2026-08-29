# Scripts README

This folder contains helper scripts for startup, smoke checks, verification, and data maintenance.

## 1. Most Important Execution Scripts

### `start_backend.ps1`

Purpose:

- sync backend dependencies when needed
- start FastAPI on port 8000

Run:

```powershell
Set-Location E:\interview_simulator
.\scripts\start_backend.ps1
```

### `smoke_dsa_flow.py`

Purpose:

- run a persisted DSA integration smoke through the live app logic

Requirements:

- a reachable MongoDB instance (`MONGO_URI`)
- `DSA_SMOKE_USER_ID` set to a real user `_id` from the `users` collection
- working Judge0

Run:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe scripts\smoke_dsa_flow.py
```

### `run_verification.py`

Purpose:

- verify the certified DSA problem bank and deterministic selector behavior

Run:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe scripts\run_verification.py
```

## 2. Other Scripts in This Folder

### `add_problem.py`

Imports an additional DSA problem pack into the local bank.

### `analytics_update.py`

Scaffold for refreshing problem performance analytics.

### `calibrate_confidence_thresholds.py`

Calibrates interview confidence thresholds from saved transcripts.

### `expand_question_bank.py`

One-shot helper to append more assessment questions to the bank.

### `generate_role_questions.py`

Role-specific question generation helper.

### `import_questions_from_apis.py`

Imports question content from multiple sources.

### `smoke_resume_flow.py`

Local resume parsing smoke harness using a generated PDF and in-process FastAPI app.

Note:

- this script relies on the local path-based parse surface and development assumptions
- for shared-folder review, treat it as a developer smoke helper rather than the main public execution path

### `update_role_catalog.py`

Updates the role catalog data used by the project.

## 3. Root-Level Helper Scripts Not Inside This Folder

The repository root also contains these useful files:

- `smoke_test.py` for an endpoint snapshot smoke check
- `inspect_db.py`, `inspect_db_v2.py`, and `inspect_db_v3.py` for direct Supabase inspection during debugging

Run `smoke_test.py` like this:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe smoke_test.py
```