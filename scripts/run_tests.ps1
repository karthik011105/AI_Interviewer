# Run the backend test suite the only way that works.
#
# The `-t .` flag is load-bearing. Without it, unittest sets the top-level
# directory to tests/ instead of the repository root, which puts tests/ on
# sys.path in place of the root. On Windows that combination kills the
# interpreter outright: an access violation (0xC0000005, exit -1073741819) with
# no output at all -- not a test failure, a process death. With `-t .` the same
# suite reports 341 passing tests.
#
# This wrapper exists so that invocation never has to be retyped from memory.
# CI runs the identical form; see .github/workflows/ci.yml.
#
# Usage:
#   .\scripts\run_tests.ps1              # whole suite
#   .\scripts\run_tests.ps1 -Verbose     # per-test names
#   .\scripts\run_tests.ps1 -Module tests.test_auth

[CmdletBinding()]
param(
    # Run a single module instead of discovering the whole suite.
    [string]$Module = "",

    # Print each test name as it runs.
    [switch]$VerboseTests
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtualenv at .venv. Create one on Python 3.11 (the version backend/Dockerfile and CI ship) and install backend/requirements.txt."
    exit 1
}

# AUTH_JWT_SECRET is mandatory: backend/config.py refuses to build settings
# without it, so every test module that imports the app would fail at import.
# The value is irrelevant to the tests, only its presence. Matches CI.
if (-not $env:AUTH_JWT_SECRET) {
    $env:AUTH_JWT_SECRET = "local-test-secret-not-used-outside-tests"
}

# Point at a port nothing listens on, so an accidental real database call fails
# loudly instead of quietly passing against leftover local state. Matches CI.
if (-not $env:MONGO_URI) {
    $env:MONGO_URI = "mongodb://127.0.0.1:59999"
}

$pyArgs = @("-m", "unittest")

if ($Module) {
    $pyArgs += $Module
} else {
    $pyArgs += @("discover", "-s", "tests", "-t", ".", "-p", "test_*.py")
}

if ($VerboseTests) {
    $pyArgs += "--verbose"
}

# Windows PowerShell 5.1 wraps every stderr line from a native executable in an
# ErrorRecord (NativeCommandError). unittest writes its entire progress report
# and summary to stderr, and the dependency stack emits deprecation warnings
# there too, so with $ErrorActionPreference = "Stop" still in force this script
# would abort on the first harmless warning and never reach the test result.
# The exit code is the thing that decides pass or fail, so read that instead.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $python @pyArgs
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousPreference
}

if ($code -eq -1073741819) {
    Write-Host ""
    Write-Warning "Access violation (0xC0000005). If you edited this script, check that -t . is still being passed -- dropping it reproduces exactly this crash."
}

exit $code
