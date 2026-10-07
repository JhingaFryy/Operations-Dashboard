"""Backfill checksheet requirement snapshots for shed visits that are already in shed.

Auto-generation runs at Shed In, so it only covers visits created from that change onward.
Visits already in the shed have no snapshot and would show an empty Pending Checksheets panel
with a WORK_PACKAGE_NOT_GENERATED blocker. This backfills them through the NORMAL generation
service - it never hand-writes requirement rows and never invents content.

    python scripts/backfill_visit_requirements.py            # preview only, writes nothing
    python scripts/backfill_visit_requirements.py --apply    # generate missing snapshots

Idempotent: a visit that already has a package is reported and skipped, never regenerated
(regenerating would silently rewrite what an open visit was told it required).
"""

import argparse
import sys
from collections import Counter

sys.path.insert(0, "/opt/operations-dashboard/backend")

from app.db.models import ShedVisit, ShedVisitChecksheetPackage  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.bldcms_client import get_bldcms_client  # noqa: E402
from app.services.checksheet_work_package_service import (  # noqa: E402
    generate_requirements_snapshot,
)
from app.services.loco_master_client import get_loco_master_client  # noqa: E402
from app.services.shed_visit_service import MINOR_STAGE_SEQUENCE, OPEN_VISIT_STATUSES  # noqa: E402


def preview(db, bl, loco, visit):
    """What generation WOULD produce, using the same resolvers it uses. Writes nothing."""
    try:
        loco_type = loco.get_locomotive(visit.loco_number).get("loco_type")
    except Exception as exc:
        return f"    Loco Master FAILED: {type(exc).__name__}"
    lines = [f"    loco_type: {loco_type}"]
    try:
        if visit.schedule_family == "MINOR":
            for stage in MINOR_STAGE_SEQUENCE:
                items = bl.resolve_applicability(
                    loco_type=loco_type, schedule_family="MINOR",
                    schedule_variant=visit.schedule_variant, workflow_stage_type=stage,
                )
                req = sum(1 for i in items if i.get("is_required"))
                state = "NOT CONFIGURED" if not items else f"rows={len(items)} required={req}"
                lines.append(f"    {stage:22} {state}")
        else:
            payload = bl.resolve_major_requirements(loco_type=loco_type)
            rows = payload["requirements"]
            lines.append(f"    rows={len(rows)}  required={sum(1 for r in rows if r['is_required'])}"
                         f"  optional={sum(1 for r in rows if not r['is_required'])}")
            lines.append(f"    by source: {dict(Counter(r['source'] for r in rows))}")
            lines.append(f"    unconfigured sections: {payload.get('unconfigured_sections')}")
    except Exception as exc:
        lines.append(f"    BL-DCMS FAILED: {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def describe_package(db, visit_id):
    pkg = (
        db.query(ShedVisitChecksheetPackage)
        .filter(ShedVisitChecksheetPackage.shed_visit_id == visit_id)
        .one_or_none()
    )
    if pkg is None:
        return None, "    package: NONE"
    rows = list(pkg.requirements)
    required = sum(1 for r in rows if r.is_active and r.is_required)
    optional = sum(1 for r in rows if r.is_active and not r.is_required)
    deactivated = sum(1 for r in rows if not r.is_active)
    stages = sorted({r.workflow_stage_type or "-" for r in rows})
    return pkg, (
        f"    package id: {pkg.id}\n"
        f"    requirements: {len(rows)}  required(blocking): {required}  "
        f"optional: {optional}  deactivated: {deactivated}\n"
        f"    stages present: {stages}\n"
        f"    sources: {dict(Counter(r.requirement_source for r in rows))}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually generate (default: preview)")
    args = parser.parse_args()

    db = SessionLocal()
    bl = get_bldcms_client()
    loco = get_loco_master_client()

    visits = (
        db.query(ShedVisit)
        .filter(ShedVisit.status.in_(OPEN_VISIT_STATUSES))
        .order_by(ShedVisit.id)
        .all()
    )
    print(f"MODE: {'APPLY' if args.apply else 'PREVIEW (no writes)'}")
    print(f"active visits ({', '.join(OPEN_VISIT_STATUSES)}): {len(visits)}\n")

    for visit in visits:
        print(f"visit {visit.id}  loco {visit.loco_number}  "
              f"{visit.schedule_family}/{visit.schedule_variant}  status={visit.status}")
        pkg, desc = describe_package(db, visit.id)
        if pkg is not None:
            print(desc)
            print("    -> already generated; skipped (never regenerated)\n")
            continue

        print(preview(db, bl, loco, visit))
        if args.apply:
            created = generate_requirements_snapshot(db, loco, bl, visit.id, None)
            print(f"    -> generated {created} requirement row(s)")
            _, desc2 = describe_package(db, visit.id)
            print(desc2)
        else:
            print("    -> would generate (re-run with --apply)")
        print()

    db.close()


if __name__ == "__main__":
    main()
