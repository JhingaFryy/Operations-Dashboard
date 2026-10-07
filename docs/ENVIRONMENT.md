# Environment variables

All values below are **placeholders**. Never commit a real credential to this repository.

## How configuration is loaded

`backend/app/core/config.py` defines a pydantic-settings model:

```python
model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

Consequences worth knowing:

- Values are read from `backend/.env`, or from the process environment (which is how the
  systemd unit supplies them in production).
- `extra="ignore"` means **an unrecognised key is silently discarded.** A typo does not
  raise an error; it surfaces later as "required variable missing" or as a default being
  used where you expected your value.
- Variables marked **required** have no default. The application fails to start without them.

One variable is read directly via `os.environ` rather than through this model —
`ADMIN_DELETION_STORAGE_ROOTS` — and is noted as such below.

## Core

| Variable | Required | Purpose | Example placeholder |
| --- | --- | --- | --- |
| `RDCMS_DATABASE_URL` | **Yes** | PostgreSQL connection for the shared `rdcms` database. Must use the `postgresql+psycopg://` scheme — the project uses psycopg 3, and a bare `postgresql://` URL makes SQLAlchemy look for psycopg2, which is not installed. | `postgresql+psycopg://DB_USER:CHANGE_ME@localhost:5432/rdcms` |
| `JWT_SECRET_KEY` | **Yes** | Secret used to sign and verify access tokens. Generate a fresh value per environment with `python3 -c "import secrets; print(secrets.token_hex(32))"`. Rotating it invalidates every issued token. | `CHANGE_ME_GENERATE_A_64_CHAR_HEX_SECRET` |
| `JWT_ALGORITHM` | No (`HS256`) | Token signing algorithm. | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No (`480`) | Access token lifetime in minutes. | `480` |

## Loco Master integration

Loco Master supplies the equipment hierarchy used for booking auto-mapping.

| Variable | Required | Purpose | Example placeholder |
| --- | --- | --- | --- |
| `LOCO_MASTER_BASE_URL` | **Yes** | Base URL of the Loco Master service. | `http://localhost:8100` |
| `LOCO_MASTER_TIMEOUT_SECONDS` | No (`10.0`) | HTTP timeout for Loco Master calls. | `10` |
| `LOCO_MASTER_INTERNAL_API_KEY` | No (`None`) | Shared key for service-to-service calls, if the deployment requires one. | *(leave empty, or set per environment)* |

## BL-DCMS integration

| Variable | Required | Purpose | Example placeholder |
| --- | --- | --- | --- |
| `BLDCMS_BASE_URL` | No (`None`) | Base URL of the BL-DCMS checksheet system. | `http://localhost:8080` |
| `BLDCMS_TIMEOUT_SECONDS` | No (`10.0`) | HTTP timeout for BL-DCMS calls. | `10` |
| `BLDCMS_INTERNAL_API_KEY` | No (`None`) | Shared key authenticating BL-DCMS → Operations Dashboard calls. | *(leave empty, or set per environment)* |
| `BLDCMS_BROWSER_ORIGINS` | No (`""`) | Comma-separated browser origins involved in the BL-DCMS → Operations Dashboard user handoff. **Not currently present in `.env.example`.** | `http://localhost:5173` |
| `OPERATIONS_INTERNAL_API_KEY` | No (`None`) | This service's own internal API key, used to authenticate inbound service-to-service requests. | *(leave empty, or set per environment)* |

## Behaviour

| Variable | Required | Purpose | Example placeholder |
| --- | --- | --- | --- |
| `LOCO_MOVEMENT_SECTION_CODES` | No (`SHIFT`) | Comma-separated section codes permitted to perform locomotive movement (Shed In / Shed Out). This is a **privilege grant** — every code listed here gains movement capability. Planning sections such as PPIO must **not** appear. **Not currently present in `.env.example`.** | `SHIFT` |
| `ADMIN_DELETION_STORAGE_ROOTS` | No (`""`) | Filesystem roots the admin deletion feature is allowed to destroy files under. Read directly from `os.environ` in `app/services/admin_deletion_service.py`, not via the settings model. **Not currently present in `.env.example`.** | `/path/to/storage/root` |

## Gaps between `.env.example` and the code

`backend/.env.example` documents 11 variables. Three more are read by the code and are not in
the template: `BLDCMS_BROWSER_ORIGINS`, `LOCO_MOVEMENT_SECTION_CODES`, and
`ADMIN_DELETION_STORAGE_ROOTS`. All three have working defaults, so the application starts
without them — but their absence from the template means a new deployment will not know they
exist. Consider adding them with placeholder values.

## Security notes

- **`backend/.env` is excluded by `.gitignore`.** Keep it that way, and verify with
  `git check-ignore -v backend/.env` after any change to the ignore rules.
- **Production secrets belong outside the repository.** Use a systemd `EnvironmentFile`
  pointing at a root-owned file with mode `600` — see
  [DEPLOYMENT.md](DEPLOYMENT.md#4-systemd).
- **Do not put secrets in inline `Environment=` directives** in a systemd unit. Any local
  user can read them with `systemctl show` or `systemctl cat`.
- **Never hardcode a `DATABASE_URL` fallback in source.** A default like
  `os.getenv("DATABASE_URL", "postgresql://DB_USER:CHANGE_ME@HOST/rdcms")` puts a live
  credential in version control permanently, and it keeps working silently when the
  environment variable is missing — so the misconfiguration is never noticed. The correct
  behaviour is to fail fast, which is what `RDCMS_DATABASE_URL` being a required field
  already does here.
- **Never commit JWT secrets or internal API keys**, including in tests, fixtures, example
  files, documentation, or shell scripts.
- If any secret is exposed, **rotate it.** Removing it from a file does not un-expose it, and
  removing it from a Git commit does not remove it from history.
