# syntax=docker/dockerfile:1.7
# Container image for the VC Portfolio Manager FastAPI backend.
#
# Not for Vercel Functions — Vercel's Python runtime can't run subprocess
# calls to LibreOffice and has a 4.5 MB request-body cap that breaks
# workspace uploads. Deploy this image to any container host (Fly.io,
# Railway, Render, Cloud Run, a VPS). See docs/DEPLOYMENT_VERCEL.md.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System binaries that office_extractors.py + document_text.py shell out to.
# libreoffice-core + libreoffice-writer/calc/impress covers doc/ppt/xls
# conversion. ghostscript is used to compress PDFs before sending them to
# Gemini as inline bytes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
      ghostscript \
      curl \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches independently of app code.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r backend/requirements.txt

# Application code.
COPY backend/ ./backend/

# Writable data dir. On container hosts, mount a persistent volume here so
# SQLite DBs, workspace blobs, scholar dossiers, and config files survive
# restarts and redeploys.
ENV DATA_ROOT=/data/entities \
    DATABASE_URL=sqlite+aiosqlite:////data/vc_portfolio.db \
    ACADEMIC_DATABASE_URL=sqlite+aiosqlite:////data/academic.db
RUN mkdir -p /data /data/entities /data/scholars /data/config /data/logs

EXPOSE 8000

WORKDIR /app/backend

# Single worker by default. Agent jobs are in-process asyncio tasks and
# scale per-worker, not per-request — stick to one worker per container
# and scale horizontally if you need more throughput.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
