# Tests README

This folder contains backend-oriented regression and verification tests.

## 1. Main Command for Checking the Project

Use the repository virtual environment and run the full suite with `unittest`:

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

This is the safest repo-level test command to include in submission instructions.

## 2. Focused Commands

### DSA-focused suites

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest tests.test_dsa_intelligence_layer tests.test_dsa_progression_and_persistence
```

### Single test module example

```powershell
Set-Location E:\interview_simulator
.\.venv\Scripts\python.exe -m unittest tests.test_report_route
```

## 3. What These Tests Cover

Representative areas include:

- certified DSA bank execution
- DSA progression and persistence
- interview evaluation and progression
- NLP linguistic analysis and NER
- question generation for HR and technical rounds
- report route correctness
- workflow reset behavior
- research asset registration

## 4. Reviewer Guidance

For normal checking, run:

1. backend startup
2. frontend build
3. full backend test suite

That combination is usually enough to show that the repository installs, compiles, and exercises the major backend flows.