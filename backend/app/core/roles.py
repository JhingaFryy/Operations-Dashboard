"""The canonical role vocabulary, and the ONE place a stored role string is interpreted.

WHY THIS EXISTS. `users.role` is a free VARCHAR(20) in the shared rdcms database, written by
BL-DCMS and by administrative tooling, with no CHECK constraint and no normalization anywhere.
Every authorization decision in this application used to compare it with exact, case-sensitive
equality:

    if user.role == "Technician": deny
    if user.role == "Supervisor": ...apply the Supervisor rules...

A row spelled "supervisor" or "SUPERVISOR" therefore matched NEITHER branch. It was not denied as
a Technician and it was not subjected to the Supervisor rules - it fell through both and was
treated as an unrecognised-but-acceptable account. That is fail-OPEN: the least-recognised account
got the fewest checks, which is exactly backwards.

THE RULE HERE IS THE OPPOSITE. A role string is canonicalized case-insensitively (and with
surrounding whitespace stripped) onto exactly one of Admin / Supervisor / Technician. Anything
else - an empty string, NULL, a typo, a role this application has never heard of - resolves to
None, and every caller treats None as "deny". An unrecognised role can never again mean "fewer
checks applied".

Canonicalization is recognition, not repair: nothing here writes to the database, and a badly
spelled row stays badly spelled until someone fixes the data. See
diagnostics/supervisor_login_audit.sql section 5 for how to find them.
"""

from sqlalchemy.orm.attributes import set_committed_value

TECHNICIAN_ROLE = "Technician"
ADMIN_ROLE = "Admin"
SUPERVISOR_ROLE = "Supervisor"

#: Every role this application recognises. Anything outside it is denied, never assumed benign.
CANONICAL_ROLES = (ADMIN_ROLE, SUPERVISOR_ROLE, TECHNICIAN_ROLE)

_BY_LOWER = {role.lower(): role for role in CANONICAL_ROLES}


def normalize_role(raw: str | None) -> str | None:
    """The canonical spelling of `raw`, or None if this application does not recognise it.

    None is the deny answer. Callers must not fall through on it.
    """
    if not raw:
        return None
    return _BY_LOWER.get(raw.strip().lower())


def canonical_role(user) -> str | None:
    """The user's canonical role, normalizing the in-memory instance as a side effect.

    WHY IT WRITES BACK, AND WHY THAT IS SAFE. Two dozen `.role ==` comparisons live in services
    downstream of authorization (section scoping, Admin bypasses, display). Normalizing only at
    the gate would admit a correctly-recognised "supervisor" and then have every one of those
    comparisons silently disagree about what they were looking at.

    `set_committed_value` writes the attribute WITHOUT marking the instance dirty, so this can
    never turn into an UPDATE when some later part of the request commits its own work. The
    database row is untouched; only this request's view of it is consistent.
    """
    canonical = normalize_role(getattr(user, "role", None))
    if canonical is not None and user.role != canonical:
        set_committed_value(user, "role", canonical)
    return canonical
