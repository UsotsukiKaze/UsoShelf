param(
    [switch]$KeepArtifacts
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    $uvCommand = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uvCommand) { throw 'uv was not found. Install it with: winget install --id astral-sh.uv -e' }
$uvPath = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.FullName }
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("JmShelf-tests-" + [guid]::NewGuid().ToString('N'))

Push-Location $projectPath
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:UV_LINK_MODE = 'copy'
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    & $uvPath run pytest -q --basetemp $testRoot
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
} finally {
    Pop-Location
    if (-not $KeepArtifacts) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "Test artifacts: $testRoot"
    }
}
