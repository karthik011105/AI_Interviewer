# Deploy the backend to a Hugging Face Docker Space.
#
#   .\scripts\deploy_hf_space.ps1 -Space your-hf-username/prepforge-api
#
# A Space builds the Dockerfile at its own root, so this assembles a small
# folder - backend/, the backend Dockerfile moved to the root, and the
# README header Hugging Face reads its settings from - and pushes it to the
# Space's git repo. git asks for your Hugging Face username and an access
# token with "write" permission the first time (huggingface.co/settings/tokens).
#
# Secrets (GROQ_API_KEY, MONGO_URI, AUTH_JWT_SECRET, ...) are NOT sent from
# here: set them in the Space under Settings -> Variables and secrets.
param(
    [Parameter(Mandatory = $true)][string]$Space
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "prepforge-hf-space"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null

# Only files git tracks, so nothing local (.env, caches, drafts) is uploaded.
Push-Location $repoRoot
try {
    $files = git ls-files backend
    foreach ($file in $files) {
        $target = Join-Path $stage $file
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file -Destination $target
    }
    $commit = (git rev-parse --short HEAD).Trim()
}
finally {
    Pop-Location
}

Move-Item (Join-Path $stage "backend\Dockerfile") (Join-Path $stage "Dockerfile")

$readme = @"
---
title: PrepForge API
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8000
pinned: false
---

Backend for PrepForge, built from commit $commit.
"@
Set-Content -Path (Join-Path $stage "README.md") -Value $readme -Encoding utf8

Push-Location $stage
try {
    git init -q -b main
    git add -A
    git commit -q -m "Deploy backend from $commit"
    git push --force "https://huggingface.co/spaces/$Space" main
    if ($LASTEXITCODE -ne 0) { throw "git push to the Space failed." }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Pushed. The Space builds now (the first build takes 10-20 minutes):"
Write-Host "  https://huggingface.co/spaces/$Space"
