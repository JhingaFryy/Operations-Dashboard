# Deployment (production)

How the Operations Dashboard is deployed. Read [INSTALLATION.md](INSTALLATION.md) first for
the component versions and test commands.

> Nothing in this document should be run casually. The database is shared with BL-DCMS, and
> `frontend/dist` is served live by nginx — building the frontend on the production host
> deploys it immediately, with no separate publish step.

## 1. Production assumptions

- A Linux server hosting both this application and BL-DCMS.
- PostgreSQL, with the `rdcms` database shared between the two applications.
- The backend runs under systemd as `operations-dashboard-backend.service`, listening on
  `127.0.0.1:8321`. **The unit file is not in this repository** — it lives in the system's
  systemd directory. Treat its exact contents as "verify in your environment".
- nginx serves `/opt/operations-dashboard/frontend/dist` and proxies `/api/` to the backend.
  The backend is never exposed directly.
- Repository checked out at `/opt/operations-dashboard`.

## 2. Safe deployment order

The order matters. Migrations before the restart, the restart before the frontend build.

1. **Pull the latest code.**
2. **Back up the database** before anything that changes schema or data.
3. **Install backend dependencies** if `requirements.txt` changed.
4. **Apply migrations**, one at a time, reviewing each result.
5. **Restart the backend.**
6. **Build the frontend.** This is the moment the change becomes visible to users.
7. **Reload nginx only if a config file changed.** A code deploy does not need it.
8. **Health check and verify.**

```bash
cd /opt/operations-dashboard
git pull --ff-only

# 2. Backup first - adjust the destination to your backup location.
pg_dump "$RDCMS_DATABASE_URL" -Fc -f /path/to/backups/rdcms_pre_deploy_$(date +%Y%m%d_%H%M%S).dump

# 3. Dependencies, only if requirements.txt changed
cd /opt/operations-dashboard/backend
venv/bin/pip install -r requirements.txt

# 4. Migrations - read section 3 (Migration notes) before running anything here

# 5. Restart the backend
sudo systemctl restart operations-dashboard-backend.service
sudo systemctl status operations-dashboard-backend.service --no-pager

# 6. Build the frontend (this publishes it)
cd /opt/operations-dashboard/frontend
npm ci
npm run build

# 7. Only if deploy/nginx/* changed
sudo nginx -t && sudo systemctl reload nginx

# 8. Health check
curl -fsS http://127.0.0.1:8321/health
curl -fsS http://127.0.0.1:8321/health/dependencies
```

Run the test suites before deploying, not after — see [INSTALLATION.md](INSTALLATION.md#5-tests).

## 3. Migration notes

- **Do not apply migrations blindly.** Read each file's header comment first; they document
  preconditions and rationale, and not all are safely re-runnable.
- There is **no migration tracking table**. Nothing prevents a double-apply. Keep an external
  record of which numbers have been applied in production.
- Migrations `004` and later open their own transaction. Several (`007`, `013`, `014`, `015`)
  include `DO $$` pre-flight blocks that deliberately abort if the database is not in the
  expected state — an abort is the migration doing its job, not a bug to work around.
- Always use `-v ON_ERROR_STOP=1` so a failure stops the script instead of continuing.
- **Use an admin database role for DDL.** The application's own role may lack `ALTER TABLE`
  privileges; schema changes should be applied by a role that owns the tables, not by the
  application user.
- **Migration `015`** adds `dashboard_access.can_route_bookings` — the per-user Operations
  Dashboard routing capability. It is forward-only, additive, writes no data, and defaults
  every existing row to `false`. It is a prerequisite for the PPIO workflow; see
  [PPIO_WORKFLOW.md](PPIO_WORKFLOW.md).

```bash
cd /opt/operations-dashboard/backend
psql "$RDCMS_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/015_dashboard_access_can_route_bookings.sql
```

**Verify the result** rather than trusting the exit code — for example, confirm the new
column exists and its default is what you expect, using a read-only query. The queries in
`backend/diagnostics/` are read-only by design and are the right place to add verification
SQL.

## 4. systemd

```bash
sudo systemctl status operations-dashboard-backend.service --no-pager
sudo systemctl restart operations-dashboard-backend.service
sudo journalctl -u operations-dashboard-backend.service -n 100 --no-pager
```

The unit is expected to run Uvicorn against `app.main:app` from
`/opt/operations-dashboard/backend`, bound to `127.0.0.1:8321`. Confirm the actual
`ExecStart`, `User` and `WorkingDirectory` on your host.

**Do not put secrets in the unit file.** Inline `Environment=` directives are world-readable
via `systemctl show` and `systemctl cat` to any local user. Prefer an `EnvironmentFile`:

```ini
[Service]
EnvironmentFile=/etc/operations-dashboard/backend.env
```

```bash
sudo install -o root -g root -m 600 /dev/null /etc/operations-dashboard/backend.env
# then populate it; see docs/ENVIRONMENT.md for the variable list
```

If secrets were previously inline in the unit file, treat them as exposed and rotate them
when you migrate to an `EnvironmentFile`.

## 5. nginx

The repository ships the real site configuration:

- `deploy/nginx/operations-dashboard.conf` — the server blocks (`listen`/`server_name` only)
- `deploy/nginx/operations-common.conf` — the shared body: document root, security headers,
  the `/api/` proxy, and the SPA fallback

Both server blocks include the same shared snippet precisely so that they cannot drift apart.
Install as documented in the file's own header comment:

```bash
sudo mkdir -p /etc/nginx/operations-dashboard
sudo cp deploy/nginx/operations-common.conf /etc/nginx/operations-dashboard/
sudo cp deploy/nginx/operations-dashboard.conf /etc/nginx/sites-available/
sudo nginx -t && sudo systemctl reload nginx
```

The site-specific hostname and rollback listener are recorded in that file; adjust
`server_name` for your environment. Points worth knowing before you edit it:

- `location /api/` must stay **above** the `location /` SPA fallback. If an API prefix is not
  matched by the proxy block, the fallback serves `index.html` with a `200` status instead,
  which client code can misread as a successful empty response.
- Hashed assets under `/assets/` are cached immutably; `index.html` is `no-store`, so a deploy
  is picked up immediately instead of leaving browsers on the old bundle.
- `/mockServiceWorker.js` returns 404 on purpose. MSW's worker ships from `frontend/public/`
  and is therefore always copied into `dist`, but it has no business being reachable in
  production.
- This vhost is deliberately **not** `default_server`.

## 6. Post-deploy verification

Work through this list after every deploy:

1. `curl -fsS http://127.0.0.1:8321/health` returns `{"status":"ok"}`.
2. `curl -fsS http://127.0.0.1:8321/health/dependencies` reports `rdcms: ok` and
   `loco_master: ok`.
3. **Login** succeeds in a browser, and a hard refresh still loads the SPA.
4. **PPIO permissions** — sign in as a PPIO Supervisor and confirm the sidebar shows
   Overview, Loco Workflow, Shed Visits and Global Booking Pool, and no Section Dashboard.
5. **Global Booking Pool** loads and bookings are grouped by source.
6. **Add Sections** on an existing booking adds the chosen section and leaves existing
   assignments untouched.
7. **Create Planning Booking** creates a booking that appears in the pool with the
   "Added by PPIO" badge.
8. Confirm movement controls (Shed In / Shed Out) are **absent** for the PPIO user and that
   calling the endpoint directly is refused.

## 7. Rollback

**Code.** Tag before deploying so there is something to return to:

```bash
git tag -a deploy-$(date +%Y%m%d) -m "pre-deploy"
git log --oneline -10
git checkout <previous-commit-or-tag>
sudo systemctl restart operations-dashboard-backend.service
cd /opt/operations-dashboard/frontend && npm ci && npm run build
```

The frontend must be rebuilt after a code rollback — `dist` is a build artifact and is not
tracked in Git, so checking out an older commit does not change what nginx serves.

**Database.** Restore the pre-deploy dump:

```bash
pg_restore --clean --if-exists -d "$RDCMS_DATABASE_URL" /path/to/backups/<dump-file>
```

Remember that `rdcms` is shared with BL-DCMS. A restore rolls back **both** applications'
data to the dump's point in time, not just this one. Confirm the impact before restoring, and
stop both backends first.

Migrations are forward-only and have no down scripts. Reversing a schema change means writing
a new, reviewed migration — not editing an applied one.
