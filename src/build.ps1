<#
.SYNOPSIS
    Build 3DMigoto Mod Viewer into a standalone portable app.
.DESCRIPTION
    Thin wrapper around build.py that picks a working Python launcher.
    Any arguments are forwarded, e.g.:  .\build.ps1 --onedir
#>
param([Parameter(ValueFromRemainingArguments = $true)] $Args)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
}
if (-not $python) { throw 'Python 3.9+ not found on PATH.' }

if ($python -eq 'py') {
    & py -3 build.py @Args
} else {
    & $python build.py @Args
}
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
