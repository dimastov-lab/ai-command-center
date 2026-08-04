#Requires -Version 5.1
<#
.SYNOPSIS
    Собирает неподписанную development-сборку AI Command Center для Windows 11 x64 (D4B).
#>

$ErrorActionPreference = "Stop"

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
if ($machine -ne "AMD64") {
    Write-Error "D4B требует Python x64 (получено: $machine). Целевая платформа — Windows 11 x64."
    exit 2
}

& $pythonBin -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath (Join-Path $repoRoot "dist\windows") `
    --workpath (Join-Path $repoRoot "build\pyinstaller-windows") `
    (Join-Path $repoRoot "packaging\windows\ai-command-center.spec")

if ($LASTEXITCODE -ne 0) {
    Write-Error "Сборка PyInstaller завершилась с кодом $LASTEXITCODE."
    exit $LASTEXITCODE
}

Write-Host "Собрано: $repoRoot\dist\windows\AI Command Center\AI Command Center.exe"
