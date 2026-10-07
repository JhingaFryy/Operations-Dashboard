"""The ONE place Operations Dashboard authorization is decided.

Access policy (production):
    Operations Dashboard is used by two kinds of account, and only these two:

      ADMIN       - the technical Superadmin. Full operational access plus the Admin-only
                    functions. Authorised by ROLE ALONE.
      SUPERVISOR  - active. Base operational access by ROLE, scoped to their own section;
                    locomotive movement additionally requires a movement section.

    Technicians, inactive accounts and roles this application does not recognise are denied
    outright, the last of those fail-closed (see app/core/roles.py).

    BASE ACCESS IS NOT AN ENTITLEMENT ROW (changed 2026-09-30). dashboard_access used to gate
    both login and all 32 operational routes for a Supervisor. In production that meant a
    legitimate active Supervisor without a row was refused at the sign-in screen, and - once
    login was relaxed - refused again on every page, which is not a usable account either. The
    shed's rule is that an active Supervisor simply HAS base Operations Dashboard access.

    dashboard_access still exists and still means something: it carries the two explicit
    capability flags (can_manage_equipment_mapping, can_add_booking_sections), which are checked
    on the routes that need them. It is no longer how a Supervisor is made real.

    Nothing below was widened. Admin functions and cross-section visibility remain role == Admin;
    locomotive movement still needs a movement section; equipment master data remains Admin-only.

Identity is NOT duplicated: this app reads the same `users` rows as BL-DCMS out of the shared
rdcms database, so employee identity, password, active flag, role and section_id have exactly one
authoritative source. `dashboard_access` is this application's own entitlement table (audited:
BL-DCMS itself has no reference to it), and remains the single source for "may use the Dashboard".

Section names are interpreted HERE and nowhere else. Scattering `section.name == "SHIFT"` through
services is how a rename silently grants or removes a privilege; every caller asks this module
instead.
"""

from fastapi import Depends, HTTPException, status

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.core.roles import canonical_role
from app.db.models import BookingSectionAssignment, User
from app.services.auth_service import ADMIN_ROLE, SUPERVISOR_ROLE, has_dashboard_access

_FORBIDDEN_NOT_SUPERVISOR = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="The Operations Dashboard is available to Supervisors only.",
)
_FORBIDDEN_NO_ENTITLEMENT = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Dashboard access has not been granted for this account.",
)
_FORBIDDEN_NO_MOVEMENT = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail=(
        "Locomotive movement (Shed In, Start Schedule, Complete Schedule, Shed Out) is "
        "restricted to Supervisors of the shed movement sections."
    ),
)
_FORBIDDEN_OTHER_SECTION = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="You may only act on bookings assigned to your own section.",
)
_FORBIDDEN_NO_SECTION = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Your account has no section assigned; operational work cannot be scoped.",
)


def _normalize(value: str | None) -> str:
    return (value or "").strip().upper()


# =================================================================================================
# SECTION KIND. Which sections perform maintenance work, and which only plan it.
#
# This lives here because this module is already the one place section names are interpreted (see
# the module docstring). PPIO is a PLANNING section: planning staff belong to it so they can be
# authorised and scoped, and so they can route bookings - but it owns no equipment, performs no
# checksheets and is never a routing destination.
#
# THERE IS A SECOND COPY OF THIS RULE, IN BL-DCMS
# (/home/elsbl/Checksheet/backend/app/domain/section_capability.py), and that is unavoidable
# rather than careless: these are two separate applications in two separate processes, and neither
# imports the other. What keeps them honest is a test on each side asserting the same single
# planning code, plus the fact that both derive identity from sections.code - the one column both
# applications already agree on. If a planning section is ever added, BOTH lists change in the
# same deploy.
#
# The default is MAINTENANCE, for the same reason as on the BL side: fifteen maintenance sections
# exist in production and none is named here, so defaulting to deny would revoke every existing
# Supervisor's work. The restricted set is small, explicit and named.
# =================================================================================================

PLANNING_SECTION_CODES: frozenset[str] = frozenset({"PPIO"})


def movement_section_codes() -> frozenset[str]:
    """The configured shed-movement section codes, normalised. See
    app/core/config.Settings.loco_movement_section_codes - this is the only reader."""
    raw = get_settings().loco_movement_section_codes
    return frozenset(_normalize(part) for part in raw.split(",") if part.strip())


def can_access_operations_dashboard(user: User) -> bool:
    """Base access to the Operations Dashboard.

    An ACTIVE ADMIN OR SUPERVISOR, by role alone. Technicians, inactive accounts and roles this
    application does not recognise are refused, the last of those fail-closed via
    app/core/roles.canonical_role.

    Base access is deliberately NOT an entitlement row - see the "BASE ACCESS IS NOT AN
    ENTITLEMENT ROW (changed 2026-09-30)" paragraph in this module's own docstring for why
    dashboard_access stopped gating it. dashboard_access still carries the explicit capability
    flags, which are checked on the routes that need them.

    NOTE FOR REVIEWERS: this docstring was rewritten on 2026-10-06, not restored. An edit in that
    session removed this function and two neighbours, and the original prose could not be
    recovered verbatim - only the behaviour below, which is unchanged. The policy it described is
    still stated in full in the module docstring above.
    """
    if not user or not user.is_active:
        return False
    role = canonical_role(user)
    return role in (ADMIN_ROLE, SUPERVISOR_ROLE)


def can_manage_loco_movement(user: User) -> bool:
    """Shed In / Start Schedule / Complete Schedule / Shed Out.

    Admin, or an entitled Supervisor whose section is a movement section. For a Supervisor this is
    strictly additive on top of normal Supervisor rights: it confers NO admin capability and no
    cross-section booking rights.

    Grants permission to INVOKE these actions and nothing more. Every workflow rule - timestamp
    ordering, Shed Out's stage/booking/checksheet gates, work-package readiness - is enforced
    independently by the services and applies identically to an Admin.
    """
    if not can_access_operations_dashboard(user):
        return False
    if user.role == ADMIN_ROLE:
        return True
    # Admin has no section at all in production (section_id IS NULL), which is precisely why the
    # Admin branch above comes first - a section lookup would otherwise deny them.
    section = getattr(user, "section", None)
    if section is None:
        return False
    allowed = movement_section_codes()
    return _normalize(section.code) in allowed or _normalize(section.name) in allowed


def is_planning_section(section) -> bool:
    """Whether a section only plans work. Matched on code OR name, like the movement list, so a
    half-finished rename cannot move a section across the boundary."""
    if section is None:
        return False
    return (
        _normalize(getattr(section, "code", None)) in PLANNING_SECTION_CODES
        or _normalize(getattr(section, "name", None)) in PLANNING_SECTION_CODES
    )


def is_planning_user(user: User) -> bool:
    """Whether this account belongs to a planning section.

    Role is not consulted here - this answers only "which kind of section". A planning section is
    not a work-performing destination, so an account in one owns no section work queue.
    """
    if user is None:
        return False
    return is_planning_section(getattr(user, "section", None))


def is_ppio_planner(user: User) -> bool:
    """An active Supervisor whose section is a planning section.

    PPIO IDENTITY IS THE CAPABILITY. This is the correction to how routing was first wired: the
    capability lived ONLY in dashboard_access.can_route_bookings, so a perfectly normal PPIO
    Supervisor - role Supervisor, section PPIO, account created through the ordinary workflow -
    came out of the box with routing DENIED. The consequences were visible in production: the
    global Booking Pool was hidden from them (it is gated on routing), and because the sidebar
    inferred "planner" from "has routing", they were instead shown a "PPIO Bookings" section work
    queue - a dashboard for a section that must never have work routed to it at all.
    
    An organizational fact should not need a per-user database row to take effect. Belonging to
    the planning section IS the planning authority, so it is read from the section, and the
    explicit flag is kept only for granting routing to an exceptional NON-planning account.

    Deliberately Supervisor-only. An Admin already routes by role, and a Technician in a planning
    section is not a planner - role still has to be right.
    """
    if user is None or not user.is_active:
        return False
    return canonical_role(user) == SUPERVISOR_ROLE and is_planning_user(user)


def is_maintenance_section(section) -> bool:
    """Whether a section performs maintenance work. A None section is NOT one - "no section" must
    never satisfy a check that gates maintenance work.

    NOTE THE ASYMMETRY WITH is_assignable_work_section BELOW. This answers "does this section do
    workshop work", defaulting to yes, which is right for deciding whether an ACCOUNT may take
    part in the checksheet workflow - every live section except PPIO does. It is NOT the right
    question for "may a booking be routed here", because that needs a positive answer about a
    specific section rather than the absence of a negative one.
    """
    if section is None:
        return False
    return not is_planning_section(section)


# =================================================================================================
# ASSIGNABLE WORK SECTIONS: where a booking may be routed.
#
# DERIVED FROM THE SECTIONS TABLE, NOT FROM A LIST. A booking may be routed to any section that
# exists and is not a planning section. That is the whole rule.
#
# An earlier version of this module carried ASSIGNABLE_WORK_SECTION_CODES - ten codes taken from
# BL-DCMS's REQUIRED_MINOR_SECTIONS. That was wrong, and wrong in a way that defeated the point of
# the feature: PPIO exists SO THAT planning can route work beyond the default equipment mapping.
# The real cases are exactly the ones a Minor-Inspection-derived list excludes -
#
#   * a CBC operating handle maps to M4-HR, but the handle is broken and needs welding, so
#     MACHINE SHOP must also act;
#   * a rain-leakage booking is routed normally, and the morning planning meeting decides
#     PRE-MONSOON must act too;
#   * planning decides SHIFT, CMS Lab or MILL-WRIGHT is also needed.
#
# Every one of those was refused by the allow-list. Deriving eligibility from any proxy fails the
# same way: equipment mappings and historical booking counts are circular (a section that has
# never received work could never receive any), and REQUIRED_MINOR_SECTIONS answers a different
# question entirely.
#
# So the only exclusion is the one with a reason: a PLANNING section is not a work destination,
# because planning routes work rather than performing it. That exclusion goes through
# is_planning_section, so a future planning section added to PLANNING_SECTION_CODES is excluded
# automatically, and no caller compares a section code itself.
#
# A consequence worth stating: a non-planning section inserted through the normal
# section-management workflow becomes assignable immediately, with no code change and no deploy.
# That is intended.
#
# NO ACTIVE/INACTIVE FILTER, because the column does not exist. `sections` carries id, name, code
# and created_at and nothing else (production schema, audited 2026-10-06). Inventing an activity
# flag for this feature would be adding schema to express a rule nobody has asked for.
# =================================================================================================


def is_assignable_work_section(section) -> bool:
    """Whether a booking may be ROUTED to this section.

    Every existing section qualifies except a planning one. `section is None` - a section id that
    matched no row - is not assignable, so a caller cannot route to a section that does not exist.
    """
    if section is None:
        return False
    return not is_planning_section(section)


def can_route_bookings(user: User) -> bool:
    """May replace a booking's maintenance-section assignments.

    Admin always; any other Dashboard user only with the explicit dashboard_access flag
    (migration 015). This is the ONE write capability a planning section (PPIO) holds anywhere in
    Operations Dashboard.

    It governs WHO may invoke the routing endpoint and nothing else. Which bookings are routable
    (the booking_source allow-list), which sections are valid destinations (maintenance only,
    never a planning section) and which assignments may be removed (never one whose work has
    started) are all decided independently by booking_routing_service and apply identically to an
    Admin.

    Deliberately NOT derived from can_add_booking_sections: that flag belongs to the retired
    add-section route, and treating it as equivalent would hand routing to every account that was
    granted a capability which has done nothing for some time.
    """
    if not can_access_operations_dashboard(user):
        return False
    if user.role == ADMIN_ROLE:
        return True
    # PPIO identity IS the capability - no dashboard_access row required for a normal planner.
    if is_ppio_planner(user):
        return True
    # The explicit flag remains, for granting routing to an exceptional NON-planning account.
    access = getattr(user, "dashboard_access", None)
    return bool(access and access.can_route_bookings)


def can_access_all_sections(user: User) -> bool:
    """Cross-section operational visibility. Admin only - never a Supervisor, movement or not."""
    return can_use_admin_functions(user)


def can_use_admin_functions(user: User) -> bool:
    """The genuine Superadmin. A Supervisor - including SHIFT/PPIO - never satisfies this."""
    return bool(user and user.is_active and user.role == ADMIN_ROLE)


# Retained name from the previous phase so existing call sites keep working.
can_use_admin_function = can_use_admin_functions


def owns_section(user: User, section_id: int | None) -> bool:
    if section_id is None or user.section_id is None:
        return False
    return user.section_id == section_id


def can_view_booking_assignment(user: User, assignment: BookingSectionAssignment) -> bool:
    """A Supervisor sees only their own section's assignment. Admin retains cross-section view."""
    if can_access_all_sections(user):
        return True
    return owns_section(user, assignment.section_id)


def can_update_booking_assignment(user: User, assignment: BookingSectionAssignment) -> bool:
    # Same rule as viewing today; kept as its own predicate so a future read/write split has one
    # obvious place to diverge rather than needing every call site revisited.
    return can_view_booking_assignment(user, assignment)


def capabilities_for(user: User) -> dict[str, bool]:
    """What the frontend may use for PRESENTATION only. Every one of these is independently
    re-enforced server-side; hiding a control is a usability decision, never the security
    boundary."""
    access = can_access_operations_dashboard(user)
    return {
        "can_route_bookings": can_route_bookings(user),
        # The RESOLVED section kind, so navigation reflects the backend rather than
        # inferring "planner" from the absence of some other capability.
        "is_planning_section": is_planning_user(user),
        "can_access_operations_dashboard": access,
        "can_manage_loco_movement": can_manage_loco_movement(user),
        # An Admin has no section of their own, but operates booking work across all of them -
        # so this reads true for Admin via can_access_all_sections rather than a section_id.
        "can_manage_own_section_bookings": access
        and (user.section_id is not None or can_access_all_sections(user)),
        "can_access_all_sections": can_access_all_sections(user),
        "can_admin": can_use_admin_functions(user),
    }


# ------------------------------------------------------------------------- dependencies --


def require_operations_user(current_user: User = Depends(get_current_user)) -> User:
    """The gate for OPERATIONAL endpoints: an active Admin or an active Supervisor.

    This is the right dependency for almost every user-facing route - reads, section work, the
    workflow - because both kinds of account legitimately use them, with the SCOPE decided further
    down (services apply section scoping; Admin bypasses it via can_access_all_sections).

    No dashboard_access row is required; see can_access_operations_dashboard for why, and for the
    list of things this deliberately does not grant. require_loco_movement, require_admin and the
    two capability dependencies remain narrower and are unaffected.

    Technicians, inactive accounts and unrecognised roles never reach here - get_current_user
    already refused them, fail closed.
    """
    if not can_access_operations_dashboard(current_user):
        raise _FORBIDDEN_NOT_SUPERVISOR
    return current_user


def require_operations_supervisor(current_user: User = Depends(get_current_user)) -> User:
    """Strictly Supervisor - for endpoints whose semantics are meaningless for an Admin (one that
    has no section of their own). Most routes should use require_operations_user above instead.

    Note what it does NOT accept: a merely authenticated user, an Admin, or an inactive account
    (get_current_user already rejects inactive accounts, Technicians and unrecognised roles, and
    re-reads the user from the database on every request - so a revoked role or deactivation
    takes effect immediately, without waiting for the token to expire).

    Like require_operations_user, this no longer demands a dashboard_access row: an active
    Supervisor IS a Supervisor. The narrowing here is the ROLE, nothing else.
    """
    if canonical_role(current_user) != SUPERVISOR_ROLE:
        raise _FORBIDDEN_NOT_SUPERVISOR
    return current_user


def require_loco_movement(current_user: User = Depends(get_current_user)) -> User:
    """Shed In / Start Schedule / Complete Schedule / Shed Out.

    Grants permission to INVOKE the action. It never bypasses the workflow's own readiness gates -
    Shed Out still independently enforces its stage, booking and checksheet requirements.
    """
    operator = require_operations_user(current_user)
    if operator.role == ADMIN_ROLE:
        return operator
    if operator.section_id is None:
        raise _FORBIDDEN_NO_SECTION
    if not can_manage_loco_movement(operator):
        raise _FORBIDDEN_NO_MOVEMENT
    return operator


def require_own_section_assignment(user: User, assignment: BookingSectionAssignment) -> None:
    """Called by services after loading an assignment. Raises rather than returning a bool so a
    caller cannot accidentally ignore the result."""
    if not can_update_booking_assignment(user, assignment):
        raise _FORBIDDEN_OTHER_SECTION


def require_route_bookings(current_user: User = Depends(get_current_user)) -> User:
    """Route guard for the planner's two writes: adding section responsibility to a booking, and
    raising a planning booking.

    ONE CAPABILITY FOR BOTH, deliberately. A separate can_create_planning_bookings was considered
    and rejected: both operations are the same authority - planning deciding which sections are
    responsible for what work - and creating a booking the planner may then route is not a
    materially different privilege from routing one that already exists. A second flag would need
    DDL, would have to be granted in lockstep with the first for the feature to be usable, and
    would give an operator a way to produce a half-functional account. If the two ever need to
    diverge, that is the moment to add the column, not now.

    It does NOT confer general booking administration: creation goes through
    booking_routing_service.create_planning_booking, which fixes the source to MANUAL and cannot
    set status, timestamps or lifecycle fields.
    """
    if not can_route_bookings(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Managing a booking's section assignments requires the booking-routing "
                "capability."
            ),
        )
    return current_user


def require_booking_pool_read(current_user: User = Depends(get_current_user)) -> User:
    """Read access to the GLOBAL, unscoped booking pool.

    Admin as before, plus an account holding the booking-routing capability - a planner cannot
    decide where a finding should go without seeing the pool it lives in. Deliberately not
    widened to every Supervisor: their surface is still the section-scoped assignment list, and a
    maintenance Supervisor could not read the global pool before this change and still cannot.

    READ ONLY. This grants visibility and nothing else: every mutation on the pool is either
    retired (410), Admin-only, or - for section routing alone - gated separately by
    require_route_bookings.
    """
    if can_use_admin_functions(current_user) or can_route_bookings(current_user):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The global booking pool is available to Admins and booking planners.",
    )

