#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Diarized Transcriber GUI into a self-contained Windows directory.

.DESCRIPTION
    Must be run from the project root (the directory that contains recorder_gui.spec).
    Produces dist\DiarizedTranscriber\ — copy the whole folder to distribute.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean
#>
param(
    [switch]$Clean   # delete build\ and dist\ before building
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Guard: must run from project root ───────────────────────────────────────
$specFile = Join-Path $PSScriptRoot "recorder_gui.spec"
if (-not (Test-Path $specFile)) {
    Write-Error "recorder_gui.spec not found. Run build.ps1 from the project root."
    exit 1
}

# ── Optional clean ───────────────────────────────────────────────────────────
if ($Clean) {
    foreach ($dir in @("build", "dist")) {
        $p = Join-Path $PSScriptRoot $dir
        if (Test-Path $p) {
            Write-Host "Removing $p ..."
            Remove-Item $p -Recurse -Force
        }
    }
}

# ── Run PyInstaller ──────────────────────────────────────────────────────────
Write-Host "Building DiarizedTranscriber ..."
pyinstaller --noconfirm recorder_gui.spec

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$outDir = Join-Path $PSScriptRoot "dist\DiarizedTranscriber"
Write-Host ""
Write-Host "Build complete: $outDir"
