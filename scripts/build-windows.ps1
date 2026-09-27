param(
    [switch]$SkipTests,
    [string]$OutputRoot = 'dist',
    [switch]$SkipArchive,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$outputPath = if ([IO.Path]::IsPathRooted($OutputRoot)) {
    [IO.Path]::GetFullPath($OutputRoot)
} else {
    [IO.Path]::GetFullPath((Join-Path $projectPath $OutputRoot))
}
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    $uvCommand = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uvCommand) { throw 'uv was not found. Install it with: winget install --id astral-sh.uv -e' }
$uvPath = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.FullName }

Push-Location $projectPath
try {
    if (-not $env:UV_CACHE_DIR) {
        $env:UV_CACHE_DIR = Join-Path $env:LOCALAPPDATA 'JmShelf\build-cache\uv'
    }
    $env:UV_LINK_MODE = 'copy'
    & $uvPath sync --group dev
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }

    if (-not $SkipTests) {
        & (Join-Path $PSScriptRoot 'test.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    }

    & $uvPath run python scripts/make-windows-icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }

    $updaterDist = Join-Path $projectPath 'build\updater-dist'
    & $uvPath run pyinstaller --noconfirm --clean --distpath $updaterDist --workpath build\updater packaging/JmShelfUpdater.spec
    if ($LASTEXITCODE -ne 0) { throw 'Updater build failed.' }
    $updaterExecutable = Join-Path $updaterDist 'JmShelfUpdater.exe'
    if (-not (Test-Path -LiteralPath $updaterExecutable)) { throw "Updater output is missing: $updaterExecutable" }
    if (-not $SkipTests) {
        $updaterTest = Start-Process -FilePath $updaterExecutable -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
        if (-not $updaterTest.WaitForExit(30 * 1000)) {
            $updaterTest.Kill()
            $updaterTest.WaitForExit()
            throw 'Packaged updater self-test timed out after 30 seconds.'
        }
        if ($updaterTest.ExitCode -ne 0) { throw "Packaged updater self-test failed with exit code $($updaterTest.ExitCode)." }
        & $uvPath run python scripts/test-packaged-updater.py --updater $updaterExecutable
        if ($LASTEXITCODE -ne 0) { throw 'Packaged updater end-to-end test failed.' }
    }

    & $uvPath run pyinstaller --noconfirm --clean --distpath $outputPath --workpath build packaging/JmShelf.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $executable = Join-Path $outputPath 'JmShelf\JmShelf.exe'
    if (-not (Test-Path -LiteralPath $executable)) { throw "Build output is missing: $executable" }
    Copy-Item -LiteralPath $updaterExecutable -Destination (Join-Path $outputPath 'JmShelf\JmShelfUpdater.exe') -Force
    if (-not $SkipTests) {
        $process = Start-Process -FilePath $executable -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(30 * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Packaged self-test timed out after 30 seconds.'
        }
        if ($process.ExitCode -ne 0) { throw "Packaged self-test failed with exit code $($process.ExitCode)." }
    }

    if (-not $SkipArchive) {
        $archive = Join-Path $outputPath 'JmShelf-windows-x64.zip'
        $archiveCreated = $false
        for ($attempt = 1; $attempt -le 8; $attempt++) {
            try {
                Compress-Archive -LiteralPath (Join-Path $outputPath 'JmShelf') -DestinationPath $archive -CompressionLevel Optimal -Force -ErrorAction Stop
                $archiveCreated = $true
                break
            } catch {
                if ($attempt -eq 8) { throw }
                Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds (350 * $attempt)
            }
        }
        if (-not $archiveCreated) { throw 'Portable archive creation failed.' }
        $versionMatch = Select-String -LiteralPath (Join-Path $projectPath 'pyproject.toml') -Pattern '^version = "([^"]+)"'
        if (-not $versionMatch) { throw 'Project version was not found in pyproject.toml.' }
        $projectVersion = $versionMatch.Matches[0].Groups[1].Value
        $versionedArchive = Join-Path $outputPath "JmShelf-windows-x64-$projectVersion.zip"
        Copy-Item -LiteralPath $archive -Destination $versionedArchive -Force
        Write-Host "Portable archive: $archive"
        Write-Host "Versioned archive: $versionedArchive"
    }
    if (-not $SkipInstaller -and ([IO.Path]::GetFullPath($outputPath) -eq [IO.Path]::GetFullPath((Join-Path $projectPath 'dist')))) {
        & (Join-Path $PSScriptRoot 'build-installer.ps1') -SkipAppBuild
        if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
    }
    Write-Host "Windows application: $executable"
} finally {
    Pop-Location
}
