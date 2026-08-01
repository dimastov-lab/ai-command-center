#Requires -Version 5.1
<#
.SYNOPSIS
    Собирает неподписанную development-сборку AI Command Center для Windows 11 x64 (D4B).
#>

$ErrorActionPreference = "Stop"

# PyInstaller writes a DEPRECATION warning to stderr when run from an
# elevated terminal. Two problems follow in PowerShell 5.1:
#   1) with $ErrorActionPreference=Stop that becomes a terminating error
#      and aborts this build script at the first warning line;
#   2) the parent runner redirects our stderr with 2>&1, which turns the
#      same warning into a NativeCommandError and aborts the runner too.
# Fix: relax EAP to Continue for the native call (non-terminating) AND merge
# PyInstaller's stderr into stdout (2>&1) so nothing reaches the parent's
# stderr stream. $LASTEXITCODE is captured immediately after the native call
# (cmdlets never reset it, so it reflects python.exe, not a later cmdlet).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonBin = if ($env:DESKTOP_PYTHON) {
    $env:DESKTOP_PYTHON
} else {
    Join-Path $repoRoot ".venv-desktop\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $pythonBin)) {
    Write-Error @"
Не найден Python desktop-окружения: $pythonBin
Задайте DESKTOP_PYTHON или создайте .venv-desktop.
"@
    exit 2
}

$machine = & $pythonBin -c "import platform; print(platform.machine())"
if ($LASTEXITCODE -ne 0 -or $machine -ne "AMD64") {
    Write-Error "D4B требует Python x64 (получено: $machine). Целевая платформа — Windows 11 x64."
    exit 2
}

$pyiPrevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $pythonBin -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath (Join-Path $repoRoot "dist\windows") `
        --workpath (Join-Path $repoRoot "build\pyinstaller-windows") `
        (Join-Path $repoRoot "packaging\windows\ai-command-center.spec") 2>&1
    $pyiExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $pyiPrevEAP
}

if ($pyiExit -ne 0) {
    Write-Error "Сборка PyInstaller завершилась с кодом $pyiExit."
    exit $pyiExit
}

Write-Host "Собрано: $repoRoot\dist\windows\AI Command Center\AI Command Center.exe"
