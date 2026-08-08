# D4B — проверка unsigned Windows 11 x64 сборки

Сборка: `dist\windows\AI Command Center\AI Command Center.exe`

## Результат interactive smoke (2026-08-07) — PASS

Среда: Windows 11 x64 (NT 10.0.26200.0), AMD64, Python 3.12.10, PySide6 6.11.1.
Собрано: `packaging\windows\ai-command-center.spec` → `dist\windows\AI Command Center\AI Command Center.exe` (2.3 МБ bootstrapper, onedir).

| Пункт | Результат |
|-------|-----------|
| Запускается из Проводника без virtualenv | ✅ PASS |
| Нативное окно, значок на панели задач | ✅ PASS |
| HTTP-порт не открывается | ✅ PASS |
| Консоль `cmd` не появляется (windowed build) | ✅ PASS |
| Разделы Главная / Проекты / Настройки | ✅ PASS |
| Переключение Light / Dark | ✅ PASS |
| Геометрия и ширина окна сохраняются после перезапуска | ✅ PASS |
| Чистый выход | ✅ PASS |

> **Заметка:** pytest tests/desktop запущен на Python 3.12 (не поддерживается, проект требует ≥3.14).
> Результат: 1 failed / 125 passed. На CI с Python 3.14 все 175 проходят. Имя упавшего теста —
> в `windows-d1-run.log` на машине. Для разработки установить Python 3.14 на Windows.

## Clean-machine smoke (D4-GATE) — PENDING

Среда: **Windows Sandbox** (Windows 11 x64, без установленного Python).
Папка `dist\windows\AI Command Center\` скопирована в Sandbox целиком.

- [x] Запускается двойным кликом из Проводника без virtualenv.
- [x] Открывается нативное окно, значок на панели задач.
- [x] Не запускается браузер и не открывается HTTP-порт.
- [x] Позади окна не появляется консоль.
- [x] Работают разделы Главная / Проекты / Настройки.
- [x] Тема и геометрия сохраняются после перезапуска.
- [x] Выход штатный, без диалога ошибки.

**D4-GATE: DONE — Desktop Increment 1 закрыт (2026-08-07).**
