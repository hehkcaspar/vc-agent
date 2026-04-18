#!/usr/bin/env bash
# Cloud Run entrypoint. Minimal: bind to $PORT, hand off to uvicorn.
#
# DATABASE_URL + ACADEMIC_DATABASE_URL come in as Cloud Run env vars (from
# Secret Manager). If unset — e.g. `docker run` locally without overrides —
# backend/app/config.py defaults to SQLite under ./data/, which is fine for
# a quick sanity check but nothing persists after the container exits.
set -euo pipefail

PORT="${PORT:-8080}"

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*'
