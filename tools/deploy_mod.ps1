<#
.SYNOPSIS
    Builds and deploys the STS2_MCP mod into the local Slay the Spire 2 install.

.PARAMETER GameDir
    Path to the Slay the Spire 2 installation directory.
    Falls back to the STS2_GAME_DIR environment variable if not specified.

.PARAMETER Configuration
    Build configuration (default: Release).

.EXAMPLE
    .\deploy_mod.ps1 -GameDir "D:\Steam\steamapps\common\Slay the Spire 2"
#>
param(
    [string]$GameDir,
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

if (-not $GameDir) { $GameDir = $env:STS2_GAME_DIR }
if (-not $GameDir) {
    Write-Host "ERROR: Game directory not specified. Pass -GameDir or set `$env:STS2_GAME_DIR." -ForegroundColor Red
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$modDir = Join-Path $repoRoot "mod"
$outDir = Join-Path $modDir "out\STS2_MCP"
$modsDir = Join-Path $GameDir "mods"

Write-Host "=== Building STS2_MCP ($Configuration) ===" -ForegroundColor Cyan
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) { $dotnet = "C:\Program Files\dotnet\dotnet.exe" }
& $dotnet build (Join-Path $modDir "STS2_MCP.csproj") -c $Configuration -o $outDir -p:STS2GameDir="$GameDir"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path $modsDir)) { New-Item -ItemType Directory -Path $modsDir -Force | Out-Null }

Write-Host "=== Deploying to $modsDir ===" -ForegroundColor Cyan
Copy-Item (Join-Path $outDir "STS2_MCP.dll") (Join-Path $modsDir "STS2_MCP.dll") -Force
Copy-Item (Join-Path $modDir "mod_manifest.json") (Join-Path $modsDir "STS2_MCP.json") -Force

$confPath = Join-Path $modsDir "STS2_MCP.conf"
if (-not (Test-Path $confPath)) {
    '{ "port": 15526 }' | Set-Content -Path $confPath -Encoding utf8
}

Write-Host ""
Write-Host "=== Deployed ===" -ForegroundColor Green
Write-Host "Launch Slay the Spire 2, enable mods, then check: curl http://localhost:15526/health"
