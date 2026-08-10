# W2 AICC artifact manifest

`scripts/build_release_manifest.py` формирует воспроизводимый release-manifest для RC на основе входного списка artifacts и точного `source_sha`.

## Контракт

- `source_sha`: обязательный 40-символьный SHA (`[0-9a-f]{40}`).
- Каждый artifact обязан содержать:
  - `path` — путь к существующему файлу;
  - `platform` — `linux|macos|windows|web|streamlit|cross-platform`;
  - `artifact_type` — тип артефакта (например, `web_bundle`, `desktop_installer`, `python_wheel`);
  - `signing_status` — `unknown|signed|unsigned|blocked|not_applicable`.
- Дополнительно:
  - `signing_required` (по умолчанию `true`);
  - `signing_identity` (`null` или строка).

Выходной JSON (`schema_version=1`) фиксирует по каждому artifact:

- имя и относительный путь;
- платформу и тип;
- размер;
- `sha256`;
- signing slot (`required/status/identity`).

## Запуск

```bash
python scripts/build_release_manifest.py \
  --source-sha <exact_git_sha> \
  --spec-file <artifacts.json> \
  --output .artifacts/release-manifest.json
```

`artifacts.json` может быть массивом объектов или объектом с ключом `artifacts`.
