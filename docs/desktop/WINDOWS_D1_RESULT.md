# Windows 11 x64 - D1-GATE + D4B run result

Generated: 2026-08-07 03:55:29 +03:00
Script: run-windows-d1.ps1

## Environment
- OS: Microsoft Windows NT 10.0.26200.0
- Arch: x64 (AMD64)
- Python: 3.12.10
- PySide6: 6.11.1

## Step 2 - automated tests
- \pytest tests/desktop -q\: 1 failed, 125 passed in 25.09s
- \uff check .\: PASS

## Step 3 - interactive D1 checklist
- Item 1: PASS
- Item 2: PASS
- Item 3: PASS
- Item 4: PASS
- Item 5: PASS
- Item 6: PASS

## Step 4 - D4B build
- PASS (C:\Users\1\Desktop\ai-command-center\dist\windows\AI Command Center\AI Command Center.exe, 2.3 MB)

## Summary
- Failed: pytest tests/desktop

---
Paste this section into docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md next to the macOS leg.
D4B - mark packaging/windows/SMOKE_CHECKLIST.md.
Step 5 (clean-machine smoke) is run SEPARATELY in Windows Sandbox.
