"""Logging levels for the Operations Dashboard API.

This service logs to stdout only; systemd captures it into the journal (StandardOutput=journal),
so there is no application-owned log file and no application-owned rotation - journald's own
retention is the single owner. See deploy/journald/ for the proposed cap.

What this module pins, and why:

  * the application at INFO - request/ workflow lines are the operational record;
  * SQLAlchemy at WARNING. SQLAlchemy's own default for the "sqlalchemy" logger is already WARNING,
    but that default only applies while nothing sets a level on it: `logging.basicConfig(INFO)` on
    the root logger does not change it, whereas any future `echo=True`, `SQLALCHEMY_ECHO`, or a
    dictConfig entry would. Pinning it here means a routine SELECT/INSERT/UPDATE can never become
    an INFO line in the journal by accident - that is what grew BL-DCMS's logs by ~6 GB in two
    days. Genuine engine problems (pool exhaustion, invalidated connections) are WARNING or worse
    and still appear.
  * uvicorn access logging stays at INFO (one line per request, the journal's rate limiting
    applies); errors and tracebacks are untouched at every level.

SQL_ECHO_LEVEL=INFO (or DEBUG) opts a development box back into statement logging.
"""

import logging
import os

SQLALCHEMY_LOGGERS = ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.pool", "sqlalchemy.dialects", "sqlalchemy.orm")


def sqlalchemy_level() -> int:
    return getattr(logging, os.getenv("SQL_ECHO_LEVEL", "WARNING").upper(), logging.WARNING)


def configure_logging(app_level: int | None = None) -> None:
    """Idempotent: safe to call at import time and again from a startup hook."""
    level = app_level if app_level is not None else getattr(
        logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    logging.basicConfig(level=level)
    logging.getLogger().setLevel(level)

    sql_level = sqlalchemy_level()
    for name in SQLALCHEMY_LOGGERS:
        logging.getLogger(name).setLevel(sql_level)
