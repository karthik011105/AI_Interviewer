# Frontend README

This folder contains the React + Vite frontend for the AI Interview Simulator.

## 1. What Lives Here

- `src/pages/` stage pages such as Resume Intake, Assessment, Technical, DSA, Project Discussion, HR, and Report
- `src/components/` reusable UI pieces such as AuthPanel, WorkflowResetControl, CodeEditor, and voice-related UI
- `src/lib/` frontend helpers including auth, routing, and workflow utilities
- `package.json` frontend scripts

## 2. Frontend Setup

Install dependencies:

```powershell
Set-Location E:\interview_simulator\frontend
npm install
```

## 3. Frontend Environment File

Use `frontend/.env.example` as the template and create `frontend/.env.local`.

Required values:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

This is the only variable the frontend reads. Sign-in goes through the
backend's own `/auth` routes, so no third-party client keys belong here.

Restart the frontend dev server after changing the file.

## 4. Start the Frontend

```powershell
Set-Location E:\interview_simulator\frontend
npm run dev
```

Default local URL:

```text
http://127.0.0.1:5173
```

## 5. Build Check

```powershell
Set-Location E:\interview_simulator\frontend
npm run build
```

This is the main non-interactive check to confirm the frontend compiles successfully.

## 6. Reviewer Flow

1. Start the backend first.
2. Start the frontend with `npm run dev`.
3. Open the app in the browser.
4. Sign in with email and password (first signup creates the account).
5. Upload a resume.
6. Continue through the available stages.
7. Open the Report page to verify persisted results.

## 7. Important Notes

- protected routes require authentication
- DSA is shown only for DSA-enabled roles
- the Report page is data-backed, not static mock content
- sign-out has a local fallback path so the UI clears even if remote revoke fails

## 8. Frontend Validation Summary

Use these commands for review:

```powershell
Set-Location E:\interview_simulator\frontend
npm run dev
npm run build
```