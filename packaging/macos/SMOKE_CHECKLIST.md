# D4A — проверка unsigned macOS `.app`

Сборка: `dist/macos/AI Command Center.app`

## Результат (2026-08-07) — PASS

Сборка: PyInstaller 6.21.0, Python 3.14.6, PySide6 6.11.1, macOS Apple Silicon (arm64).
Команда: `uv run --with "pyinstaller>=6.10,<7.0" pyinstaller packaging/macos/ai-command-center.spec --distpath dist/macos --workpath build/macos --noconfirm`

| Пункт | Результат |
|-------|-----------|
| Сборка завершена без ошибок | ✅ PASS — `dist/macos/AI Command Center.app` (98 МБ) |
| Запускается из Finder без virtualenv (`open` → Launch Services) | ✅ PASS |
| Нативное окно, Dock icon, системное меню | ✅ PASS |
| HTTP-порт не открывается | ✅ PASS — PID не в TCP LISTEN |
| Живёт 4+ сек без краша, чистый выход | ✅ PASS — exit 143 (SIGTERM) |
| Excluded: streamlit / fastapi / uvicorn / flask | ✅ PASS — нет в bundle |
| Разделы Главная / Проекты / Настройки работают | ✅ PASS |
| Переключение Light / Dark | ✅ PASS |
| Геометрия и тема сохраняются после перезапуска | ✅ PASS |
| Console: нет необработанных traceback | ✅ PASS |

Сборка unsigned и unnotarized. Для внутреннего запуска при срабатывании
Gatekeeper: Finder → правый клик → «Открыть». Подпись и обход Gatekeeper
средствами сборки запрещены.
