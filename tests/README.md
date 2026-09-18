# Tests README

This folder contains backend-oriented regression and verification tests.
35 modules, **404 tests**.

## 1. Main command for checking the project

Use the wrapper. It sets the environment the suite needs and passes the one flag
that must not be dropped:

```powershell
Set-Location E:\interview_simulator
.\scripts\run_tests.ps1
```

```bash
# POSIX
./scripts/run_tests.sh
```

### Why there is a wrapper: `-t .` is load-bearing

If you invoke `unittest` directly, it **must** be this form:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p "test_*.py"
```

Dropping `-t .` does not give you a test failure. It kills the interpreter:

```
exit -1073741819   (0xC0000005, access violation)
```

...with **zero output**, before a single test runs. Without `-t .`, unittest sets
the top-level directory to `tests/` rather than the repository root, which puts
`tests/` on `sys.path` in place of the root. Every module then resolves its
imports against the wrong root.

This is easy to misdiagnose as a broken environment or a flaky native dependency,
because each of the modules passes when run on its own, and all of them import
cleanly together in one process. Only discovery with the wrong top-level
directory fails, and it fails silently. An earlier revision of this file
recommended the form without `-t .` as "the safest repo-level test command",
and `.github/workflows/ci.yml` inherited the same mistake. Use the wrapper.

## 2. Focused commands

### A single module

```powershell
.\scripts\run_tests.ps1 -Module tests.test_report_route
```

### DSA-focused suites

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dsa_intelligence_layer tests.test_dsa_progression_and_persistence
```

Naming modules explicitly, as above, does not go through discovery, so `-t .`
does not apply and these are safe to run directly.

### Per-test names

```powershell
.\scripts\run_tests.ps1 -VerboseTests
```

## 3. Required environment

`backend/config.py` refuses to build settings without `AUTH_JWT_SECRET`, so
every module that imports the app fails at import if it is unset. The wrapper
supplies a throwaway value, and also points `MONGO_URI` at a port nothing
listens on so that an accidental real database call fails loudly instead of
quietly passing against leftover local state. CI sets both the same way.

The virtualenv should be built on **Python 3.11** — the version
`backend/Dockerfile` and CI ship — so local runs exercise the interpreter that
is actually deployed.

## 4. What these tests cover

Representative areas include:

- certified DSA bank execution, progression, and persistence
- interview evaluation and progression, the dynamic engine, engine selection
- conversation memory, interview context, and the interview director
- NLP linguistic analysis and NER
- question generation for HR and technical rounds, role alignment, skill profiles
- resume upload invariants and the resume skill profiler
- auth, quotas, and rate limiting
- report route correctness, feedback generation
- streaming primitives and streaming TTS, TTS provider selection
- config precedence, logging config, metrics, observability
- workflow reset behaviour and research asset registration

## 5. Database integration tests

`tests/test_database_integration.py` drives `MongoRepository` against a real
MongoDB: 23 tests covering the unique and TTL indexes, optimistic concurrency
on both hot paths, the single-use guarantee on password reset tokens under a
concurrent race, and the persistence round trips. Each test class creates a
throwaway database and drops it afterwards, so it never touches real data.

They **skip themselves** when no database is reachable, so a contributor
without MongoDB still gets a green suite. Set `MONGO_TEST_URI` if yours is not
on `mongodb://localhost:27017`. CI runs a MongoDB service container and asserts
it is reachable before the suite, because a silent skip would mean a green run
with no database coverage at all.

This file exists because nothing else here executes a real query. Four defects
were found the first time the layer was actually run, including one where the
optimistic-concurrency version counter could be moved backwards, defeating the
guard for every later writer.

## 6. Reviewer guidance

For normal checking, run:

1. backend startup
2. frontend tests and build (`npm test` then `npm run build`, Node 21+ — the
   test script uses a glob that older Node passes through as a literal path)
3. the full backend suite via `scripts/run_tests`

That combination shows the repository installs, compiles, and exercises the
major backend flows.

## 7. What these tests do not cover

Worth knowing before treating a green run as proof the system works:

- Judge0 is not exercised. The DSA execution tests do not run code.
- No test drives the interview WebSocket against a real socket.
- Groq is never called.
- Every module *except* `test_database_integration.py` mocks the repository
  layer and passes against an unreachable MongoDB, so outside that one file no
  real query, index, or concurrency behaviour is validated.

A green suite means the logic is consistent with its own assumptions. It does
not mean the deployed system works; that needs the live smoke test.
