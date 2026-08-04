# Windows 11 x64 — раннбук прогона Desktop Increment 1

Этот документ самодостаточен: его хватит, чтобы выполнить всю Windows-часть,
не зная истории обсуждения. Ветка для работы — `integration/desktop-increment-1`.

## Зачем это нужно

Windows-железа у проекта не было с 2026-07-28, и на этом застряли четыре вещи:

1. **`AICC-D1-GATE`** — открытый PR #83. macOS-нога PASS, Windows-нога `NOT PERFORMED`.
   Чеклист — в `docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md` §"Outstanding work".
   Раннер GitHub Actions гейт не закрывает: определение требует реального железа.
2. **D3-гейт** — `DESKTOP_INCREMENT_1.md` §4 требует прохода **на обеих** платформах.
   На Windows `QSettings` пишет в реестр, а не в plist; идентичность бэкендов
   предполагать нельзя (`IMPLEMENTATION_ROADMAP.md`, D3B "Primary risks").
3. **D4B** — Windows packaging. Спека и скрипт сборки уже написаны, не прогонялись.
4. **`AICC-D4-GATE`** — `DESKTOP_INCREMENT_1.md` §6, обе платформы.

## Предусловия

- **Python 3.12 или 3.13, обязательно x64.** Не ARM-сборка: целевая платформа
  заявлена как Windows 11 x64 (`PLATFORM_BEHAVIOR.md` §2), и скрипт сборки
  проверяет `platform.machine() == "AMD64"`, иначе выходит с кодом 2.
- Git, PowerShell 5.1+.

## Шаг 1 — окружение

```powershell
git clone -b integration/desktop-increment-1 https://github.com/dimastov-lab/ai-command-center
cd ai-command-center
py -3.12 -m venv .venv-desktop
.venv-desktop\Scripts\python -m pip install -r requirements-desktop.txt -r requirements-dev.txt -r requirements-desktop-build.txt
```

## Шаг 2 — автоматические тесты

```powershell
.venv-desktop\Scripts\python -m pytest tests/desktop -q
```

Ожидается **124 passed**. Две ловушки, обе дают ложный успех:

- **Не задавай `QT_QPA_PLATFORM` вручную.** `tests/desktop/conftest.py` делает
  `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`. Уже заданную переменную
  `setdefault` не перезапишет — в частности пустую строку, с которой Qt падает.
- **Проверяй слово `passed`, а не отсутствие красного.** Там же стоит
  `pytest.importorskip("PySide6")`: если PySide6 не встал, весь набор молча уходит
  в `skipped`. `0 failed` при `skipped` — это провал, а не успех.

Дальше — полный набор и линтер, чтобы отделить свои регрессии от чужих:

```powershell
.venv-desktop\Scripts\python -m pytest -q
.venv-desktop\Scripts\python -m ruff check .
```

На macOS в полном наборе стабильно плавают по таймингам
`tests/test_task_pipeline_background_sync.py` и `tests/test_runtime_supervisor.py` —
они не связаны с desktop/Qt. Всё остальное красное считать своим.

## Шаг 3 — интерактивный чеклист D1 (только человек)

Автоматизировать нельзя: нужен подключённый дисплей. Гейт этого прямо требует.

```powershell
.venv-desktop\Scripts\python -m command_center.desktop
```

1. Отрисовались `AppShell`, `Sidebar` (девять разделов; Sessions, Execution, Git,
   Artifacts, Reports, Agents — неактивны) и `TopBar`.
2. Клик по «Главная» / «Проекты» / «Настройки» переключает видимую страницу;
   клик по неактивному пункту не делает ничего.
3. Переключение Light / Dark / System видимо меняет палитру окна.
4. Сменить системную тему Windows «Choose your mode» при режиме «Система» —
   приложение подхватывает её на лету, не требуя перезапуска.
5. **Подвинуть и растянуть окно, выйти, запустить снова — геометрия
   восстанавливается точно, включая ширину.** Это единственный пункт, не закрытый
   ни на одной платформе: у прошлых автоматических сессий не было дисплея, и
   `tests/desktop/test_settings_persistence.py` явно оговаривает, что точность
   ширины проверяется вручную на реальном экране.
6. Выход штатный: без диалога ошибки и без падения.

## Шаг 4 — сборка D4B

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-desktop-windows.ps1
```

Результат: `dist\windows\AI Command Center\AI Command Center.exe`.

Ориентир с macOS: аналогичный бандл там весит ~97 МБ, стартует без traceback,
`streamlit`/`fastapi`/`uvicorn` внутрь не попадают — спека их исключает. Если на
Windows серверный стек оказался внутри или всплыло консольное окно — это дефект
сборки, а не ожидаемое поведение.

## Шаг 5 — smoke на чистой машине (только человек)

`DESKTOP_INCREMENT_1.md` §5 требует машину **без Python и без `.venv`**. Машина,
на которой шли шаги 1–4, чистой уже не является: собранный `.exe` может молча
подхватить системный интерпретатор, и на ней это не проявится.

Годится **Windows Sandbox** (даёт чистую систему на каждый запуск) либо второй
пользовательский аккаунт без Python в `PATH`. Скопировать туда папку
`dist\windows\AI Command Center` целиком и пройти
`packaging\windows\SMOKE_CHECKLIST.md`.

SmartScreen на первом запуске покажет «Windows protected your PC». Это ожидаемое
поведение неподписанной сборки (`PLATFORM_BEHAVIOR.md` §2), а не дефект:
«More info» → «Run anyway».

## Шаг 6 — запись результата

Гейты закрываются записанным результатом, а не устным «всё работает»:

- Windows-нога D1 → `docs/desktop/D1_FINAL_GATE_SMOKE_TEST.md`, рядом с macOS-ногой:
  версия Windows, версии Python и PySide6, вывод pytest, результат по каждому из
  шести пунктов.
- D4B → отметки в `packaging/windows/SMOKE_CHECKLIST.md`.

Что-то не прошло — записывать как есть. Незакрытый гейт с честной причиной лучше,
чем закрытый без доказательств: ровно так и появился PR #83.

## Запрещено

- Подпись и нотаризация, обход SmartScreen средствами сборки
  (`DESKTOP_INCREMENT_1.md` §1, binding decision 12).
- Правки `app.py` и Streamlit-слоя: Increment 1 добавляет потребителя
  `command_center/*`, а не меняет существующий.
- Любые пост-Increment-1 фичи: запуск/отмена run, стриминг, встроенный терминал,
  server mode, SSO, интерфейсы AICOS.
- Правки `command_center/desktop`, `command_center/application`,
  `command_center/platform` сверх того, что требует само пакетирование
  (`DESKTOP_INCREMENT_1.md` §5).
