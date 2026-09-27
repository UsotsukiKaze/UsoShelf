$ErrorActionPreference = 'Stop'

$projectPath = Split-Path -Parent $PSScriptRoot
$appUrl = 'http://127.0.0.1:17318'
$edgeCandidates = @(
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edgePath = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edgePath) { throw 'Microsoft Edge was not found. Run uv run jmshelf and open http://127.0.0.1:17318 manually.' }
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    $uvCommand = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uvCommand) { throw 'uv was not found. Install it with: winget install --id astral-sh.uv -e' }
$uvPath = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.FullName }

$serverReady = $false
try {
    $health = Invoke-RestMethod -Uri "$appUrl/api/health" -TimeoutSec 1
    $serverReady = $health.ok -and $health.backend -eq 'python'
    if ($health.ok -and -not $serverReady) {
        $headers = @{ 'X-JmShelf-Request' = '1' }
        Invoke-RestMethod -Uri "$appUrl/api/app/exit" -Method Post -Headers $headers -TimeoutSec 2 | Out-Null
        Start-Sleep -Milliseconds 500
    }
} catch { }

if (-not $serverReady) {
    $previousDesktopFlag = $env:JMSHELF_DESKTOP
    $env:JMSHELF_DESKTOP = '1'
    Start-Process -FilePath $uvPath -ArgumentList @('run', '--no-sync', 'jmshelf') -WorkingDirectory $projectPath -WindowStyle Hidden
    $env:JMSHELF_DESKTOP = $previousDesktopFlag
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $health = Invoke-RestMethod -Uri "$appUrl/api/health" -TimeoutSec 1
            if ($health.ok -and $health.backend -eq 'python') { $serverReady = $true; break }
        } catch { }
    }
}

if (-not $serverReady) { throw 'The JmShelf Python server failed to start. Run uv run jmshelf to inspect the error.' }
$launchUrl = "${appUrl}/?v=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
Start-Process -FilePath $edgePath -ArgumentList @("--app=$launchUrl", '--start-maximized')
