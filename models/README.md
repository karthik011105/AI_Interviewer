# Models README

This folder stores local research checkpoint files that are present in the repository for project support and academic context.

Current files:

- `job_matcher_model.pth`
- `parser_model.pth`
- `t5_model.pth`

## Important Note

These files are not loaded by the live interview runtime.

Current project behavior:

- the backend indexes them through `backend/research_assets.py`
- they appear as optional research assets in the backend `/health` response
- they do not affect the active resume, interview, DSA, or report flows

## How to Check

Start the backend and call:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

Look for the `research_assets` section in the response.