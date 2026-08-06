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
    Write-Host @"
ERROR: Не найден Python desktop-окружения: $pythonBin
Задайте DESKTOP_PYTHON или создайте .venv-desktop.
"@ -ForegroundColor Red
    exit 2
}

$pyiPrevEAP = $ErrorActionPreference   # = "Stop"; relaxed around native calls
$ErrorActionPreference = "Continue"
try {
    $machine = (& $pythonBin -c "import platform; print(platform.machine())" 2>&1) -join ""
    $machineOK = ($LASTEXITCODE -eq 0 -and $machine.Trim() -eq "AMD64")
} finally {
    $ErrorActionPreference = $pyiPrevEAP
}
if (-not $machineOK) {
    Write-Host "ERROR: D4B требует Python x64 (получено: '$machine'). Целевая платформа — Windows 11 x64." -ForegroundColor Red
    exit 2
}

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
    Write-Host "ERROR: Сборка PyInstaller завершилась с кодом $pyiExit." -ForegroundColor Red
    exit $pyiExit
}

Write-Host "Собрано: $repoRoot\dist\windows\AI Command Center\AI Command Center.exe"
