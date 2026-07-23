<#
.SYNOPSIS
  Provision the fast zhiyue combat simulator backend (research-only).

.DESCRIPTION
  Clones the zhiyue sts2_env combat core into ./vendor/sts2-rl-agent (git-ignored)
  at the pinned commit and installs its runtime deps into the rl venv.

  The simulator source is deliberately NOT committed to this repo: upstream ships
  no LICENSE and is "for research and educational purposes" only. Provenance and
  the local parity fixes applied on top of the pinned commit are recorded in
  vendor/sts2-rl-agent/UPSTREAM.md.

  NOTE: a fresh clone gives the *pristine* upstream commit. The parity fixes in
  UPSTREAM.md ("Local changes") were validated against the real game and are what
  rl/tests/sim expects. For exact parity, restore vendor/ from the team snapshot
  rather than a bare clone.

.EXAMPLE
  pwsh scripts/setup_sim.ps1
#>
[CmdletBinding()]
param(
    [string]$Commit = "1b7e7ce35e608722650763938c153ea8bc370333",
    [string]$Repo   = "https://github.com/zhiyue/sts2-rl-agent",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot   = Split-Path -Parent $PSScriptRoot
$vendorDir  = Join-Path $repoRoot "vendor/sts2-rl-agent"
$python     = Join-Path $repoRoot "rl/.venv/Scripts/python.exe"

if ((Test-Path (Join-Path $vendorDir "sts2_env/core/combat.py")) -and -not $Force) {
    Write-Host "vendor/sts2-rl-agent already populated; skipping clone (use -Force to re-clone)." -ForegroundColor Yellow
} else {
    if (Test-Path $vendorDir) { Remove-Item -Recurse -Force $vendorDir }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("sts2-sim-" + [guid]::NewGuid().ToString("N"))
    Write-Host "Cloning $Repo @ $Commit ..." -ForegroundColor Cyan
    git clone --filter=blob:none --no-checkout $Repo $tmp
    git -C $tmp sparse-checkout init --cone
    git -C $tmp sparse-checkout set sts2_env docs
    git -C $tmp checkout $Commit
    New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
    Copy-Item -Recurse -Force (Join-Path $tmp "sts2_env") (Join-Path $vendorDir "sts2_env")
    $docDst = Join-Path $vendorDir "docs"
    New-Item -ItemType Directory -Force -Path $docDst | Out-Null
    Copy-Item -Force (Join-Path $tmp "docs/CARDS_REFERENCE.md") (Join-Path $docDst "CARDS_REFERENCE.md")
    Remove-Item -Recurse -Force $tmp
    Write-Warning "Cloned pristine upstream. Reapply the parity fixes in UPSTREAM.md or restore vendor/ from the team snapshot for exact parity."
}

if (Test-Path $python) {
    Write-Host "Installing sim runtime deps into rl venv ..." -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $repoRoot "requirements-sim.txt")
} else {
    Write-Warning "rl venv not found at $python; run 'pip install -r requirements-sim.txt' in your env."
}

Write-Host "Done. Verify with:  `$env:PYTHONPATH='vendor/sts2-rl-agent'; python -c 'import sts2_env.cards'" -ForegroundColor Green
