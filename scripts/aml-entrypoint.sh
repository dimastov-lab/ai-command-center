#!/usr/bin/env bash
# AML Service entrypoint
# 1. Создаёт директорию данных (если не смонтирована)
# 2. Засевает правила 115-ФЗ (идемпотентно — пропускает уже существующие)
# 3. Запускает Streamlit

set -euo pipefail

DATA_DIR="${AICC_DATA_DIR:-/data}"
RULES_DB="${DATA_DIR}/aml_rules.db"

echo "[AML] Data directory: ${DATA_DIR}"
mkdir -p "${DATA_DIR}"

echo "[AML] Seeding 115-ФЗ rules (idempotent)..."
python -m command_center.seed_rules_115fz --db "${RULES_DB}"
echo "[AML] Rules seeded."

echo "[AML] Starting Streamlit on :${STREAMLIT_SERVER_PORT:-8501} ..."
exec streamlit run app.py \
    --server.port "${STREAMLIT_SERVER_PORT:-8501}" \
    --server.address "${STREAMLIT_SERVER_ADDRESS:-0.0.0.0}" \
    --server.headless true \
    --browser.gatherUsageStats false
