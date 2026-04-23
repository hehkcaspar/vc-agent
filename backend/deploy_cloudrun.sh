#!/usr/bin/env bash
# One-shot Cloud Run deploy helper.
# Run from the repo root:  bash backend/deploy_cloudrun.sh
#
# Prereqs — see docs/DEPLOYMENT_GCP.md §2 for the full one-time setup.
# Required env when calling this script:
#   PROJECT_ID           gcloud project ID
#   GCS_BUCKET           bucket for workspace blobs (mounted at /mnt/gcs)
#   CLOUD_SQL_INSTANCE   connection name, PROJECT:REGION:INSTANCE
# Optional:
#   REGION               default us-central1
#   SERVICE              default vc-agent-backend
#   CORS_ORIGINS         default "*"
#
# Secrets (created once, referenced by name):
#   gemini-api-key       value: the Gemini API key
#   portfolio-db-url     value: postgresql+asyncpg://USER:PASS@/vc_portfolio?host=/cloudsql/PROJECT:REGION:INSTANCE
#   academic-db-url      value: postgresql+asyncpg://USER:PASS@/academic?host=/cloudsql/PROJECT:REGION:INSTANCE

set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${GCS_BUCKET:?Set GCS_BUCKET (blob mount)}"
: "${CLOUD_SQL_INSTANCE:?Set CLOUD_SQL_INSTANCE (PROJECT:REGION:INSTANCE)}"
: "${REGION:=us-central1}"
: "${SERVICE:=vc-agent-backend}"
: "${REPO:=vc-agent}"
: "${IMAGE_TAG:=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
: "${CORS_ORIGINS:=*}"
: "${APP_PASSWORD:=}"   # empty = no gate (SPA wide open); set to enable login card

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:${IMAGE_TAG}"

echo "==> Building image with Cloud Build: $IMAGE"
gcloud builds submit \
    --project="$PROJECT_ID" \
    --config=cloudbuild.yaml \
    --substitutions="_IMAGE=${IMAGE}" \
    .

echo "==> Deploying to Cloud Run: $SERVICE in $REGION"
gcloud run deploy "$SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="$IMAGE" \
    --platform=managed \
    --port=8080 \
    --cpu=2 \
    --memory=4Gi \
    --min-instances=1 \
    --max-instances=1 \
    --no-cpu-throttling \
    --timeout=3600 \
    --concurrency=40 \
    --execution-environment=gen2 \
    --add-cloudsql-instances="${CLOUD_SQL_INSTANCE}" \
    --update-secrets="GEMINI_API_KEY=gemini-api-key:latest,DATABASE_URL=portfolio-db-url:latest,ACADEMIC_DATABASE_URL=academic-db-url:latest" \
    --set-env-vars="^@@^CORS_ORIGINS=${CORS_ORIGINS}@@GCS_BUCKET=${GCS_BUCKET}@@APP_PASSWORD=${APP_PASSWORD}" \
    --add-volume="name=gcs-data,type=cloud-storage,bucket=${GCS_BUCKET}" \
    --add-volume-mount="volume=gcs-data,mount-path=/mnt/gcs"

echo
echo "==> Service URL:"
gcloud run services describe "$SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format="value(status.url)"
