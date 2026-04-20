# Deployment: Firebase Hosting (frontend) + Cloud Run + Cloud SQL (backend)

Two-service deploy on Google Cloud. Firebase Hosting serves the Vite SPA;
Cloud Run runs the FastAPI backend; **Cloud SQL Postgres** holds the two
structured databases; **GCS** holds the workspace blob + scholar dossier
filesystem.

## 1. Architecture

```
 ┌──────────────────────────┐        ┌─────────────────────────────────────┐
 │  Firebase Hosting (CDN)  │  ───▶  │  Cloud Run: vc-agent-backend        │
 │  dist/ built by Vite     │ /api/* │  FastAPI + LibreOffice + Ghostscript│
 │  SPA rewrite for routes  │ rewrite│                                     │
 └──────────────────────────┘        │   unix socket /cloudsql/… ⟷ Cloud   │
                                     │                               SQL   │
                                     │     ├─ DB: vc_portfolio             │
                                     │     └─ DB: academic                 │
                                     │                                     │
                                     │   FUSE mount /mnt/gcs     ⟷ GCS    │
                                     │     ├─ entities/  (blobs, versions) │
                                     │     ├─ scholars/  (dossiers)        │
                                     │     └─ config/    (funds, legal)    │
                                     └─────────────────────────────────────┘
```

Key decisions:

- **Firebase `rewrites.run` instead of direct CORS.** The browser only ever
  talks to `https://<project>.web.app`. Firebase server-side-proxies
  `/api/**` to Cloud Run. No CORS preflight, one TLS cert.
- **Cloud SQL Postgres for structured data.** One Cloud SQL instance hosts
  both logical databases (`vc_portfolio` and `academic`). Cloud Run mounts
  the instance as a unix socket at `/cloudsql/PROJECT:REGION:INSTANCE` via
  `--add-cloudsql-instances` — no IP allowlists, no password in transit,
  IAM-gated.
- **GCS for the "complicated filesystem."** Workspace blobs, scholar
  dossiers, config JSONs live on GCS, FUSE-mounted at `/mnt/gcs`. FUSE
  caches reads in memory per-instance.
- **`--min-instances=1 --max-instances=1 --no-cpu-throttling`.** Max=1
  because the academic heartbeat scheduler is an in-process asyncio tick
  — two instances would fire it twice. Min=1 + no-throttling because
  Cloud Run's default "freeze CPU between requests" mode stops
  `asyncio.sleep`. Lift max-instances once heartbeat is split out to
  Cloud Scheduler.
- **LibreOffice + Ghostscript baked into the image.** Both are
  apt-installable; the container weighs ~700 MB and boots in ~10s.

## 2. One-time GCP project setup

```bash
# Pick your IDs
export PROJECT_ID=my-vc-agent
export REGION=us-central1
export GCS_BUCKET=${PROJECT_ID}-data
export SQL_INSTANCE=vc-agent-db
export CLOUD_SQL_INSTANCE=${PROJECT_ID}:${REGION}:${SQL_INSTANCE}
export DB_USER=vc
export DB_PASSWORD=$(openssl rand -base64 24)   # save this somewhere
export SA_EMAIL=$(gcloud projects describe "$PROJECT_ID" \
    --format='value(projectNumber)')-compute@developer.gserviceaccount.com

gcloud config set project "$PROJECT_ID"

# APIs
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    sqladmin.googleapis.com \
    firebasehosting.googleapis.com

# Artifact Registry repo
gcloud artifacts repositories create vc-agent \
    --repository-format=docker \
    --location="$REGION"

# Cloud SQL Postgres instance (db-f1-micro for MVP; upgrade later).
# --edition=ENTERPRISE is required for the shared-core tier; new projects
# default to ENTERPRISE_PLUS which rejects db-f1-micro.
gcloud sql instances create "$SQL_INSTANCE" \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-f1-micro \
    --region="$REGION" \
    --storage-size=10GB \
    --storage-auto-increase \
    --backup-start-time=03:00

# The two databases + a single user
gcloud sql databases create vc_portfolio --instance="$SQL_INSTANCE"
gcloud sql databases create academic     --instance="$SQL_INSTANCE"
gcloud sql users create "$DB_USER" \
    --instance="$SQL_INSTANCE" \
    --password="$DB_PASSWORD"

# GCS bucket for the blob filesystem
gcloud storage buckets create "gs://${GCS_BUCKET}" \
    --location="$REGION" \
    --uniform-bucket-level-access

# Cloud Run runtime SA — needs storage + cloudsql + secret access.
# Post-2024 projects use the default compute SA as the Cloud Build worker
# as well, so it also needs `cloudbuild.builds.builder` (covers log write,
# source read, AR push) otherwise `gcloud builds submit` 403s.
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectUser"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/cloudsql.client" --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/cloudbuild.builds.builder" --condition=None

# Secrets
printf 'YOUR_GEMINI_KEY' | \
    gcloud secrets create gemini-api-key --data-file=-

printf "postgresql+asyncpg://%s:%s@/vc_portfolio?host=/cloudsql/%s" \
    "$DB_USER" "$DB_PASSWORD" "$CLOUD_SQL_INSTANCE" | \
    gcloud secrets create portfolio-db-url --data-file=-

printf "postgresql+asyncpg://%s:%s@/academic?host=/cloudsql/%s" \
    "$DB_USER" "$DB_PASSWORD" "$CLOUD_SQL_INSTANCE" | \
    gcloud secrets create academic-db-url --data-file=-

# Grant the Cloud Run SA read on each secret
for s in gemini-api-key portfolio-db-url academic-db-url; do
    gcloud secrets add-iam-policy-binding "$s" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/secretmanager.secretAccessor"
done

# If the project lives under a Google Workspace org that enforces the
# `iam.allowedPolicyMemberDomains` constraint (domain-restricted sharing),
# binding `allUsers` to the Cloud Run service will fail with
# `FAILED_PRECONDITION: One or more users named in the policy do not belong
# to a permitted customer`. Match the pattern other web projects use by
# overriding the policy at the project level. `causally.xyz` already does
# this on seeat-webapp-*.
cat > /tmp/orgpolicy_allow_all.yaml <<EOF
name: projects/$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')/policies/iam.allowedPolicyMemberDomains
spec:
  rules:
  - allowAll: true
EOF
gcloud services enable orgpolicy.googleapis.com
gcloud org-policies set-policy /tmp/orgpolicy_allow_all.yaml --project="$PROJECT_ID"
```

Universal config files self-seed on first boot — no deploy-time upload
needed. Two mechanisms, both read-on-startup from the repo and both
no-op when the runtime file already exists:

- **Reader-module self-seed** (since 2026-04-20): `dimensions.json`
  seeds from `backend/app/services/academic/dimensions_seed.json` via
  `dimensions.read_dimensions()`; `continuous_tasks.json` seeds from
  `backend/app/services/academic/continuous_tasks_seed.json` via
  `continuous_config.load_continuous_tasks()`.
- **Lifespan copy-on-startup** (`ensure_universal_configs_seeded()` +
  `ensure_*_seed()` in `main.py`): `field_archetypes.json`,
  `heartbeat.json`, `ranking_presets/*.json` copy from
  `backend/app/defaults/` → `/mnt/gcs/config/`. `legal_templates.json`
  and `legal_review_checklist.json` seed from Pydantic-validated inline
  Python defaults in their config modules.

**Per-environment files (`funds.json`, `digests/*.md`) are intentionally
NOT seeded** — `funds.json` comes from user action via the Settings UI,
digests are generated on schedule.

## 3. Deploy the backend

```bash
# From repo root
export PROJECT_ID=my-vc-agent
export REGION=us-central1
export GCS_BUCKET=${PROJECT_ID}-data
export CLOUD_SQL_INSTANCE=${PROJECT_ID}:${REGION}:vc-agent-db
export CORS_ORIGINS="https://${PROJECT_ID}.web.app"   # or leave "*" for first boot

bash backend/deploy_cloudrun.sh
```

The script runs Cloud Build via `cloudbuild.yaml` at the repo root (builds
linux/amd64 remotely, faster than local docker for most laptops), then
`gcloud run deploy` with the full flag set. The script deliberately omits
both `--allow-unauthenticated` and `--no-allow-unauthenticated` so it
doesn't clobber an existing IAM state on re-deploy. Set the public/private
state once via console or `gcloud run services add-iam-policy-binding`;
subsequent `bash backend/deploy_cloudrun.sh` runs preserve it.

See `backend/deploy_cloudrun.sh` for the exact command.

Watch the first boot:

```bash
gcloud run services logs tail vc-agent-backend --region "$REGION"
```

Expect `init_db()` + `init_academic_db()` to run on the first request,
which creates all tables via `Base.metadata.create_all`. The lifespan in
`app/main.py` then runs idempotent migrations — `inspect()`-based
column-adds (dialect-agnostic), a Postgres-only `_FK_CASCADE_SPEC` loop
that re-issues FK constraints with `ON DELETE CASCADE` via
`pg_constraint.confdeltype` checks, and `ensure_*_seed()` /
`ensure_universal_configs_seeded()` for the universal config files.

## 4. Deploy the frontend

```bash
cd frontend
npm install -g firebase-tools         # if not installed
firebase login                        # needs a REAL TTY — run in macOS
                                      # Terminal.app, not Claude Code's `!`
                                      # shell. Token cached at
                                      # ~/.config/configstore/firebase-tools.json
firebase projects:addfirebase "$PROJECT_ID"  # one-time: register GCP project
                                             # with Firebase (not needed if
                                             # already a Firebase project).
# Edit .firebaserc → replace REPLACE-WITH-YOUR-FIREBASE-PROJECT-ID with
# the project that owns Firebase Hosting (usually same as $PROJECT_ID).
npm install
npm run build
firebase deploy --only hosting --project="$PROJECT_ID"
```

Firebase returns a URL like `https://my-vc-agent.web.app`. If you want
the browser to call Cloud Run directly (bypassing Firebase's
`rewrites.run` proxy), redeploy the backend with
`CORS_ORIGINS=https://my-vc-agent.web.app`. With rewrites, CORS is
irrelevant — the request is same-origin.

## 5. Environment variables

### Frontend (build-time)

| Var | Required | Notes |
|---|---|---|
| `VITE_API_URL` | no | Leave unset to use the Firebase rewrite (recommended). Set only to bypass Firebase and call Cloud Run directly. |

### Backend (Cloud Run env + secrets)

| Var | Source | Required | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | Secret Manager | **yes** | `gemini-api-key` secret |
| `DATABASE_URL` | Secret Manager | **yes** | `portfolio-db-url` — full Postgres URL with `host=/cloudsql/...` |
| `ACADEMIC_DATABASE_URL` | Secret Manager | **yes** | `academic-db-url` — same instance, `academic` DB |
| `CORS_ORIGINS` | env | yes (prod) | Redundant when using Firebase rewrites only |
| Storage paths (`DATA_ROOT`, `ACADEMIC_SCHOLARS_DIR`, etc.) | env | no | Dockerfile already sets to `/mnt/gcs/*`; override only to change the mount layout |
| `GEMINI_MODEL`, `ACADEMIC_GEMINI_MODEL`, `CHAT_DEFAULT_AGENT_MODE`, `CHAT_AGENT_RECURSION_LIMIT` | env | no | Tuning |
| `SEMANTIC_SCHOLAR_API_KEY`, `SERPAPI_KEY`, `MOONSHOT_API_KEY` | Secret Manager | no | Additional integrations |

## 6. Local Docker sanity check

```bash
# From repo root
docker build -f backend/Dockerfile -t vc-agent-backend:local .

# Quick smoke test — SQLite-on-ephemeral-disk, no GCS, no Cloud SQL
docker run --rm -p 8080:8080 \
    -e GEMINI_API_KEY="$GEMINI_API_KEY" \
    -e CORS_ORIGINS='*' \
    -e DATABASE_URL='sqlite+aiosqlite:////tmp/vc.db' \
    -e ACADEMIC_DATABASE_URL='sqlite+aiosqlite:////tmp/academic.db' \
    -e DATA_ROOT=/tmp/entities \
    -e ACADEMIC_SCHOLARS_DIR=/tmp/scholars \
    -e ACADEMIC_CONFIG_DIR=/tmp/config \
    -e FUNDS_CONFIG_PATH=/tmp/config/funds.json \
    -e LEGAL_TEMPLATES_CONFIG_PATH=/tmp/config/legal_templates.json \
    -e LEGAL_REVIEW_CHECKLIST_CONFIG_PATH=/tmp/config/legal_review_checklist.json \
    vc-agent-backend:local

curl http://localhost:8080/health
```

For end-to-end testing against real Cloud SQL, use the **Cloud SQL Auth
Proxy** locally:

```bash
cloud-sql-proxy "${PROJECT_ID}:${REGION}:${SQL_INSTANCE}" &
DATABASE_URL="postgresql+asyncpg://vc:${DB_PASSWORD}@127.0.0.1:5432/vc_portfolio" \
ACADEMIC_DATABASE_URL="postgresql+asyncpg://vc:${DB_PASSWORD}@127.0.0.1:5432/academic" \
    python backend/run.py
```

## 7. Persistence semantics

| Data | Location | Durable? | Notes |
|---|---|---|---|
| Postgres tables (portfolio + academic) | Cloud SQL | yes | Daily backups, 7-day retention by default; enable PITR if you want finer recovery |
| Workspace blobs (`/mnt/gcs/entities/*`) | GCS | yes | Versioning lives in the repo-level `.versions/` dir inside the mount |
| Scholar dossiers (`/mnt/gcs/scholars/*`) | GCS | yes | |
| Config JSONs (`/mnt/gcs/config/*.json`) | GCS | yes | Seeded on first boot if missing |
| In-flight background jobs | In-memory on Cloud Run instance | **no** | Killed by container recycle; `status='evaluating'` reset on boot recovers scholars but chat jobs stay stuck |

## 8. Known gotchas

### 8a. Runtime

- **Cold-start ~8–15s.** First request after scale-up pays Python import +
  SQLAlchemy connect. `--min-instances=1` keeps one warm, so this only
  happens after a deploy.
- **GCS FUSE throughput ~10 MB/s.** Fine for document uploads; don't
  write log files there. Cloud Run captures stdout/stderr to Cloud
  Logging automatically.
- **Atomic `os.replace` on GCS FUSE is NOT atomic.** Rename = copy + delete.
  The config-file writers in `services/*_config.py` use tmp-then-rename
  which is still correct — just slower (~100-200ms per write). Only
  matters for the seed-on-startup flow.
- **Cloud Run volume mounts are Preview (2026).** Monitor release notes.
  If the mount ever 500s, blob reads/writes fail until it recovers.
- **Container recycle kills in-flight agent jobs.** FastAPI BackgroundTasks
  are in-process. Mid-job recycles (platform maintenance, OOM) lose the
  job. Portfolio `ChatCompletionJob` rows in `running` status stay
  stuck; user must requeue. Fix forward: move to Cloud Tasks.
- **LibreOffice CPU spikes.** Legacy-office conversion pegs 1 CPU for
  several seconds. If p95 chat latency spikes, that's probably an upload
  concurrent with a conversion. Bump `--cpu` to 4.

### 8b. Deploy-time gotchas (learned during first vc-agent-taihill deploy)

- **Cloud SQL ENTERPRISE_PLUS is the default for new projects.** That
  edition rejects shared-core tiers (`db-f1-micro`, `db-g1-small`) with
  `Invalid Tier … for (ENTERPRISE_PLUS) Edition`. Pass
  `--edition=ENTERPRISE` explicitly (see §2). Only affects the cheap MVP
  tier — `db-custom-*` and `db-perf-optimized-*` work on either edition.
- **Compute SA needs `cloudbuild.builds.builder` on new projects.**
  `gcloud builds submit` fails with `storage.objects.get … forbidden`
  because the default build worker (compute SA post-2024) lacks the role.
  Grant is in §2.
- **`gcloud builds submit --file=` is gone.** Newer gcloud drops the
  flag — this repo ships `cloudbuild.yaml` at the root and the deploy
  script uses `--config=cloudbuild.yaml --substitutions=_IMAGE=…`.
- **`--set-env-vars` treats commas as dict separators.** A value
  containing commas (e.g. `CORS_ORIGINS=https://a,https://b`) fails with
  `Bad syntax for dict arg`. Use the escape syntax:
  `--set-env-vars="^@@^CORS_ORIGINS=https://a,https://b"`. The `@@` (or
  any chosen string) becomes the env-var separator instead of `,`.
  `deploy_cloudrun.sh` already does this.
- **`iam.allowedPolicyMemberDomains` org policy blocks `allUsers`.**
  `causally.xyz` org enforces domain-restricted sharing. The fix is a
  project-level policy override (`allowAll: true`) — see §2. Without it,
  you cannot bind `allUsers → roles/run.invoker`, which is required for
  Firebase Hosting classic `rewrites.run` to reach Cloud Run.
- **Classic Firebase `rewrites.run` does NOT carry auth tokens.** The
  Firebase edge proxies unauthenticated HTTP, so it cannot invoke an
  IAP-protected or `--no-allow-unauthenticated` Cloud Run. Options:
  (a) keep Cloud Run public + add app-level auth (what this repo does,
  matching `seeat-webapp-*`); (b) migrate to Firebase App Hosting (Web
  Frameworks) which does pass identity; (c) bake the SPA into the Cloud
  Run image and drop Firebase Hosting so one origin + IAP covers
  everything. Don't try to mix classic Hosting rewrites with IAP.
- **The Firebase Hosting P4SA can't be pre-provisioned.**
  `gcloud beta services identity create --service=firebasehosting.googleapis.com`
  returns `IAM_SERVICE_NOT_CONFIGURED_FOR_IDENTITIES`. Just deploy
  once and the SA materialises if/when it's needed.
- **`firebase login` needs a real TTY.** Claude Code's `!` prefix runs
  without one; open a separate macOS Terminal window to run
  `firebase login` once. Token is saved to
  `~/.config/configstore/firebase-tools.json` and reused everywhere.
- **Frontend expects `/api/*`; backend serves unprefixed routes.**
  Vite dev proxy strips `/api`. Firebase Hosting rewrites do NOT strip —
  `main.py` has a small `_strip_api_prefix` middleware that normalises
  production to match dev. If you ever introduce a genuine `/api/...`
  route on the backend, that middleware will eat the prefix before
  matching.
- **Firebase CDN caches 404s for 10 minutes.** After a backend fix that
  flips a 404 → 200, `max-age=600` keeps the stale 404 in edge cache.
  Append a cache-busting query (`?cb=…`) to verify immediately, or wait
  it out.
- **`gcloud org-policies set-policy` requires `orgpolicy.googleapis.com`
  to be enabled.** It is NOT in the base API-enable list of §2 since
  most deploys inherit policy from the org; enable lazily when you need
  a per-project override.
- **Postgres enforces FKs; SQLite doesn't — ORM cascade isn't enough.**
  `Base.metadata.create_all()` renders `ForeignKey("entities.id")` as a
  constraint with `NO ACTION` as the delete rule. SQLite ignores that;
  Postgres aborts `DELETE FROM entities WHERE …` whenever any child
  table still references the row, even if SQLAlchemy's Python-side
  `cascade="all, delete-orphan"` intended to delete the children first
  (the ORM emits per-table batch DELETEs, and a self-referential FK
  like `workspace_nodes.parent_id` fails in the batch because rows
  reference siblings that aren't gone yet). Every FK into `entities.id`
  (`conversation_sessions`, `chat_completion_jobs`, `workspace_nodes`,
  `workspace_ops`) plus the self-reference on `workspace_nodes.parent_id`
  now declares `ondelete="CASCADE"` in `models.py`, and the lifespan
  migration in `main.py` re-issues the constraint via
  `pg_constraint.confdeltype` check if an existing Postgres DB was
  created before that fix. If you see a future `violates foreign key
  constraint "<x>_fkey"` on entity delete, add the offending FK to
  `_FK_CASCADE_SPEC` in `main.py`.
- **Universal configs self-seed; never deploy-upload generated content.**
  Two seeding mechanisms (both no-op when target exists):
  - Reader-module self-seed for `dimensions.json` (from
    `backend/app/services/academic/dimensions_seed.json`) and
    `continuous_tasks.json` (from `.../continuous_tasks_seed.json`) —
    triggered lazily on first call to `read_dimensions()` /
    `load_continuous_tasks()`.
  - `ensure_universal_configs_seeded()` in
    `services/config_seeding.py` (wired into `main.py` lifespan) —
    copies `field_archetypes.json`, `heartbeat.json`,
    `ranking_presets/*.json` from `backend/app/defaults/` →
    `/mnt/gcs/config/`.
  - `legal_templates.json` + `legal_review_checklist.json` seed from
    Pydantic-validated inline Python defaults in their config modules.
  **Never `gsutil cp -r data/config/*`** — that's how a dev-machine
  weekly digest leaked into prod on 2026-04-18. If you must seed a
  per-environment file (e.g. `funds.json`), target the single file
  explicitly and never the whole dir.
- **Self-seed preserves existing files — rename-in-source means stale
  prod config.** The seeder only writes when the target is missing, so
  any structural rename (e.g. `patents_lens` → `patents_web` when the
  scaffold became a real source) leaves the prod file pinned on the
  old keys. Two forcing-function patterns depending on which mechanism
  owns the file:
  - **Reader-module-seeded** (`dimensions.json`,
    `continuous_tasks.json`): `gsutil rm
    gs://$GCS_BUCKET/config/<file>` — next reader call self-seeds from
    the freshly-shipped in-package JSON. Validated on 2026-04-19 for
    `dimensions.json`.
  - **`ensure_universal_configs_seeded`-seeded** (`field_archetypes`,
    `heartbeat`, `ranking_presets`): `gsutil cp
    backend/app/defaults/<file> gs://$GCS_BUCKET/config/<file>` timed
    just after the Cloud Run revision swap (so the new container
    serves the freshly-aligned config immediately; the old
    container's remaining seconds see `Unknown source '<oldname>'`
    errors that `_run_source` swallows).
  `_compose_data_gaps_context` will surface the mismatch as
  `missing_data` entries until the config is rewritten.
- **Cloud Run's HTTP/1.1 request body ceiling is 32 MB — not configurable.** Every POST that transits Cloud Run (folder upload, zip upload, `/ingest/resources`) rejects bodies larger than 32 MB with HTTP 413. Firebase Hosting wraps the 413 as HTTP 500, which is what the browser sees. The fix is structural: don't route large bytes through Cloud Run. Files + Folder upload modes now use a **signed-URL flow** — `POST /workspace/upload-init` issues a GCS v4 signed PUT URL, the browser PUTs bytes directly to `storage.googleapis.com`, then `POST /workspace/upload-commit` registers the `WorkspaceNode` via `workspace_service.register_uploaded_blob`. See `backend/app/services/storage.py::GcsSignedUrlAdapter`. Deploy-time prerequisites that all bit on first rollout:
  1. **IAM Token Creator self-binding** on the compute SA: `gcloud iam service-accounts add-iam-policy-binding $SA --member=serviceAccount:$SA --role=roles/iam.serviceAccountTokenCreator --project=$PROJECT_ID`. Cloud Run's metadata-issued credentials have no private key — signing errors with `AttributeError: you need a private key to sign credentials` unless the adapter delegates to the IAM SignBlob API via `service_account_email` + `access_token`.
  2. **`GCS_OBJECT_PREFIX=entities/`** env var. The FUSE bucket mount lives at `/mnt/gcs` but `DATA_ROOT=/mnt/gcs/entities`, so signed URLs need the `entities/` prefix to target the same object the backend's `finalize_upload` reads via FUSE. Default in `config.py` is `"entities/"`; change only if `DATA_ROOT` layout changes.
  3. **`GCS_BUCKET` env var on the container** (not just the volume mount). `deploy_cloudrun.sh` passes it via `--set-env-vars`. Without it the adapter falls back to `LocalFilesystemAdapter` and every init returns `use_direct_upload=True`, silently disabling the signed-URL path.
  4. **Bucket CORS policy**: `gcloud storage buckets update gs://$GCS_BUCKET --cors-file=backend/gcs-cors.json`. Browsers preflight cross-origin PUTs; without CORS rules allowing the Firebase origin, Chrome blocks the actual PUT even though the signed URL is valid. Curl tests work without CORS because curl skips preflight — **always verify via browser**, not just curl.
  The full flow is a 3-step dance: init (small JSON, through Cloud Run), PUT (bytes, direct to GCS), commit (small JSON, through Cloud Run). Neither of the small calls transits >1 KB. Zip mode still uses the legacy `POST /workspace/upload-zip` because unpacking needs the bytes server-side; if you need >32 MB zips, add a parallel `upload-zip-commit` endpoint that reads from GCS via the FUSE mount.
- **Postgres timestamp columns reject tz-aware datetimes until migrated.**
  Every `Column(DateTime)` in `models.py` + `academic_models.py` was
  swapped to the `UtcDateTime` TypeDecorator (defined in
  `datetime_support.py`), which declares `DateTime(timezone=True)` at
  the dialect level and normalises aware-UTC on both the write and
  read paths. `utc_now()` now returns aware UTC. An existing Postgres
  DB predates the switch — its columns are still `TIMESTAMP WITHOUT
  TIME ZONE` and asyncpg raises
  `DataError: can't subtract offset-naive and offset-aware datetimes`
  on every insert until the lifespan migration
  (`_upgrade_timestamp_columns`, Postgres-only, gated on
  `settings.portfolio_is_sqlite` / `.academic_is_sqlite`) has rewritten
  each column with `ALTER COLUMN … TYPE TIMESTAMP WITH TIME ZONE USING
  col AT TIME ZONE 'UTC'`. Discovery is dynamic
  (`information_schema.columns`), so any new DateTime column picks up
  the migration on next boot without code changes. During the
  rolling deploy, the outgoing revision's naive writes will error for
  10-30 s against the new schema; acceptable for MVP traffic.

## 9. Graduation paths

- **Heartbeat → Cloud Scheduler + Cloud Run Job.** Split
  `academic/heartbeat.py` out so the FastAPI service is request-scoped.
  Lets you drop `--no-cpu-throttling` (cheaper) and raise `--max-instances`
  (horizontal scale).
- **Background jobs → Cloud Tasks.** Move `run_chat_agent_job` /
  `run_preset_agent_job` onto a Cloud Tasks queue with a Cloud Run Job
  worker. Survives recycles and scales beyond one instance.
- **Blobs: FUSE → GCS SDK.** Rewrite `services/storage.py`
  `LocalFilesystemAdapter` as `GcsStorageAdapter` using
  `google-cloud-storage`. Drops the Preview FUSE dependency; better perf
  for high-fanout reads.
- **Cloud SQL tier upgrade.** db-f1-micro is fine for ≤20 concurrent
  connections. Step up to `db-custom-2-4096` (2 vCPU, 4 GB) when
  connection pressure or write latency climbs.

## 10. Local dev stays on SQLite

None of the code changes for Postgres break the local-dev SQLite path:

- `aiosqlite` is still in `requirements.txt`
- Default `DATABASE_URL` / `ACADEMIC_DATABASE_URL` in `config.py` still
  point at local `.db` files
- `settings.portfolio_is_sqlite` / `.academic_is_sqlite` gate the SQLite-
  specific bits (WAL PRAGMA, `check_same_thread` connect_arg)
- Column-add migrations in `app/main.py` lifespan use SQLAlchemy's
  `inspect()` which works on both. The Postgres-only `_FK_CASCADE_SPEC`
  block (re-issues FK constraints with `ON DELETE CASCADE`) is gated
  behind `if not settings.portfolio_is_sqlite` so local runs skip it.

So `python backend/run.py` still "just works" without Postgres installed.

## Reference

- [Cloud Run volume mounts (Cloud Storage)](https://cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts)
- [Cloud Run Cloud SQL connection (unix socket)](https://cloud.google.com/sql/docs/postgres/connect-run)
- [Cloud Run secrets](https://cloud.google.com/run/docs/configuring/services/secrets)
- [Cloud Run CPU always-allocated](https://cloud.google.com/run/docs/configuring/billing-settings)
- [Cloud SQL Postgres pricing](https://cloud.google.com/sql/pricing)
- [Firebase Hosting → Cloud Run rewrites](https://firebase.google.com/docs/hosting/cloud-run)
- [Firebase Hosting full config](https://firebase.google.com/docs/hosting/full-config)
- [asyncpg](https://magicstack.github.io/asyncpg/current/)
- [psycopg v3 (sync Postgres driver)](https://www.psycopg.org/psycopg3/)
