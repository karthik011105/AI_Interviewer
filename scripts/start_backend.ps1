param(
    [int]$Port = 8000,
    [switch]$SkipDependencySync,
    [switch]$ForceDependencySync
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$requirementsPath = Join-Path $repoRoot "backend\requirements.txt"
$requirementsStampPath = Join-Path $repoRoot ".venv\backend-requirements.sha256"

if (-not (Test-Path $pythonPath)) {
    Write-Error "Project virtual environment not found at $pythonPath"
    exit 1
}

if (-not (Test-Path $requirementsPath)) {
    Write-Error "Backend requirements file not found at $requirementsPath"
    exit 1
}

if (-not $SkipDependencySync) {
    $requirementsHash = (Get-FileHash -Path $requirementsPath -Algorithm SHA256).Hash
    $installedRequirementsHash = ""

    if (Test-Path $requirementsStampPath) {
        $installedRequirementsHash = (Get-Content -Path $requirementsStampPath -Raw).Trim()
    }

    if ($ForceDependencySync -or $requirementsHash -ne $installedRequirementsHash) {
        Write-Host "Syncing backend dependencies from $requirementsPath ..."
        & $pythonPath -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Dependency sync failed while installing backend requirements."
            exit $LASTEXITCODE
        }
        Set-Content -Path $requirementsStampPath -Value $requirementsHash -NoNewline
    }
    else {
        Write-Host "Backend dependencies already match backend/requirements.txt"
    }
}

Set-Location $repoRoot
& $pythonPath -m uvicorn backend.main:app --reload --port $Port