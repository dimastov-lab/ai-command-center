<#
.SYNOPSIS
  Windows 11 x64 — исполнитель D1-GATE + D4B (Desktop Increment 1).
  Автоматизирует Шаги 1, 2, 4, 6 из docs/desktop/WINDOWS_RUNBOOK.md
  и проводит человека по интерактивному чеклисту Шага 3.
  Шаг 5 (smoke на чистой машине) вынесен отдельно — нужен отдельный окружение.

.DESCRIPTION
  Запускать на реальной Windows 11 x64 с подключённым дисплеем,
  находясь в пустом каталоге (скрипт сам клонирует репо):
    powershell -ExecutionPolicy Bypass -File run-windows-d1.ps1
  Лог пишется в windows-d1-run.log рядом со скриптом.

  Гейт требует реального железа — CI-раннер этого не закрывает.
#>

[CmdletBinding()]
param(
    [string]$Branch      = "integration/desktop-increment-1",
    [string]$RepoUrl     = "https://github.com/dimastov-lab/ai-command-center",
    [string]$CloneDir    = "ai-command-center",
    # py launcher launcher-псевдоним; если не задан, скрипт ищет python.
    [string]$PythonExe   = "",
    # Не задавай QT_QPA_PLATFORM — conftest сам поставит offscreen.
    [switch]$SkipClone,
    [switch]$SkipInteractive,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # ускорить git/pip вывод

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$LogFile   = Join-Path $ScriptDir "windows-d1-run.log"
$ReportFile = Join-Path $ScriptDir "WINDOWS_D1_RESULT.md"

# -------- утилиты --------
function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Step([string]$name) { Write-Log ""; Write-Log "===== $name =====" }

function Fail([string]$msg, [int]$code = 1) {
    Write-Log "ОШИБКА: $msg"
    Write-Log "Полный лог: $LogFile"
    exit $code
}

function Check-ExitCode([string]$what) {
    if ($LASTEXITCODE -ne 0) { Fail "$what завершилось с кодом $LASTEXITCODE" $LASTEXITCODE }
}

# сборка отчёта в памяти
$Report = [ordered]@{}
$Report.WindowsVersion = [System.Environment]::OSVersion.VersionString
$Report.WindowsArch    = "x64 (AMD64)"  # проверяется ниже
$Report.PythonVersion  = ""
$Report.PySide6Version = ""
$Report.PytestSummary   = ""
$Report.RuffResult      = ""
$Report.Checklist       = @{}   # пункт -> "PASS" / "FAIL" / комментарий
$Report.BuildResult     = ""
$Report.FailedSteps     = @()

# -------- Шаг 0: проверки среды --------
Step "Шаг 0 — проверка среды"

$arch = [System.Environment]::Is64BitOperatingSystem
if (-not $arch) { Fail "Нужна x64 ОС; скрипт сборки проверяет AMD64." }
if (-not [System.Environment]::Is64BitProcess) {
    Write-Log "Процесс 32-битный — перезапусти в 64-битном PowerShell." }

# найдём python (x64)
if ($PythonExe -ne "") {
    $py = $PythonExe
} else {
    $py = $null
    foreach ($cand in @("py -3.12", "py -3.13", "py", "python")) {
        $exe, $args = $cand -split ' ',2
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $py = $cand; break
        }
    }
    if (-not $py) { Fail "Python не найден. Установи Python 3.12/3.13 x64 и py launcher." }
}
Write-Log "Используем Python: $py"

# проверим, что python x64
$pyArch = & $py -c "import platform,sys; sys.stdout.write(platform.machine())" 2>$null
Write-Log "platform.machine() = $pyArch"
if ($pyArch -ne "AMD64") {
    Fail "Python не x64 (AMD64): '$pyArch'. Поставь x64-сборку Python."
}
$pyVer = & $py -c "import sys; sys.stdout.write('%d.%d.%d'%sys.version_info[:3])" 2>$null
$Report.PythonVersion = $pyVer
Write-Log "Python $pyVer x64 — OK"

# -------- Шаг 1: клон + окружение --------
Step "Шаг 1 — клон и виртуальное окружение"

$Target = Join-Path $ScriptDir $CloneDir
if (-not $SkipClone) {
    if (Test-Path $Target) {
        Write-Log "Каталог $Target существует — обновляю."
        & git -C $Target fetch origin --quiet; Check-ExitCode "git fetch"
        & git -C $Target checkout $Branch --quiet 2>$null
        & git -C $Branch reset --hard origin/$Branch --quiet 2>$null
        & git -C $Target reset --hard "origin/$Branch" --quiet; Check-ExitCode "git reset"
    } else {
        & git clone -b $Branch $RepoUrl $Target --quiet; Check-ExitCode "git clone"
    }
} else {
    Write-Log "SkipClone — работаю в существующем $Target"
}
if (-not (Test-Path $Target)) { Fail "Каталог репо не найден: $Target" }

$VenvPy = Join-Path $Target ".venv-desktop\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Log "Создаю venv .venv-desktop"
    & $py -m venv (Join-Path $Target ".venv-desktop"); Check-Exitcode "venv"
}

Write-Log "Установка зависимостей (может занять несколько минут)…"
& $VenvPy -m pip install --upgrade pip --quiet; Check-ExitCode "pip upgrade"
& $VenvPy -m pip install -r (Join-Path $Target "requirements-desktop.txt") `
                        -r (Join-Path $Target "requirements-dev.txt") `
                        -r (Join-Path $Target "requirements-desktop-build.txt") `
                        --quiet 2>&1 | Add-Content -Path $LogFile -Encoding UTF8
Check-ExitCode "pip install deps"

# версия PySide6
$ps6 = & $VenvPy -c "import PySide6,sys; sys.stdout.write(PySide6.__version__)" 2>$null
if (-not $ps6) {
    Write-Log "ВНИМАНИЕ: PySide6 не импортируется — desktop suite уйдёт в skipped (это провал)."
    $Report.FailedSteps += "PySide6 не установлен"
} else {
    $Report.PySide6Version = $ps6
    Write-Log "PySide6 $ps6 — OK"
}

# -------- Шаг 2: автоматические тесты --------
Step "Шаг 2 — автоматические тесты (pytest tests/desktop + ruff)"

# ВАЖНО: не задаваем QT_QPA_PLATFORM — conftest сам поставит offscreen.
# Если задать пустую строку, setdefault не перезапишет, и Qt упадёт.
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Write-Log "Запуск: pytest tests/desktop -q  (ожидается 124 passed)"
$pyOut = & $VenvPy -m pytest (Join-Path $Target "tests/desktop") -q 2>&1
$pyOut | Add-Content -Path $LogFile -Encoding UTF8
$lastLine = ($pyOut | Where-Object { $_ -match "passed|failed|skipped|error" } | Select-Object -Last 1)
$Report.PytestSummary = "$lastLine"
Write-Log "pytest: $lastLine"
if ($LASTEXITCODE -ne 0) { $Report.FailedSteps += "pytest tests/desktop" }
# проверим, что набор реально выполнялся, а не ушел в skip
if ($lastLine -match "skipped" -and $lastLine -notmatch "passed") {
    Write-Log "ВНИМАНИЕ: нет passed — возможно PySide6 не встал (skipped = провал)."
    $Report.FailedSteps += "desktop suite skipped (PySide6?)"
}

Write-Log "Запуск: ruff check ."
$ruffOut = & $VenvPy -m ruff check $Target 2>&1
$ruffOut | Add-Content -Path $LogFile -Encoding UTF8
if ($LASTEXITCODE -eq 0) { $Report.RuffResult = "PASS" } else { $Report.RuffResult = "FAIL"; $Report.FailedSteps += "ruff" }
Write-Log "ruff: $($Report.RuffResult)"

# -------- Шаг 3: интерактивный чеклист (только человек) --------
if (-not $SkipInteractive) {
    Step "Шаг 3 — интерактивный D1-чеклист (нужен экран)"
    Write-Host ""
    Write-Host "Сейчас запустится desktop-приложение." -ForegroundColor Yellow
    Write-Host "Проверь по очереди 6 пунктов. Приложение НЕ закрывай до конца чеклиста." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Пункты:" -ForegroundColor Cyan
    Write-Host "  1) AppShell + Sidebar (9 разделов; Sessions/Execution/Git/Artifacts/Reports/Agents неактивны) + TopBar"
    Write-Host "  2) Клик 'Главная'/'Проекты'/'Настройки' переключает страницу; клик по неактивному — ничего"
    Write-Host "  3) Light/Dark/System видимо меняют палитру"
    Write-Host "  4) В режиме 'Система' смена системной темы Windows подхватывается на лету"
    Write-Host "  5) ПОДВИНУТЬ/РАСТЯНУТЬ окно -> Выйти -> Запустить снова -> точная геометрия включая ширину (QSettings registry)"
    Write-Host "  6) Штатный выход без диалога ошибки и без падения"
    Write-Host ""
    Read-Host "Нажми Enter, чтобы запустить приложение"

    $appProc = Start-Process -FilePath $VenvPy -ArgumentList "-m","command_center.desktop" -WorkingDirectory $Target -PassThru -WindowStyle Normal
    Write-Log "Приложение запущено (PID $($appProc.Id))."

    $checks = @(
        @{N=1; Q="1) AppShell + Sidebar (9 разделов, 6 неактивны) + TopBar отрисовались?"},
        @{N=2; Q="2) Клик по разделам переключает страницу; неактивный — ничего?"},
        @{N=3; Q="3) Light/Dark/System видимо меняют палитру?"},
        @{N=4; Q="4) В режиме 'Система' смена системной темы подхватывается на лету?"},
        @{N=5; Q="5) Геометрия (включая ширину) точно восстановилась после рестарта?"},
        @{N=6; Q="6) Штатный выход без ошибки/падения?"}
    )
    foreach ($c in $checks) {
        Write-Host ""
        Write-Host $c.Q -ForegroundColor Cyan
        do {
            $ans = Read-Host "PASS / FAIL / комментарий (p/f/<текст>)"
        } until ($ans -ne "")
        if ($ans -match "^p")      { $Report.Checklist[$c.N] = "PASS" }
        elseif ($ans -match "^f")  { $Report.Checklist[$c.N] = "FAIL"; $Report.FailedSteps += "Чек $($c.N)" }
        else                       { $Report.Checklist[$c.N] = $ans }
    }
    # пункт 5 требует рестарта — подскажем
    if ($Report.Checklist[5] -eq "PASS") {
        Write-Log "Чек 5 пройдён (геометрия восстановилась)."
    }

    Write-Host ""
    Write-Host "Можешь закрыть приложение." -ForegroundColor Yellow
    if (-not $appProc.HasExited) {
        try { Stop-Process -Id $appProc.Id -ErrorAction SilentlyContinue } catch {}
    }
}

# -------- Шаг 4: сборка D4B --------
if (-not $SkipBuild) {
    Step "Шаг 4 — сборка D4B (build-desktop-windows.ps1)"
    $buildScript = Join-Path $Target "scripts\build-desktop-windows.ps1"
    if (-not (Test-Path $buildScript)) {
        Write-Log "Скрипт сборки не найден: $buildScript — пропускаю D4B"
        $Report.BuildResult = "skipped (нет скрипта)"
        $Report.FailedSteps += "D4B: нет build-desktop-windows.ps1"
    } else {
        Write-Log "Запуск: powershell -File $buildScript (может занять 10-20 мин)"
        & powershell -ExecutionPolicy Bypass -File $buildScript 2>&1 |
            Add-Content -Path $LogFile -Encoding UTF8
        if ($LASTEXITCODE -eq 0) {
            $exe = Join-Path $Target "dist\windows\AI Command Center\AI Command Center.exe"
            if (Test-Path $exe) {
                $sz = [math]::Round((Get-Item $exe).Length / 1MB, 1)
                $Report.BuildResult = "PASS ($exe, ${sz} МБ)"
                Write-Log "Сборка OK: $exe ($sz МБ)"
            } else {
                $Report.BuildResult = "FAIL (exe не найден)"
                $Report.FailedSteps += "D4B: exe не найден"
            }
        } else {
            $Report.BuildResult = "FAIL (код $LASTEXITCODE)"
            $Report.FailedSteps += "D4B: сборка"
        }
    }
}

# -------- Шаг 6: запись результата --------
Step "Шаг 6 — формирование отчёта WINDOWS_D1_RESULT.md"

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$r = $Report
$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine("# Windows 11 x64 — результат прогона D1-GATE + D4B")
[void]$md.AppendLine("")
[void]$md.AppendLine("Сгенерировано: $now")
[void]$md.AppendLine("Скрипт: run-windows-d1.ps1")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Среда")
[void]$md.AppendLine("- ОС: $($r.WindowsVersion)")
[void]$md.AppendLine("- Архитектура: $($r.WindowsArch)")
[void]$md.AppendLine("- Python: $($r.PythonVersion)")
[void]$md.AppendLine("- PySide6: $($r.PySide6Version)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Шаг 2 — автоматические тесты")
[void]$md.AppendLine("- \`pytest tests/desktop -q\`: $($r.PytestSummary)")
[void]$md.AppendLine("- \`ruff check .\`: $($r.RuffResult)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Шаг 3 — интерактивный D1-чеклист")
if ($SkipInteractive) {
    [void]$md.AppendLine("- ПРОПУЩЕН (SkipInteractive)")
} else {
    foreach ($k in ($r.Checklist.Keys | Sort-Object)) {
        [void]$md.AppendLine("- Пункт ${k}: $($r.Checklist[$k])")
    }
}
[void]$md.AppendLine("")
[void]$md.AppendLine("## Шаг 4 — сборка D4B")
[void]$md.AppendLine("- $($r.BuildResult)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Итог")
if ($r.FailedSteps.Count -eq 0) {
    [void]$md.AppendLine("- **D1-GATE Windows: PASS**")
    [void]$md.AppendLine("- **D4B: $($r.BuildResult)**")
} else {
    [void]$md.AppendLine("- Не пройдено: " + ($r.FailedSteps -join "; "))
}
[void]$md.AppendLine("")
[void]$md.AppendLine("---")
[void]$md.AppendLine("Вставь эту секцию в docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md рядом с macOS-ногой.")
[void]$md.AppendLine("D4B — отметки в packaging/windows/SMOKE_CHECKLIST.md.")
[void]$md.AppendLine("Шаг 5 (smoke на чистой машине) исполняется ОТДЕЛЬНО — нужен Windows Sandbox.")

$md.ToString() | Set-Content -Path $ReportFile -Encoding UTF8
Write-Log "Отчёт записан: $ReportFile"

# -------- финал --------
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "Готово. Лог:   $LogFile" -ForegroundColor Green
Write-Host "Отчёт: $ReportFile" -ForegroundColor Green
if ($Report.FailedSteps.Count -eq 0) {
    Write-Host "Все шаги зелёные." -ForegroundColor Green
} else {
    Write-Host "Провалы: $($Report.FailedSteps -join '; ')" -ForegroundColor Red
}
Write-Host ""
Write-Host "Осталось вручную:" -ForegroundColor Yellow
Write-Host "  1) Скопируй содержимое $ReportFile в docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md"
Write-Host "  2) Шаг 5 — smoke на чистой машине (Windows Sandbox) по packaging/windows/SMOKE_CHECKLIST.md"
Write-Host "  3) Если D1 PASS — поставь AICC-D1-GATE = Done в docs/roadmap/MASTER_ROADMAP_TASKS.json"
Write-Host "==================================================" -ForegroundColor Green