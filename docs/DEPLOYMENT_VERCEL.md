# Deployment: Vercel (frontend) + container host (backend)

This app deploys as **two services**: the Vite + React frontend goes on Vercel,
the FastAPI backend goes on a long-lived container host (Fly.io recommended;
Railway / Render / Cloud Run work with the same Dockerfile). Vercel-only
deployment is not viable — see [Why not full-Vercel](#why-not-full-vercel).

## 1. Architecture

```
 ┌────────────────────────┐           ┌─────────────────────────────┐
 │   Vercel (frontend)    │   HTTPS   │   Fly.io (backend)          │
 │   Vite + React build   │ ────────▶ │   FastAPI + uvicorn         │
 │   dist/ served on CDN  │  CORS     │   LibreOffice + Ghostscript │
 │                        │           │   SQLite on persistent vol  │
 └────────────────────────┘           └─────────────────────────────┘
          VITE_API_URL                        /data volume
```

The browser calls the backend directly using `VITE_API_URL`. This avoids
Vercel rewrite caching (Vercel honors upstream `Cache-Control` by default as
of April 2026) and keeps both deploys independently observable.

## 2. Frontend → Vercel

1. **Import the repo** in the Vercel dashboard → _Add New → Project_.
2. **Root Directory:** `frontend/`. Vercel auto-detects Vite from
   `frontend/vercel.json` + `package.json`.
3. **Environment Variables** (Production + Preview):
   - `VITE_API_URL` = `https://<your-backend>.fly.dev` (no trailing slash)
4. Deploy. `npm run build` runs `tsc && vite build`; output is `dist/`.

### SPA routing

`vercel.json` declares the Vite framework preset, which handles SPA fallback
for React Router automatically. No manual rewrite rules needed.

## 3. Backend → Fly.io

Prereqs: `flyctl` installed, logged in (`flyctl auth login`).

```bash
# From repo root. fly.toml lives in backend/ but Dockerfile COPYs from ../
cd backend
flyctl launch --no-deploy --copy-config --name vc-agent-backend

# Persistent volume for SQLite + workspace blobs + scholar dossiers
flyctl volumes create vc_agent_data --region iad --size 10

# Required + common secrets
flyctl secrets set \
  GEMINI_API_KEY=sk-... \
  CORS_ORIGINS=https://vc-agent.vercel.app

# Deploy
flyctl deploy
```

The Dockerfile `COPY backend/ ./backend/` assumes the build context is the
repo root. If `fly launch` set the Dockerfile path relative to `backend/`,
edit `fly.toml` to build from the repo root (`dockerfile = "backend/Dockerfile"`
and run `flyctl deploy ..` from inside `backend/`). Easiest path: run
`flyctl deploy` from the repo root with `--config backend/fly.toml
--dockerfile backend/Dockerfile`.

### Persistent volume

`[mounts]` in `fly.toml` mounts `vc_agent_data` at `/data`. The Dockerfile
points `DATA_ROOT`, `DATABASE_URL`, and `ACADEMIC_DATABASE_URL` at that
path so SQLite files, workspace blobs (`/data/entities/...`), scholar
dossiers (`/data/scholars/...`), and config JSONs (`/data/config/...`)
survive deploys and machine restarts.

### Scaling

Keep one machine per app unless you've migrated SQLite to Postgres.
SQLite doesn't tolerate concurrent writers across processes, and the
academic heartbeat scheduler expects exactly one instance.

```bash
flyctl scale count 1
flyctl scale vm shared-cpu-2x --memory 2048
```

## 4. Environment variables

### Frontend (Vercel)

| Var | Required | Example |
|---|---|---|
| `VITE_API_URL` | yes (prod) | `https://vc-agent-backend.fly.dev` |

### Backend (Fly secrets)

| Var | Required | Notes |
|---|---|---|
| `GEMINI_API_KEY` | **yes** | Google AI Studio key |
| `CORS_ORIGINS` | yes (prod) | Comma-separated origins. Set to the Vercel URL(s). |
| `GEMINI_MODEL` | no | Default `gemini-3.1-pro-preview` |
| `GEMINI_METADATA_EXTRACTION_MODEL` | no | Default `gemini-3.1-flash-lite-preview` |
| `ACADEMIC_GEMINI_MODEL` | no | Default `gemini-3-flash-preview` |
| `CHAT_DEFAULT_AGENT_MODE` | no | `one_shot` \| `react` \| `deep_agent` |
| `CHAT_AGENT_RECURSION_LIMIT` | no | Default 100 |
| `MOONSHOT_API_KEY` / `KIMI_CODE_API_KEY` | no | Enables Kimi model profile |
| `SEMANTIC_SCHOLAR_API_KEY` | no | Academic tracking — free tier works without |
| `SERPAPI_KEY` | no | Academic tracking — Google Scholar metrics |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | no | Optional tracing |
| `DATA_ROOT` / `DATABASE_URL` / `ACADEMIC_DATABASE_URL` | no | Overridden by `fly.toml` to point at `/data` |

## 5. Local Docker sanity check

Before the first Fly deploy, verify the image builds and starts locally:

```bash
# From repo root
docker build -f backend/Dockerfile -t vc-agent-backend .

docker run --rm -p 8000:8000 \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  -e CORS_ORIGINS='*' \
  -v "$PWD/data:/data" \
  vc-agent-backend

# Then in another shell:
curl http://localhost:8000/health   # {"status":"healthy"}
```

LibreOffice-dependent uploads (legacy `.doc/.ppt/.xls`) should now succeed —
they'd fail silently on a stock Python base image.

## 6. CORS notes

`backend/app/main.py` reads `CORS_ORIGINS` on startup. `"*"` (default) is
fine for dev but Starlette drops cookies/credentials when origin is `*`; the
concrete list enables `allow_credentials=True` automatically.

Preview deploys on Vercel get unique URLs (`vc-agent-git-feature-x.vercel.app`).
Either:
- Add each preview URL to `CORS_ORIGINS`, or
- Use a wildcard pattern via a Starlette origin regex (would require a small
  code change — current impl uses the exact-match list).

## 7. Alternative backend hosts

The Dockerfile is host-agnostic. Equivalent configs:

- **Railway**: point service at the Dockerfile, attach a volume at `/data`,
  set the same secrets.
- **Render**: Web Service → Docker → `backend/Dockerfile`, attach a disk
  at `/data`, set env vars. Render's free tier has ephemeral disks → use
  the paid tier or swap SQLite for a managed Postgres.
- **Google Cloud Run**: supports Dockerfiles but filesystem is ephemeral.
  You'll need to migrate `data/` to Cloud Storage + Cloud SQL first.
- **Plain VPS (Hetzner, DO)**: `docker compose` with a named volume.

## 8. Migrating SQLite → Postgres (later)

When single-node SQLite becomes limiting (concurrent writers, multi-region,
horizontal scale), swap to Neon / Supabase / RDS:

1. Provision a Postgres instance, capture the URL.
2. Install `asyncpg`: add `asyncpg==0.30.0` to `backend/requirements.txt`.
3. Set `DATABASE_URL=postgresql+asyncpg://user:pass@host/db` and
   `ACADEMIC_DATABASE_URL=...` as Fly secrets.
4. SQLAlchemy's `Base.metadata.create_all` in `init_db` / `init_academic_db`
   will create the schema on first boot. The idempotent `ALTER TABLE`
   migrations in `app/main.py` lifespan use `PRAGMA table_info(...)` which
   is SQLite-specific — rewrite them against `information_schema.columns`
   before switching, or drop them once the schema is stable.
5. Migrate existing data with `pgloader` or a custom script.

Blob storage (`/data/entities/<id>/workspace/blobs/`) is separate and can
stay on the Fly volume, or migrate to Vercel Blob / S3 at the same time.

## Why not full-Vercel

| Constraint | Issue |
|---|---|
| **LibreOffice subprocess** | `services/office_extractors.py` shells out to `soffice` for legacy `.doc/.ppt/.xls`. Not installable in Vercel's Python runtime. Vercel Sandbox could run it, but invoking it per-upload adds latency + cost. |
| **Ghostscript subprocess** | PDF compression before Gemini upload. Same story. |
| **SQLite on filesystem** | Vercel Function filesystem is ephemeral + per-invocation-isolated. Would need to migrate both DBs to Neon Postgres first. |
| **Workspace blob storage** | `data/entities/<id>/workspace/blobs/...` written via `LocalFilesystemAdapter`. Would need to migrate to Vercel Blob (512 MB max per blob). |
| **4.5 MB request body cap** | Vercel Functions hard-limit. Workspace supports 50 MB per file / 500 MB per zip uploads. Client-direct upload to Blob would bypass, but adds a whole layer. |
| **Long-running agent jobs** | Fluid Compute timeout is 300s (Hobby) / 800s (Pro). `initial_screening_v2` can run 5+ min. Would need Vercel Queues + Workflows. |
| **In-process heartbeat scheduler** | `HeartbeatScheduler` is a 60s asyncio tick in the FastAPI lifespan, assuming a long-lived process. Vercel Functions cold-start per invocation. Would need Vercel Cron + state coordination. |

Every blocker has a Vercel-native solution, but collectively they amount to
rewriting a third of the backend. A $5–$10/month Fly machine runs the whole
thing unchanged.

## Reference

- [Vercel: Vite framework](https://vercel.com/docs/frameworks/vite)
- [Vercel: vercel.json config](https://vercel.com/docs/project-configuration/vercel-json)
- [Fly.io: Dockerfile deploy](https://fly.io/docs/languages-and-frameworks/dockerfile/)
- [Fly.io: Volumes](https://fly.io/docs/volumes/overview/)
- [FastAPI + Vercel limitations](https://vercel.com/docs/frameworks/backend/fastapi) — for context on what the serverless path looks like if you ever need it.
