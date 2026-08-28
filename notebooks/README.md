# Notebooks README

This folder stores research notebooks that support the academic side of the project.

Current files:

- `Job_Matcher.ipynb`
- `Parser.ipynb`
- `T5_Question_Generation_FineTuning (2).ipynb`

## Important Note

These notebooks are not executed by the live application runtime.

Current project behavior:

- the backend registers them through `backend/research_assets.py`
- they appear as optional research assets in the backend `/health` response
- they are included for documentation, experimentation, and presentation support

## How to Check

Start the backend and call:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

Look for the `research_assets` section in the response.

If you want to inspect a notebook manually, open it in VS Code or Jupyter Notebook.