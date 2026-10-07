"""In-memory rate limiting for destructive and authentication-sensitive endpoints.

WHY THIS EXISTS NOW. Until Admin deletion, nothing in this application needed throttling: every
endpoint was either read-only or a bounded state transition on a booking. Password
re-authentication changes that. A re-auth endpoint that accepts unlimited attempts is an oracle for
an Admin's password, and the deletion endpoints are the single most damaging thing an attacker who
guessed one could reach. The audit of this backend found no limiter of any kind - no slowapi, no
429 anywhere - so this is new rather than a wrapper over something existing.

HAND-ROLLED, DELIBERATELY. BL-DCMS solves the same problem the same way in
/home/elsbl/Checksheet/backend/app/core/rate_limit.py, and this mirrors it so the two services
behave alike and neither needs a shared store. The reasoning carries over: this app runs as a
single uvicorn process on 127.0.0.1:8321 with no --workers, so one per-process counter sees every
request.

THE LIMIT IS PER ACCOUNT, NOT PER IP. A workshop shares one office network, so an IP-keyed budget
would let one person's mistyped password lock out everybody else's. Keying on the authenticated
employee id also means an attacker cannot escape their own budget by changing source address -
they already had to present a valid token to reach these endpoints at all.

FAILURES COUNT, SUCCESSES DO NOT. An Admin who deletes six visits correctly in five minutes is
working; six wrong passwords in five minutes is not. Callers therefore record a failure explicitly
(see record_failure) rather than every call being metered.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, status

logger = logging.getLogger("app.security")

#: Buckets are pruned opportunistically rather than on a timer - there is no scheduler here, and a
#: workshop's handful of Admins will never accumulate enough keys to matter.
_PRUNE_EVERY_N_CHECKS = 200


class RateLimiter:
    """A fixed-window-per-key counter. Not a token bucket: the simpler model is easier to reason
    about when the consequence of getting it wrong is locking an Admin out of their own system."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._check_count = 0

    def _bucket_key(self, action: str, key: str) -> str:
        # Namespaced by action. Without this, a failed deletion re-auth and a failed login would
        # share one budget for the same person, so one could exhaust the other.
        return f"{action}:{key}"

    def check(self, *, key: str, action: str, max_attempts: int, window_seconds: int) -> None:
        """Raise 429 if `key` has already recorded `max_attempts` failures inside the window.

        Called BEFORE the expensive or dangerous work, so a blocked caller never reaches a password
        comparison at all.
        """
        now = time.monotonic()
        bucket_key = self._bucket_key(action, key)

        with self._lock:
            self._check_count += 1
            if self._check_count % _PRUNE_EVERY_N_CHECKS == 0:
                self._prune_locked(now, window_seconds)

            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                return
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) < max_attempts:
                return
            retry_after = max(1, int(window_seconds - (now - bucket[0])))

        # Logged outside the lock. Every violation is its own line in the security log, so repeated
        # attempts are greppable as a pattern rather than needing a separate alerting path.
        logger.warning(
            "Rate limit exceeded",
            extra={
                "action": action,
                "success": False,
                "rate_limit_key": key,
                "max_attempts": max_attempts,
                "window_seconds": window_seconds,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "TOO_MANY_ATTEMPTS",
                "message": (
                    f"Too many failed attempts. Try again in about {retry_after} second(s)."
                ),
            },
            headers={"Retry-After": str(retry_after)},
        )

    def record_failure(self, *, key: str, action: str) -> None:
        """Count one failure against `key`. Only failures are recorded - see the module docstring."""
        now = time.monotonic()
        bucket_key = self._bucket_key(action, key)
        with self._lock:
            self._buckets.setdefault(bucket_key, deque()).append(now)

    def reset(self, *, key: str, action: str) -> None:
        """Clear a key's failures. Called after a SUCCESSFUL re-authentication, so an Admin who
        mistypes twice and then gets it right starts clean rather than carrying the near-miss."""
        with self._lock:
            self._buckets.pop(self._bucket_key(action, key), None)

    def _prune_locked(self, now: float, window_seconds: int) -> None:
        for bucket_key in list(self._buckets):
            bucket = self._buckets[bucket_key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                del self._buckets[bucket_key]

    def reset_for_tests(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._check_count = 0


#: One process-wide limiter, like BL's. Module-level state is correct here: one uvicorn process has
#: one rate-limit story, and a per-request limiter would count nothing.
limiter = RateLimiter()

# --------------------------------------------------------------------------------- policies ----
# Named so a reader sees the policy rather than two bare integers at the call site.

#: Destructive re-authentication. Five wrong passwords in fifteen minutes is far more than any
#: honest typo sequence and far less than useful for guessing a bcrypt-hashed password.
REAUTH_ACTION = "ADMIN_DESTRUCTIVE_REAUTH"
REAUTH_MAX_ATTEMPTS = 5
REAUTH_WINDOW_SECONDS = 15 * 60
