# Impact OS — productie-image voor always-on hosting (Fly.io / VPS)
# Frontend is statisch (geen build-stap) → we kopiëren frontend/ + backend/.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Systeemdeps (psycopg2-binary + een paar native nodig bij build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies eerst (cache-vriendelijk)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App-code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY data/ ./data/
COPY docs/ ./docs/

# De data-dir moet schrijfbaar zijn voor SQLite + uploads (volume op Fly)
RUN mkdir -p /app/data /app/data/uploads /app/data/workspace \
    && chmod -R 777 /app/data

EXPOSE 8080

# Fly.io zet PORT; uvicorn bindt op 0.0.0.0 zodat de container bereikbaar is.
# IMPACTOS_DB_PATH wijst naar het volume (zie fly.toml [mounts]).
CMD uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8080}"
