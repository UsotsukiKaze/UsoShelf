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
    if ($env:JMSHELF_SIGN_CERT_THUMBPRINT) {
        $thumbprint = ($env:JMSHELF_SIGN_CERT_THUMBPRINT -replace '\s', '').ToUpperInvariant()
        if ($thumbprint -notmatch '^[A-F0-9]{40}$') { throw 'Invalid code-signing certificate thumbprint.' }
        $signTool = if ($env:JMSHELF_SIGNTOOL_PATH) { $env:JMSHELF_SIGNTOOL_PATH } else { (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source }
        if (-not $signTool -or -not (Test-Path -LiteralPath $signTool -PathType Leaf)) { throw 'signtool.exe was not found.' }
        $timestamp = if ($env:JMSHELF_TIMESTAMP_URL) { $env:JMSHELF_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }
        & (Join-Path $PSScriptRoot 'sign-windows.ps1') -Paths @($application, (Join-Path $applicationDirectory 'JmShelfUpdater.exe')) -VerifyOnly
        $signCommand = "`"$signTool`" sign /sha1 $thumbprint /fd SHA256 /tr $timestamp /td SHA256 `$f"
        & $compiler /Qp "-sjmshelf=$signCommand" $installerScript
    } else {
        & $compiler /Qp $installerScript
    }
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler failed with exit code $LASTEXITCODE." }
} finally {
    $env:JMSHELF_INSTALLER_SOURCE_DIR = $previousSourceDirectory
    Pop-Location
}

if (-not (Test-Path -LiteralPath $expectedInstaller)) {
    throw "Installer output is missing: $expectedInstaller"
}
if ($env:JMSHELF_SIGN_CERT_THUMBPRINT) {
    & (Join-Path $PSScriptRoot 'sign-windows.ps1') -Paths @($expectedInstaller) -VerifyOnly
}

$installer = Get-Item -LiteralPath $expectedInstaller
Write-Host "Windows installer: $($installer.FullName)"
Write-Host "Installer size: $([Math]::Round($installer.Length / 1MB, 2)) MB"
