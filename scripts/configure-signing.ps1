param(
    [string]$PfxPath = '',
    [securestring]$PfxPassword,
    [string]$Thumbprint = '',
    [string]$SignToolPath = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'

if ($PfxPath -and $Thumbprint) {
    throw 'Use either -PfxPath or -Thumbprint, not both.'
}

if ($PfxPath) {
    $resolvedPfx = (Resolve-Path -LiteralPath $PfxPath -ErrorAction Stop).Path
    if (-not $PfxPassword) {
        $PfxPassword = Read-Host 'PFX password' -AsSecureString
    }
    $certificate = Import-PfxCertificate -FilePath $resolvedPfx -CertStoreLocation Cert:\CurrentUser\My -Password $PfxPassword -ErrorAction Stop
    $Thumbprint = $certificate.Thumbprint
}

$cleanThumbprint = ($Thumbprint -replace '\s', '').ToUpperInvariant()
if ($cleanThumbprint -and $cleanThumbprint -notmatch '^[A-F0-9]{40}$') {
    throw 'The certificate thumbprint must contain 40 hexadecimal characters.'
}

$candidates = @(
    Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object {
            $_.HasPrivateKey -and
            $_.NotBefore -le (Get-Date) -and $_.NotAfter -gt (Get-Date) -and
            ($_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3') -and
            (-not $cleanThumbprint -or $_.Thumbprint -eq $cleanThumbprint)
        }
)
if (-not $candidates) {
    throw 'No valid code-signing certificate with a usable private key was found. Import a CA-issued PFX or connect its hardware token first.'
}
if (-not $cleanThumbprint -and $candidates.Count -ne 1) {
    $candidates | Select-Object Thumbprint, Subject, NotAfter | Format-Table -AutoSize
    throw 'More than one code-signing certificate is available; rerun with -Thumbprint.'
}
$certificate = $candidates[0]

if ($SignToolPath) {
    $resolvedSignTool = (Resolve-Path -LiteralPath $SignToolPath -ErrorAction Stop).Path
} else {
    $found = Get-Command signtool.exe -ErrorAction SilentlyContinue
    $resolvedSignTool = if ($found) { $found.Source } else { '' }
    if (-not $resolvedSignTool) {
        $sdkRoot = 'C:\Program Files (x86)\Windows Kits\10\bin'
        if (Test-Path -LiteralPath $sdkRoot) {
            $resolvedSignTool = Get-ChildItem -LiteralPath $sdkRoot -Filter signtool.exe -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.DirectoryName -match '[\\/]x64$' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1 -ExpandProperty FullName
        }
    }
}
if (-not $resolvedSignTool -or -not (Test-Path -LiteralPath $resolvedSignTool -PathType Leaf)) {
    throw 'Windows SDK signtool.exe was not found. Install the Windows SDK Signing Tools or pass -SignToolPath.'
}
if ($TimestampUrl -notmatch '^https?://') {
    throw 'TimestampUrl must be an HTTP or HTTPS URL.'
}

$settings = @{
    JMSHELF_SIGN_CERT_THUMBPRINT = $certificate.Thumbprint
    JMSHELF_SIGNTOOL_PATH = $resolvedSignTool
    JMSHELF_TIMESTAMP_URL = $TimestampUrl
}
foreach ($entry in $settings.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'User')
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
}

Write-Host "Code-signing certificate: $($certificate.Subject)"
Write-Host "Thumbprint: $($certificate.Thumbprint)"
Write-Host "Expires: $($certificate.NotAfter)"
Write-Host "SignTool: $resolvedSignTool"
Write-Host 'Signing is configured for this shell and future sessions. Build with .\scripts\build-windows.ps1.'
