#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${DESKTOP_PYTHON:-$repo_root/.venv-desktop/bin/python}"

if [[ ! -x "$python_bin" ]]; then
  echo "Не найден Python desktop-окружения: $python_bin" >&2
  echo "Задайте DESKTOP_PYTHON или создайте .venv-desktop." >&2
  exit 2
fi

if [[ "$("$python_bin" -c 'import platform; print(platform.machine())')" != "arm64" ]]; then
  echo "D4A требует Python arm64 на Apple Silicon." >&2
  exit 2
fi

"$python_bin" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$repo_root/dist/macos" \
  --workpath "$repo_root/build/pyinstaller-macos" \
  "$repo_root/packaging/macos/ai-command-center.spec"

echo "Собрано: $repo_root/dist/macos/AI Command Center.app"
