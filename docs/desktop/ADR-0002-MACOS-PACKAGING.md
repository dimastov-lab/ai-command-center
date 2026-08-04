# ADR-0002: упаковка desktop-приложения для macOS

- Статус: принято
- Дата: 2026-07-30
- Область: D4A

## Контекст

В раннем мастер-плане упоминался `pyside6-deploy`, однако обязательные
архитектурные документы проекта (`ARCHITECTURE.md` §16,
`IMPLEMENTATION_ROADMAP.md` D4A) уже выбрали PyInstaller как единый упаковщик
для macOS и Windows.

## Решение

Использовать PyInstaller 6.x для unsigned development `.app`. Единственная
точка входа вызывает `command_center.desktop.app.run`. Ресурсы разрешаются
только через `command_center.platform.resources`, основанный на
`importlib.resources`; application/domain-слои не знают о frozen-режиме.

Сборка запускается `scripts/build-desktop-macos.sh`. Зависимости сборки
отделены от runtime в `requirements-desktop-build.txt`.

## Отклонённая альтернатива

`pyside6-deploy` отклонён для D4A: он создаёт второй toolchain, расходится с
утверждённой общей стратегией macOS/Windows и не даёт преимуществ для текущего
Qt Widgets-приложения без QML-ресурсов.

## Последствия

- Получаем воспроизводимый arm64 `.app` без подписи и notarization.
- Gatekeeper-предупреждение для внутренней сборки ожидаемо.
- Подпись, notarization, DMG и автообновление остаются вне D4A.
