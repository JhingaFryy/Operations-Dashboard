# Operations Dashboard

A FastAPI + React operations dashboard for locomotive shed workflow, booking pool
management, shed visits, and PPIO planning.

It runs against the shared `rdcms` PostgreSQL database and is deployed alongside
BL-DCMS at Electric Loco Shed, Valsad.

## Key features

- **Shed visit tracking** — arrival, schedule lifecycle, and departure of locomotives
- **Loco workflow overview** — per-visit checksheet work packages and requirements
- **Global Booking Pool** — cross-section view of every booking, grouped by booking source
- **Additive multi-section booking routing** — sections are added to a booking, never
  removed or replaced
- **PPIO planning workflow** — a planning section that routes work without performing it
- **Planning booking creation** — planners raise bookings directly
- **"Added by PPIO" provenance** — the pool shows which planning section introduced a booking
- **Read-only planning access** for movement and workflow pages
- **Section-scoped booking workflows** — supervisors act only on their own section's work
- **PostgreSQL** backend storage
- **React** single-page frontend

## Architecture

| Layer | Technology |
| --- | --- |
| Backend | FastAPI (`app.main:app`), served by Uvicorn on `127.0.0.1:8321` |
| Frontend | React 19 + Vite + TypeScript, compiled to `frontend/dist` |
| Database | PostgreSQL (database `rdcms`), accessed via SQLAlchemy 2 + psycopg 3 |
| Process management | systemd (`operations-dashboard-backend.service`) |
| Reverse proxy | nginx — serves `frontend/dist` and proxies `/api/` to the backend |
| Integration | Loco Master (equipment hierarchy) and BL-DCMS (checksheet system) over HTTP |

The backend is never exposed directly; nginx is the only public entry point. Because the
SPA calls relative `/api/...` paths, the deployment is same-origin by construction and no
CORS is configured on the backend.

## Repository layout

```
backend/
  app/
    api/         FastAPI routers (auth, bookings, shed_visits, workflow, internal, …)
    core/        Configuration, authorization (authz.py), security, rate limiting
    services/    Business logic (booking routing, creation, section dashboard, …)
    db/          SQLAlchemy models and session management
    schemas/     Pydantic request/response models
    clients/     HTTP clients for Loco Master and BL-DCMS
    domain/      Domain rules shared across services
    main.py      Application entry point and router registration
  migrations/    Forward-only numbered SQL migrations (001–015)
  scripts/       One-off operational and migration helper scripts
  diagnostics/   Read-only audit SQL queries (never mutate data)
  tests/         pytest suite
  requirements.txt
frontend/
  src/
    pages/       Route-level screens
    components/  Reusable UI, grouped by feature
    api/         Typed API client modules
    auth/        Authentication context and permission helpers
    lib/         Pure helper modules (grouping, routing rules, formatting)
    mocks/       MSW handlers used by the test suite
    theme/       Design tokens
  package.json
deploy/
  nginx/         Production nginx site and shared snippet
docs/            This documentation
```

`app/core/authz.py` is the single place authorization is decided. Section names and codes
are interpreted there and nowhere else — add new rules to that module rather than scattering
section-code checks through routes or components.

## Quick start for development

See [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Production deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Environment variables

See [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md).

## PPIO workflow

See [docs/PPIO_WORKFLOW.md](docs/PPIO_WORKFLOW.md).

## Security

- **Never commit `backend/.env`.** It is excluded by `.gitignore`; keep it that way.
- Use `backend/.env.example` as the template. It contains placeholders only.
- Keep production secrets outside Git — prefer a systemd `EnvironmentFile` pointing at a
  root-owned file with restrictive permissions.
- **Rotate credentials if they are ever leaked**, including the JWT signing secret, the
  database password, and any internal API keys. Rotating the JWT secret invalidates all
  issued tokens, so plan for users to sign in again.
- Never put tokens, codes, or passwords in URLs or query strings.
- A **private repository is recommended.** This codebase describes the internal workflow and
  network topology of a production system.

## License

No license has been selected yet. Treat this repository as private/internal.
