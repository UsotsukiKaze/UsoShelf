$ErrorActionPreference = 'Stop'

$projectPath = Split-Path -Parent $PSScriptRoot
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    $uvCommand = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($uvCommand) {
    $uvPath = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.FullName }
    Push-Location $projectPath
    try {
        & $uvPath sync
        if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }
        exit 0
    } finally {
        Pop-Location
    }
}

Write-Host 'Install uv with: winget install --id astral-sh.uv -e'
throw 'uv was not found. Install uv, restart the terminal, then run this script again.'
