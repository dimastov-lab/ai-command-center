# Подпись Windows-сборки (#197, SIGNING_RUNBOOK.md §2).
# Запускается ПОСЛЕ scripts/build-desktop-windows.ps1 на машине владельца
# с установленным сертификатом (OV/EV токен или Azure Trusted Signing dlib).
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Artifact = "$PSScriptRoot\..\dist\windows\AI Command Center\AI Command Center.exe",
    # Отпечаток сертификата; по умолчанию — единственный code-signing серт из хранилища.
    [string]$Thumbprint = $env:AICC_SIGN_THUMBPRINT,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Artifact)) {
    Write-Error "Нет сборки: $Artifact — сначала scripts/build-desktop-windows.ps1"
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    Write-Error "signtool.exe не найден (Windows SDK). SIGNING_RUNBOOK.md §1 (Windows)."
}

if (-not $Thumbprint) {
    $certs = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert)
    if ($certs.Count -eq 0) {
        Write-Error "Нет code-signing сертификата. Owner-шаги: SIGNING_RUNBOOK.md §1 (Windows)."
    }
    if ($certs.Count -gt 1) {
        Write-Error "Несколько сертификатов; задайте AICC_SIGN_THUMBPRINT."
    }
    $Thumbprint = $certs[0].Thumbprint
}

& $signtool.Source sign /fd SHA256 /td SHA256 /tr $TimestampUrl /sha1 $Thumbprint $Artifact
if ($LASTEXITCODE -ne 0) { Write-Error "signtool sign failed ($LASTEXITCODE)" }

& $signtool.Source verify /pa $Artifact
if ($LASTEXITCODE -ne 0) { Write-Error "signtool verify failed ($LASTEXITCODE)" }

Write-Host "Подписано: $Artifact (далее — SmartScreen-приёмка на чистой машине, SMOKE_CHECKLIST)"
