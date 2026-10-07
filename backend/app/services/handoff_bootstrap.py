"""Carrying an authenticated identity across the one page load between POST / and GET /.

THE PROBLEM THIS SOLVES. The browser arrives at the Operations Dashboard root with a one-time
code from BL-DCMS in a cross-origin form POST. The code can be redeemed immediately, but the
resulting token cannot be handed to the SPA yet - the SPA does not exist until the browser has
followed a redirect and loaded GET /. Putting the token in the redirect, the URL, the fragment
or the HTML would expose it exactly where this design is trying not to.

So the POST leaves behind a BOOTSTRAP: an opaque random id in an HttpOnly, SameSite cookie,
which the freshly-loaded SPA exchanges for its normal token over a same-origin call. The cookie
carries the id and nothing else - no employee, no role, no token - and the id is meaningless
without the record held here.

WHERE THE STATE LIVES. In this process's memory, deliberately, and this is the one thing to know
before changing the deployment: it works because the Operations Dashboard backend runs as a
SINGLE uvicorn process (see deploy/systemd - no --workers). The state exists for about one
second, between the redirect and the SPA's first call, so a restart in that window simply lands
the user on the sign-in page rather than losing anything. If the service is ever given multiple
workers, a bootstrap created by one worker would be invisible to another and roughly (n-1)/n of
handoffs would fail; at that point this store moves to a table, which is a small change because
everything below is behind these four functions. `warn_if_multi_process()` shouts at startup if
the usual worker environment variables suggest that has happened.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Long enough for a redirect plus an SPA boot on a slow tablet; short enough that a stolen
#: cookie is worthless almost immediately.
BOOTSTRAP_TTL_SECONDS = 120

_CODE_BYTES = 32


@dataclass(frozen=True)
class Bootstrap:
    """What the SPA gets back when it presents a valid bootstrap id."""

    employee_id: str
    access_token: str
    expires_at: datetime


# Guarded because uvicorn serves requests from a thread pool even in one process.
_lock = threading.Lock()
_store: dict[str, Bootstrap] = {}


def create(employee_id: str, access_token: str) -> str:
    """Stash an already-issued token and return the opaque id that stands for it.

    The caller has ALREADY authenticated and authorized this user; this function decides
    nothing. It exists purely to bridge one page load.
    """
    _purge_expired()
    bootstrap_id = secrets.token_urlsafe(_CODE_BYTES)
    with _lock:
        _store[bootstrap_id] = Bootstrap(
            employee_id=employee_id,
            access_token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=BOOTSTRAP_TTL_SECONDS),
        )
    logger.info("Handoff bootstrap created for employee_id=%s", employee_id)
    return bootstrap_id


def consume(bootstrap_id: str | None) -> Bootstrap | None:
    """Exchange the id for its token, exactly once.

    SINGLE USE is enforced by removing the record under the lock before it is inspected, so two
    concurrent finalizes cannot both come away with a token. An expired record is discarded
    rather than returned. Returns None for unknown, spent and expired alike - the caller cannot
    tell them apart, and neither can anything probing it.
    """
    if not bootstrap_id:
        return None
    with _lock:
        bootstrap = _store.pop(bootstrap_id, None)
    if bootstrap is None:
        logger.warning("Handoff bootstrap refused (unknown or already used)")
        return None
    if bootstrap.expires_at <= datetime.now(timezone.utc):
        logger.warning("Handoff bootstrap refused (expired) for employee_id=%s", bootstrap.employee_id)
        return None
    logger.info("Handoff bootstrap finalized for employee_id=%s", bootstrap.employee_id)
    return bootstrap


def _purge_expired() -> None:
    now = datetime.now(timezone.utc)
    with _lock:
        for key in [k for k, v in _store.items() if v.expires_at <= now]:
            _store.pop(key, None)


def reset_for_tests() -> None:
    with _lock:
        _store.clear()


def warn_if_multi_process() -> None:
    """Called at startup. In-memory bootstrap state is per-process; say so loudly if the usual
    worker settings suggest there is more than one."""
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = os.getenv(name)
        if raw and raw.strip().isdigit() and int(raw) > 1:
            logger.error(
                "%s=%s: BL-DCMS handoff bootstrap state is per-process and will fail "
                "intermittently with more than one worker - move it to a table first.",
                name, raw,
            )
