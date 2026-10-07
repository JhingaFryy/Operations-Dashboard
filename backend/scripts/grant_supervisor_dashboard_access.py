"""Grant Operations Dashboard access to every ACTIVE Supervisor.

Policy (production): BL-DCMS has no separate Dashboard entitlement - its Dashboard access is
role-based - so every ACTIVE Supervisor is eligible for Operations Dashboard. OD keeps its own
`dashboard_access` table as the explicit OD entitlement store, and this script populates it.

    python scripts/grant_supervisor_dashboard_access.py            # preview, writes nothing
    python scripts/grant_supervisor_dashboard_access.py --apply

Never grants to Technicians, inactive users, or Admins.

Goes through the existing application service (app/services/access_service.update_access) rather
than writing SQL directly, so the same validation, audit columns (granted_by/granted_at) and
idempotency rules apply as when an Admin does it through the API. Re-running is safe: a user who
is already enabled is reported and skipped, so no duplicate row is ever created and no existing
grant is re-stamped.

Existing per-user flags (can_add_booking_sections / can_manage_equipment_mapping) are PRESERVED,
not reset - this script only ensures the base entitlement is enabled.
"""

import argparse
import sys

sys.path.insert(0, "/opt/operations-dashboard/backend")

from app.db.models import User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.schemas.dashboard_access import DashboardAccessUpdateRequest  # noqa: E402
from app.services.access_service import update_access  # noqa: E402
from app.services.auth_service import ADMIN_ROLE, SUPERVISOR_ROLE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually grant (default: preview)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        admin = (
            db.query(User)
            .filter(User.role == ADMIN_ROLE, User.is_active.is_(True))
            .order_by(User.id)
            .first()
        )
        if admin is None:
            print("ERROR: no active Admin found to attribute the grant to (granted_by).")
            return 2

        supervisors = (
            db.query(User)
            .filter(User.role == SUPERVISOR_ROLE, User.is_active.is_(True))
            .order_by(User.employee_id)
            .all()
        )

        already, to_grant = [], []
        for user in supervisors:
            access = user.dashboard_access
            (already if (access and access.is_enabled) else to_grant).append(user)

        print(f"MODE: {'APPLY' if args.apply else 'PREVIEW (no writes)'}")
        print(f"  active Supervisors : {len(supervisors)}")
        print(f"  already entitled   : {len(already)}")
        print(f"  to grant           : {len(to_grant)}")

        granted = 0
        if args.apply:
            for user in to_grant:
                access = user.dashboard_access
                payload = DashboardAccessUpdateRequest(
                    is_enabled=True,
                    # Preserve whatever the user already had; never widen privileges here.
                    can_add_booking_sections=bool(access and access.can_add_booking_sections),
                    can_manage_equipment_mapping=bool(access and access.can_manage_equipment_mapping),
                )
                update_access(db, user.id, payload, admin)
                granted += 1
            print(f"  newly entitled     : {granted}")
        else:
            print("  (re-run with --apply to write)")

        enabled_total = sum(
            1
            for u in db.query(User).filter(
                User.role == SUPERVISOR_ROLE, User.is_active.is_(True)
            ).all()
            if u.dashboard_access and u.dashboard_access.is_enabled
        )
        print(f"  final entitled     : {enabled_total}")

        # Safety assertions: nothing outside the eligible set may have been touched.
        bad = (
            db.query(User)
            .join(User.dashboard_access)
            .filter(
                (User.role != SUPERVISOR_ROLE) | (User.is_active.is_(False)),
            )
            .all()
        )
        enabled_bad = [u for u in bad if u.dashboard_access and u.dashboard_access.is_enabled]
        print(f"  non-eligible enabled: {len(enabled_bad)} (must be 0)")
        return 0 if not enabled_bad else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
