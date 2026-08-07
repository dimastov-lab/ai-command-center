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

Прогоняется на **чистой машине Windows 11 x64** — без установленного Python,
без `.venv`, без копии репозитория. Папку `dist\windows\AI Command Center\` копируем на неё целиком.

- [ ] Запускается двойным кликом из Проводника без virtualenv.
- [ ] Открывается нативное окно, значок на панели задач.
- [ ] Не запускается браузер и не открывается HTTP-порт.
- [ ] Позади окна не появляется консоль.
- [ ] Работают разделы Главная / Проекты / Настройки.
- [ ] Тема и геометрия сохраняются после перезапуска.
- [ ] Выход штатный, без диалога ошибки.

Вариант: **Windows Sandbox** (встроен в Windows 11 Pro/Enterprise): включить через
«Компоненты Windows», скопировать папку сборки в Sandbox, запустить .exe.

Сборка unsigned. SmartScreen: «More info» → «Run anyway».
