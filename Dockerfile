# AML Service — производственный образ
# Python 3.13-slim, Streamlit на порту 8501
FROM python:3.13-slim AS base

WORKDIR /app

# Системные зависимости (необходимы для некоторых Python-пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Зависимости устанавливаем отдельным слоем — кешируется при изменении кода
# (vendor/ содержит вендорное колесо aios_sdk, на которое ссылается requirements.txt)
COPY requirements.txt ./
COPY vendor/ ./vendor/
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код приложения
COPY command_center/ ./command_center/
COPY app.py ./

# Entrypoint
COPY scripts/aml-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Директория для данных — монтируется как том
RUN mkdir -p /data
ENV AICC_DATA_DIR=/data

# Streamlit не должен открывать браузер в контейнере
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_HEADLESS=true

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
