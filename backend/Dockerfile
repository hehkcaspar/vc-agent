# syntax=docker/dockerfile:1.7
# Container image for the VC Portfolio Manager FastAPI backend — Cloud Run edition.
#
# Topology on Cloud Run:
#   - Structured DBs (portfolio + academic) → Cloud SQL Postgres, mounted via
#     Cloud Run's built-in `--add-cloudsql-instances` unix socket at
#     /cloudsql/PROJECT:REGION:INSTANCE. DATABASE_URL + ACADEMIC_DATABASE_URL
#     come in from Secret Manager at deploy time.
#   - Workspace blobs + scholar dossiers + config JSONs → GCS bucket mounted
#     at /mnt/gcs via Cloud Run's Cloud Storage volume mount (FUSE; read
#     caches in memory per-instance).
#   - `--max-instances=1 --no-cpu-throttling` keeps the in-process heartbeat
#     scheduler alive and avoids duplicate heartbeats. Lift max-instances
#     once heartbeat is split out to Cloud Scheduler.
#
# Full setup in docs/DEPLOYMENT_GCP.md.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System binaries: LibreOffice (legacy office conversion), Ghostscript
# (PDF compression before Gemini upload), tini (reaps LibreOffice subprocesses).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
      ghostscript \
      ca-certificates \
      tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first for layer caching.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r backend/requirements.txt

COPY backend/ ./backend/

RUN chmod +x /app/backend/entrypoint.sh

# Split storage: Cloud SQL for the two databases (DATABASE_URL +
# ACADEMIC_DATABASE_URL overridden via Secret Manager at deploy time), GCS
# FUSE mount for the "complicated filesystem" (blobs, dossiers, config JSONs).
# Every one of these is a Pydantic Settings field → env-overridable.
ENV DATA_ROOT=/mnt/gcs/entities \
    ACADEMIC_SCHOLARS_DIR=/mnt/gcs/scholars \
    ACADEMIC_CONFIG_DIR=/mnt/gcs/config \
    FUNDS_CONFIG_PATH=/mnt/gcs/config/funds.json \
    LEGAL_TEMPLATES_CONFIG_PATH=/mnt/gcs/config/legal_templates.json \
    LEGAL_REVIEW_CHECKLIST_CONFIG_PATH=/mnt/gcs/config/legal_review_checklist.json

RUN mkdir -p /mnt/gcs

WORKDIR /app/backend

# Cloud Run injects PORT; we default to 8080 for local `docker run`.
EXPOSE 8080

# tini reaps zombies (LibreOffice spawns subprocesses that can orphan).
ENTRYPOINT ["/usr/bin/tini", "--", "/app/backend/entrypoint.sh"]
