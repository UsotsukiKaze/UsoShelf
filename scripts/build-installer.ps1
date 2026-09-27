param(
    [switch]$SkipAppBuild,
    [string]$IsccPath = $env:JMSHELF_ISCC_PATH
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$installerScript = Join-Path $projectPath 'packaging\JmShelf.iss'
$versionMatch = Select-String -LiteralPath $installerScript -Pattern '^#define MyAppVersion "([^"]+)"'
if (-not $versionMatch) { throw 'Installer version was not found in packaging/JmShelf.iss.' }
$installerVersion = $versionMatch.Matches[0].Groups[1].Value
$expectedInstaller = Join-Path $projectPath "dist\JmShelf-Setup-$installerVersion-x64.exe"
$stagingRoot = Join-Path $projectPath '.installer-stage\dist'

if (-not $SkipAppBuild) {
    & (Join-Path $PSScriptRoot 'build-windows.ps1') -OutputRoot $stagingRoot -SkipArchive -SkipInstaller
    if ($LASTEXITCODE -ne 0) { throw 'JmShelf application build failed.' }
    $application = Join-Path $stagingRoot 'JmShelf\JmShelf.exe'
} else {
    $application = Join-Path $projectPath 'dist\JmShelf\JmShelf.exe'
}

if (-not (Test-Path -LiteralPath $application)) {
    throw "Packaged application is missing: $application"
}
$applicationDirectory = Split-Path -Parent $application

$compilerCandidates = @(
    $IsccPath,
    (Join-Path $projectPath '.tools\InnoSetup\ISCC.exe'),
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

$compiler = $compilerCandidates | Select-Object -First 1
if (-not $compiler) {
    throw 'ISCC.exe was not found. Install Inno Setup or set JMSHELF_ISCC_PATH.'
}

Push-Location $projectPath
try {
    $previousSourceDirectory = $env:JMSHELF_INSTALLER_SOURCE_DIR
    $env:JMSHELF_INSTALLER_SOURCE_DIR = $applicationDirectory
    & $compiler /Qp $installerScript
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler failed with exit code $LASTEXITCODE." }
} finally {
    $env:JMSHELF_INSTALLER_SOURCE_DIR = $previousSourceDirectory
    Pop-Location
}

if (-not (Test-Path -LiteralPath $expectedInstaller)) {
    throw "Installer output is missing: $expectedInstaller"
}

$installer = Get-Item -LiteralPath $expectedInstaller
Write-Host "Windows installer: $($installer.FullName)"
Write-Host "Installer size: $([Math]::Round($installer.Length / 1MB, 2)) MB"
