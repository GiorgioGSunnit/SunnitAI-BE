FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src:/app/src/be/src

WORKDIR /app

# System dependencies (PDF rendering + curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt /tmp/requirements.txt
COPY src/be/requirement_extration/requirements.txt /tmp/requirements-extration.txt

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt && \
    pip install -r /tmp/requirements-extration.txt && \
    pip install scikit-learn && \
    python -m spacy download it_core_news_lg && \
    rm -rf /tmp/*.txt /root/.cache

# Copy application source
COPY src/be/azure-durable-function/ /home/site/wwwroot/
COPY src/be/requirement_extration/  /app/requirement_extration/
COPY src/be/src/                    /app/src/be/src/
COPY src/core/                      /app/src/core/
COPY src/utils/                     /app/src/utils/

COPY entrypoint-unified.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7071 2025

ENTRYPOINT ["/entrypoint.sh"]
