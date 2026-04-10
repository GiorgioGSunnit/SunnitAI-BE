FROM mcr.microsoft.com/azure-functions/python:4-python3.11

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    FUNCTIONS_WORKER_RUNTIME=python \
    ASPNETCORE_URLS=http://0.0.0.0:7071 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src:/app/src/be/src

WORKDIR /app

# Build dependencies - removed gcc (not needed without torch)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements first for layer caching
COPY requirements.txt /tmp/requirements.txt
COPY src/be/requirement_extration/requirements.txt /tmp/requirements-extration.txt

# Install Python deps in single layer
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements-extration.txt && \
    pip install --no-cache-dir azure-functions \
        azurefunctions-extensions-bindings-blob \
        azurefunctions-extensions-http-fastapi \
        scikit-learn && \
    python -m spacy download it_core_news_lg && \
    rm -rf /tmp/*.txt /root/.cache

COPY src/be/azure-durable-function/ /home/site/wwwroot/
COPY src/be/requirement_extration/ /app/requirement_extration/
COPY src/be/src/ /app/src/be/src/
COPY src/core/ /app/src/core/
COPY src/utils/ /app/src/utils/

COPY entrypoint-unified.sh /entrypoint.sh
COPY scripts/pod-bashrc /root/.bashrc
RUN chmod +x /entrypoint.sh

EXPOSE 7071 2025

ENTRYPOINT ["/entrypoint.sh"]
