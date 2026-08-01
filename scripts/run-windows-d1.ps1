<#
.SYNOPSIS
  Windows 11 x64 runner for D1-GATE + D4B (Desktop Increment 1).
  Automates Steps 1, 2, 4, 6 of docs/desktop/WINDOWS_RUNBOOK.md and
  guides a human through the interactive Step 3 checklist.
  Step 5 (clean-machine smoke) is intentionally separate.

.DESCRIPTION
  Run on real Windows 11 x64 with an attached display, from any empty folder
  (the script clones the repo itself):
    powershell -ExecutionPolicy Bypass -File run-windows-d1.ps1
  Log is written to windows-d1-run.log next to the script.

  The gate requires real hardware; a CI runner cannot close it.
#>

[CmdletBinding()]
param(
    [string]$Branch      = "integration/desktop-increment-1",
    [string]$RepoUrl     = "https://github.com/dimastov-lab/ai-command-center",
    [string]$CloneDir    = "ai-command-center",
    [string]$PythonExe   = "",
    [switch]$SkipClone,
    [switch]$SkipInteractive,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$LogFile    = Join-Path $ScriptDir "windows-d1-run.log"
$ReportFile = Join-Path $ScriptDir "WINDOWS_D1_RESULT.md"

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Step([string]$name) { Write-Log ""; Write-Log "===== $name =====" }

function Fail([string]$msg, [int]$code = 1) {
    Write-Log "ERROR: $msg"
    Write-Log "Full log: $LogFile"
    exit $code
}

function Check-ExitCode([string]$what) {
    if ($LASTEXITCODE -ne 0) { Fail "$what exited with code $LASTEXITCODE" $LASTEXITCODE }
}

$Report = [ordered]@{}
$Report.WindowsVersion = [System.Environment]::OSVersion.VersionString
$Report.WindowsArch    = "x64 (AMD64)"
$Report.PythonVersion  = ""
$Report.PySide6Version = ""
$Report.PytestSummary  = ""
$Report.RuffResult     = ""
$Report.Checklist      = @{}
$Report.BuildResult    = ""
$Report.FailedSteps    = @()

# -------- Step 0: environment checks --------
Step "Step 0 - environment check"

if (-not [System.Environment]::Is64BitOperatingSystem) { Fail "An x64 OS is required (build script checks AMD64)." }
if (-not [System.Environment]::Is64BitProcess) {
    Write-Log "Process is 32-bit - relaunch in 64-bit PowerShell."
}

# Find a working x64 Python. Trial-run each candidate rather than trusting Get-Command,
# because 'py -3.12' as a single string fails when invoked with &.
$py = $null
if ($PythonExe -ne "") { $py = $PythonExe }
if (-not $py) {
    foreach ($cand in @("py -3.12", "py -3.13", "py -3", "py", "python", "python3")) {
        try {
            $out = & powershell -NoProfile -Command "$cand -c 'import sys; sys.stdout.write(str(sys.version_info[:2]))'" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -match '^\(\d') {
                $py = $cand
                Write-Log "Found Python via: $cand ($out)"
                break
            }
        } catch { }
    }
}
if (-not $py) {
    Fail ("Python not found. Install Python 3.12 or 3.13 x64 from https://www.python.org/downloads/windows/ " +
          "and check 'Add python.exe to PATH' during install. Or rerun with -PythonExe <full path to python.exe>.")
}
Write-Log "Using Python: $py"

# Resolve to the real python.exe path so we can call it directly (no nested quoting).
$realPy = (Invoke-Expression "$py -c 'import sys; print(sys.executable)'" 2>$null)
if ($realPy) { $realPy = $realPy.Trim() }
if (-not $realPy -or -not (Test-Path $realPy)) {
    Fail "Could not resolve python.exe path from '$py'. Run with -PythonExe <full path to python.exe>."
}
Write-Log "Resolved python.exe: $realPy"

$pyArch = & $realPy -c "import platform,sys; sys.stdout.write(platform.machine())" 2>$null
Write-Log "platform.machine() = $pyArch"
if ($pyArch -ne "AMD64") { Fail "Python is not x64 (AMD64): '$pyArch'. Install an x64 build." }
$pyVer = & $realPy -c "import sys; sys.stdout.write('%d.%d.%d'%sys.version_info[:3])" 2>$null
$Report.PythonVersion = $pyVer
Write-Log "Python $pyVer x64 - OK"

# -------- Step 1: clone + venv --------
Step "Step 1 - clone and virtualenv"

$Target = Join-Path $ScriptDir $CloneDir
if (-not $SkipClone) {
    if (Test-Path $Target) {
        Write-Log "$Target exists - updating."
        & git -C $Target fetch origin --quiet; Check-ExitCode "git fetch"
        & git -C $Target checkout $Branch --quiet 2>$null
        & git -C $Target reset --hard "origin/$Branch" --quiet; Check-ExitCode "git reset"
    } else {
        & git clone -b $Branch $RepoUrl $Target --quiet; Check-ExitCode "git clone"
    }
} else {
    Write-Log "SkipClone - using existing $Target"
}
if (-not (Test-Path $Target)) { Fail "Repo dir not found: $Target" }

$VenvPy = Join-Path $Target ".venv-desktop\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Log "Creating venv .venv-desktop"
    & $realPy -m venv (Join-Path $Target ".venv-desktop"); Check-ExitCode "venv"
}

Write-Log "Installing dependencies (may take a few minutes)..."
& $VenvPy -m pip install --upgrade pip --quiet; Check-ExitCode "pip upgrade"
& $VenvPy -m pip install -r (Join-Path $Target "requirements-desktop.txt") `
                        -r (Join-Path $Target "requirements-dev.txt") `
                        -r (Join-Path $Target "requirements-desktop-build.txt") `
                        --quiet 2>&1 | Add-Content -Path $LogFile -Encoding UTF8
Check-ExitCode "pip install deps"

$ps6 = & $VenvPy -c "import PySide6,sys; sys.stdout.write(PySide6.__version__)" 2>$null
if (-not $ps6) {
    Write-Log "WARNING: PySide6 did not import - desktop suite will be skipped (that is a failure)."
    $Report.FailedSteps += "PySide6 not installed"
} else {
    $Report.PySide6Version = $ps6
    Write-Log "PySide6 $ps6 - OK"
}

# -------- Step 2: automated tests --------
Step "Step 2 - automated tests (pytest tests/desktop + ruff)"

# Do NOT set QT_QPA_PLATFORM - conftest sets offscreen via setdefault.
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Write-Log "Running: pytest tests/desktop -q  (expected 124 passed)"
$pyOut = & $VenvPy -m pytest (Join-Path $Target "tests/desktop") -q 2>&1
$pyOut | Add-Content -Path $LogFile -Encoding UTF8
$lastLine = ($pyOut | Where-Object { $_ -match "passed|failed|skipped|error" } | Select-Object -Last 1)
$Report.PytestSummary = "$lastLine"
Write-Log "pytest: $lastLine"
if ($LASTEXITCODE -ne 0) { $Report.FailedSteps += "pytest tests/desktop" }
if ($lastLine -match "skipped" -and $lastLine -notmatch "passed") {
    Write-Log "WARNING: no 'passed' - PySide6 may be missing (skipped = failure)."
    $Report.FailedSteps += "desktop suite skipped (PySide6?)"
}

Write-Log "Running: ruff check ."
$ruffOut = & $VenvPy -m ruff check $Target 2>&1
$ruffOut | Add-Content -Path $LogFile -Encoding UTF8
if ($LASTEXITCODE -eq 0) { $Report.RuffResult = "PASS" } else { $Report.RuffResult = "FAIL"; $Report.FailedSteps += "ruff" }
Write-Log "ruff: $($Report.RuffResult)"

# -------- Step 3: interactive D1 checklist (human only) --------
if (-not $SkipInteractive) {
    Step "Step 3 - interactive D1 checklist (display required)"
    Write-Host ""
    Write-Host "The desktop app will now launch." -ForegroundColor Yellow
    Write-Host "Work through the 6 checks below. Do NOT close the app until the checklist is done." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Checks:" -ForegroundColor Cyan
    Write-Host "  1) AppShell + Sidebar (9 sections; Sessions/Execution/Git/Artifacts/Reports/Agents inactive) + TopBar render"
    Write-Host "  2) Click Home/Projects/Settings switches the visible page; click on an inactive item does nothing"
    Write-Host "  3) Light/Dark/System visibly changes the window palette"
    Write-Host "  4) In System mode, changing the Windows system theme is picked up on the fly"
    Write-Host "  5) Move/resize the window -> exit -> relaunch -> geometry restored exactly incl. width (QSettings registry)"
    Write-Host "  6) Clean exit with no error dialog and no crash"
    Write-Host ""
    Read-Host "Press Enter to launch the app"

    $appProc = Start-Process -FilePath $VenvPy -ArgumentList "-m","command_center.desktop" -WorkingDirectory $Target -PassThru -WindowStyle Normal
    Write-Log "App launched (PID $($appProc.Id))."

    $checks = @(
        @{N=1; Q="1) AppShell + Sidebar (9 sections, 6 inactive) + TopBar rendered?"},
        @{N=2; Q="2) Clicking sections switches the page; inactive item does nothing?"},
        @{N=3; Q="3) Light/Dark/System visibly changes the palette?"},
        @{N=4; Q="4) In System mode the Windows theme is picked up on the fly?"},
        @{N=5; Q="5) Geometry (incl. width) restored exactly after restart?"},
        @{N=6; Q="6) Clean exit with no error/crash?"}
    )
    foreach ($c in $checks) {
        Write-Host ""
        Write-Host $c.Q -ForegroundColor Cyan
        do {
            $ans = Read-Host "PASS / FAIL / comment (p/f/<text>)"
        } until ($ans -ne "")
        if ($ans -match "^p")      { $Report.Checklist[$c.N] = "PASS" }
        elseif ($ans -match "^f")  { $Report.Checklist[$c.N] = "FAIL"; $Report.FailedSteps += "Check $($c.N)" }
        else                       { $Report.Checklist[$c.N] = $ans }
    }
    if ($Report.Checklist[5] -eq "PASS") { Write-Log "Check 5 passed (geometry restored)." }

    Write-Host ""
    Write-Host "You may close the app now." -ForegroundColor Yellow
    if (-not $appProc.HasExited) {
        try { Stop-Process -Id $appProc.Id -ErrorAction SilentlyContinue } catch { }
    }
}

# -------- Step 4: D4B build --------
if (-not $SkipBuild) {
    Step "Step 4 - D4B build (build-desktop-windows.ps1)"
    $buildScript = Join-Path $Target "scripts\build-desktop-windows.ps1"
    if (-not (Test-Path $buildScript)) {
        Write-Log "Build script not found: $buildScript - skipping D4B"
        $Report.BuildResult = "skipped (no build script)"
        $Report.FailedSteps += "D4B: build-desktop-windows.ps1 missing"
    } else {
        Write-Log "Running: powershell -File $buildScript (may take 10-20 min)"
        & powershell -ExecutionPolicy Bypass -File $buildScript 2>&1 |
            Add-Content -Path $LogFile -Encoding UTF8
        if ($LASTEXITCODE -eq 0) {
            $exe = Join-Path $Target "dist\windows\AI Command Center\AI Command Center.exe"
            if (Test-Path $exe) {
                $sz = [math]::Round((Get-Item $exe).Length / 1MB, 1)
                $Report.BuildResult = "PASS ($exe, ${sz} MB)"
                Write-Log "Build OK: $exe ($sz MB)"
            } else {
                $Report.BuildResult = "FAIL (exe not found)"
                $Report.FailedSteps += "D4B: exe not found"
            }
        } else {
            $Report.BuildResult = "FAIL (code $LASTEXITCODE)"
            $Report.FailedSteps += "D4B: build"
        }
    }
}

# -------- Step 6: report --------
Step "Step 6 - writing WINDOWS_D1_RESULT.md"

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$r = $Report
$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine("# Windows 11 x64 - D1-GATE + D4B run result")
[void]$md.AppendLine("")
[void]$md.AppendLine("Generated: $now")
[void]$md.AppendLine("Script: run-windows-d1.ps1")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Environment")
[void]$md.AppendLine("- OS: $($r.WindowsVersion)")
[void]$md.AppendLine("- Arch: $($r.WindowsArch)")
[void]$md.AppendLine("- Python: $($r.PythonVersion)")
[void]$md.AppendLine("- PySide6: $($r.PySide6Version)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Step 2 - automated tests")
[void]$md.AppendLine("- \`pytest tests/desktop -q\`: $($r.PytestSummary)")
[void]$md.AppendLine("- \`ruff check .\`: $($r.RuffResult)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Step 3 - interactive D1 checklist")
if ($SkipInteractive) {
    [void]$md.AppendLine("- SKIPPED (SkipInteractive)")
} else {
    foreach ($k in ($r.Checklist.Keys | Sort-Object)) {
        [void]$md.AppendLine("- Item ${k}: $($r.Checklist[$k])")
    }
}
[void]$md.AppendLine("")
[void]$md.AppendLine("## Step 4 - D4B build")
[void]$md.AppendLine("- $($r.BuildResult)")
[void]$md.AppendLine("")
[void]$md.AppendLine("## Summary")
if ($r.FailedSteps.Count -eq 0) {
    [void]$md.AppendLine("- **D1-GATE Windows: PASS**")
    [void]$md.AppendLine("- **D4B: $($r.BuildResult)**")
} else {
    [void]$md.AppendLine("- Failed: " + ($r.FailedSteps -join "; "))
}
[void]$md.AppendLine("")
[void]$md.AppendLine("---")
[void]$md.AppendLine("Paste this section into docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md next to the macOS leg.")
[void]$md.AppendLine("D4B - mark packaging/windows/SMOKE_CHECKLIST.md.")
[void]$md.AppendLine("Step 5 (clean-machine smoke) is run SEPARATELY in Windows Sandbox.")

$md.ToString() | Set-Content -Path $ReportFile -Encoding UTF8
Write-Log "Report written: $ReportFile"

# -------- final --------
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "Done. Log:   $LogFile" -ForegroundColor Green
Write-Host "Report: $ReportFile" -ForegroundColor Green
if ($Report.FailedSteps.Count -eq 0) {
    Write-Host "All steps green." -ForegroundColor Green
} else {
    Write-Host "Failures: $($Report.FailedSteps -join '; ')" -ForegroundColor Red
}
Write-Host ""
Write-Host "Remaining manual steps:" -ForegroundColor Yellow
Write-Host "  1) Paste $ReportFile contents into docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md"
Write-Host "  2) Step 5 - clean-machine smoke (Windows Sandbox) per packaging/windows/SMOKE_CHECKLIST.md"
Write-Host "  3) If D1 PASS - set AICC-D1-GATE = Done in docs/roadmap/MASTER_ROADMAP_TASKS.json"
Write-Host "==================================================" -ForegroundColor Green