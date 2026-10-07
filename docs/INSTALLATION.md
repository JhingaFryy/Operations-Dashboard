# Installation (development)

Local setup for the Operations Dashboard. For production, see [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Prerequisites

| Requirement | Version used on the reference machine | Notes |
| --- | --- | --- |
| Linux | Ubuntu | Any modern distribution should work |
| Python | 3.14.4 | The project uses `X \| None` type syntax and modern typing; 3.11+ is a safe floor. **Verify in your environment.** |
| Node.js | 22.22.1 | Vite 8 requires a current LTS |
| npm | 9.2.0 | A `package-lock.json` is committed |
| PostgreSQL | 18.6 | Database name `rdcms` |
| Git | any | |

There is no `pyproject.toml` or `setup.cfg`; dependencies live in
`backend/requirements.txt` and the backend is run from the `backend/` directory rather than
installed as a package.

## 2. Clone

```bash
git clone <repo-url>
cd operations-dashboard
```

## 3. Backend setup

```bash
cd backend
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

Create your environment file from the template:

```bash
cp .env.example .env
```

Then edit `backend/.env` and set, at minimum, the three variables that have **no default**
and will stop the application from starting if missing:

- `RDCMS_DATABASE_URL`
- `LOCO_MASTER_BASE_URL`
- `JWT_SECRET_KEY`

```
RDCMS_DATABASE_URL=postgresql+psycopg://DB_USER:CHANGE_ME@localhost:5432/rdcms
```

Generate your own signing secret — never reuse one from another environment:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Every variable is documented in [ENVIRONMENT.md](ENVIRONMENT.md). Settings are loaded by
`app/core/config.py` from `backend/.env` with `extra="ignore"`, so unknown keys in the file
are silently skipped — a typo in a variable name fails quietly rather than loudly.

### Database and migrations

The application **never** creates tables automatically. `Base.metadata.create_all()` is
deliberately not used against this database, so the schema must exist before the backend is
useful.

Migrations are plain SQL files in `backend/migrations/`, numbered `001` through `015`,
applied **in order**. There is **no migration tracking table** and no migration runner —
which means nothing will stop you from applying the same migration twice. Keep your own
record of what has been applied.

Migrations `004` and later wrap themselves in an explicit transaction, and several
(`007`, `013`, `014`, `015`) contain `DO $$` verification blocks that abort the transaction
if their preconditions are not met. Migrations `001`–`003` predate that convention and have
accompanying Python helpers:

```bash
# migrations 001-003 only
venv/bin/python scripts/apply_migration_001.py

# migrations 004 onwards
psql "$RDCMS_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/004_booking_client_booking_id_idempotency.sql
```

Read each migration's header comment before applying it. They document intent and
preconditions, and some are not safely re-runnable.

### Run the dev server

```bash
cd backend
venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8321
```

Interactive API docs are then at `http://127.0.0.1:8321/docs`. Note that production nginx
deliberately does **not** expose `/docs`, `/redoc` or `/openapi.json`.

Health checks:

```bash
curl http://127.0.0.1:8321/health
curl http://127.0.0.1:8321/health/dependencies
```

`/health` is a static liveness probe. `/health/dependencies` actually tries the database and
Loco Master and reports each as `ok` or `unreachable`.

## 4. Frontend setup

```bash
cd frontend
npm ci          # use npm ci - package-lock.json is committed
npm run dev
```

The dev server proxies `/api` to `http://127.0.0.1:8321`. If your backend runs elsewhere,
override the target rather than editing the config:

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:9000 npm run dev
```

Production build:

```bash
npm run build          # runs: tsc -b && vite build
```

The build writes to `frontend/dist`, **which is also the directory nginx serves in
production.** On the production host, building *is* deploying. When you only want to verify
that a build succeeds, send the output elsewhere:

```bash
npx vite build --outDir /tmp/od-build-check
```

## 5. Tests

Backend (`pytest.ini` sets `testpaths = tests` and `pythonpath = .`, so run it from
`backend/`):

```bash
cd backend
venv/bin/python -m pytest
```

Frontend:

```bash
cd frontend
npm test            # vitest run, jsdom + MSW, setup in src/test/setup.ts
npm run lint        # oxlint
npx tsc -b --force  # type check
```

**Use `tsc -b`, not `tsc --noEmit`.** The root `frontend/tsconfig.json` is
references-only (`"files": []`), so `tsc --noEmit` type-checks nothing and exits 0 no matter
how broken the code is. Only the build (`-b`) walks the project references.

## 6. Troubleshooting

**Database connection fails.** Confirm PostgreSQL is running, the `rdcms` database exists,
and your role can connect. Check with `psql "$RDCMS_DATABASE_URL" -c 'SELECT 1'`. Note the
URL scheme must be `postgresql+psycopg://` — the project uses psycopg 3, and a bare
`postgresql://` URL will make SQLAlchemy reach for psycopg2, which is not installed.

**Application refuses to start with a validation error.** `RDCMS_DATABASE_URL`,
`LOCO_MASTER_BASE_URL` and `JWT_SECRET_KEY` are required with no defaults. Because
`extra="ignore"` is set, a misspelled key is ignored silently — so the symptom of a typo is
"required variable missing", not "unknown variable".

**Port already in use.** Find the listener with `ss -ltnp | grep 8321`. In development, pass
a different `--port`; do not change the production port without also updating nginx's
`proxy_pass`.

**Frontend shows no data / requests 404.** In development, confirm the Vite proxy target
matches your backend port. In production, confirm nginx's `location /api/` block is present —
the SPA fallback (`try_files $uri $uri/ /index.html`) will otherwise answer API calls with
the HTML shell, and a `200 text/html` response can be mistaken by client code for an empty
successful result rather than an error.

**Loco Master reports `unreachable`.** Equipment lookups and booking auto-mapping depend on
it. Check `LOCO_MASTER_BASE_URL` and that the service is listening.
