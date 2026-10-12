param(
    [Parameter(Mandatory = $true)][string[]]$Paths,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$thumbprint = ($env:JMSHELF_SIGN_CERT_THUMBPRINT -replace '\s', '').ToUpperInvariant()
if ($thumbprint -notmatch '^[A-F0-9]{40}$') {
    throw 'Set JMSHELF_SIGN_CERT_THUMBPRINT to the SHA-1 thumbprint of a trusted code-signing certificate.'
}
$certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $thumbprint -and $_.HasPrivateKey -and ($_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3') } |
    Select-Object -First 1
if (-not $certificate) { throw "No usable code-signing certificate with private key was found: $thumbprint" }

$signTool = if ($env:JMSHELF_SIGNTOOL_PATH) { $env:JMSHELF_SIGNTOOL_PATH } else { (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source }
if (-not $signTool -or -not (Test-Path -LiteralPath $signTool -PathType Leaf)) {
    throw 'signtool.exe was not found; set JMSHELF_SIGNTOOL_PATH to its absolute path.'
}
$timestamp = if ($env:JMSHELF_TIMESTAMP_URL) { $env:JMSHELF_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }

foreach ($path in $Paths) {
    $resolved = (Resolve-Path -LiteralPath $path -ErrorAction Stop).Path
    if (-not $VerifyOnly) {
        & $signTool sign /sha1 $thumbprint /fd SHA256 /tr $timestamp /td SHA256 $resolved
        if ($LASTEXITCODE -ne 0) { throw "Code signing failed: $resolved" }
    }
    & $signTool verify /pa /tw $resolved
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $resolved" }
    $signature = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $thumbprint) {
        throw "The expected trusted signature is missing: $resolved"
    }
    Write-Host "Verified code signature: $resolved"
}
