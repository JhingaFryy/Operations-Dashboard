"""Reconcile or recover ONE open MINOR shed visit's work package - never expand it.

    python scripts/refresh_visit_requirements.py --visit 9            # preview only, writes nothing
    python scripts/refresh_visit_requirements.py --visit 9 --apply    # reconcile / generate a missing package

A visit's work package is frozen when it is generated (normally at Shed In) and is the visit's
authoritative scope from then on - here and in BL-DCMS. Configuration that became applicable later
(a new section, a new equipment slot) is never appended to an existing visit. For a visit that
already has a package, --apply only re-reads its minor_inspection_configuration_complete flag; for
a visit with NO package it generates the first one. Same code path as
POST /api/shed-visits/{id}/checksheet-work-package/refresh.
"""

import argparse
import sys

sys.path.insert(0, "/opt/operations-dashboard/backend")

from app.db.models import ShedVisit  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.bldcms_client import get_bldcms_client  # noqa: E402
from app.services.checksheet_work_package_service import _get_existing_package, refresh_work_package  # noqa: E402
from app.services.loco_master_client import get_loco_master_client  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--visit", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        visit = db.get(ShedVisit, args.visit)
        if visit is None:
            raise SystemExit(f"visit {args.visit} not found")
        print(f"MODE: {'APPLY' if args.apply else 'PREVIEW (no writes)'}")
        print(f"visit {visit.id} loco {visit.loco_number} {visit.schedule_family}/{visit.schedule_variant} status={visit.status}")

        package = _get_existing_package(db, visit.id)
        if package is not None:
            print(f"package {package.id} is FROZEN: {len(package.requirements)} requirement(s), "
                  f"minor_inspection_configuration_complete={package.minor_inspection_configuration_complete}")
            print("Later configuration is never appended to an existing visit; --apply only re-reads the "
                  "configuration-complete flag.")
        else:
            print("no package: --apply generates the visit's first (then frozen) package from current configuration")
        if not args.apply:
            db.rollback()
            return

        try:
            result = refresh_work_package(db, get_loco_master_client(), get_bldcms_client(), visit.id, None)
        except HTTPException as exc:  # pragma: no cover - operator output
            raise SystemExit(f"refused: {exc.detail}")
        print(f"generated_new_package={result['generated']} added={len(result['added'])} retained={result['retained']} "
              f"configuration_complete: {result['minor_inspection_configuration_complete_before']} -> "
              f"{result['minor_inspection_configuration_complete_after']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
