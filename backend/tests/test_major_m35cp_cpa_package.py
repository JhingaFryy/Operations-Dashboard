"""M35-CP Major: Auxiliary Compressor CRC150 (BL-DCMS migration 050) in the Major work package.

BL-DCMS now returns, for M35-CP, a third ordinary REQUIRED Major item per technology beside the main
Compressor and Cab AC - never a choice between them - and the Major resolver takes no IOH/TOH argument, so
an IOH visit and a TOH visit receive the identical M35-CP package. Filing the auxiliary compressor never
settles the main compressor: they are different equipment identities.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.core import dependencies as dependencies_module
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_shed_visit

INTERNAL_KEY = "test-internal-key-not-for-production"
CP = 15
# technology -> loco, loco type, [(equipment id, equipment name, template id)] as BL-DCMS resolves after 050
TECH = {
    "CONVENTIONAL": ("22324", "WAP4", [(143, "Compressor", 143), (144, "Cab AC", 141),
                                       (179, "Auxiliary Compressor CRC150", 404)]),
    "3_PHASE": ("30476", "WAP7", [(141, "Compressor", 142), (142, "Cab AC", 140),
                                  (178, "Auxiliary Compressor CRC150", 403)]),
}


# A second locomotive of the same type: one locomotive can only be in the shed on one visit at a time.
SECOND = {"CONVENTIONAL": "22325", "3_PHASE": "30477"}


class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=CP, code="M35-CP", name="M35-CP")
    for technology, (loco, loco_type, items) in TECH.items():
        mock_loco_client.add_locomotive(loco, loco_type=loco_type)
        mock_loco_client.add_locomotive(SECOND[technology], loco_type=loco_type)
        for eq_id, name, tpl in items:
            mock_bldcms_client.add_major_requirement(
                loco_type=loco_type, section_id=CP, section_name="M35-CP", template_id=tpl,
                template_name=f"Checksheet for {name}", technology=technology, equipment_id=eq_id,
                equipment_name=name, maintenance_type=None, source="MAJOR_EQUIPMENT", is_required=True)
    make_movement_supervisor_headers(db_session)
    admin = db_session.get(models.User, 1)
    admin.role = "Admin"
    db_session.commit()
    return admin


def _package(client, db_session, internal, visit_id, loco, variant):
    make_shed_visit(db_session, visit_id, loco, schedule_family="MAJOR", schedule_variant=variant,
                    arrival_at=datetime.now(timezone.utc) - timedelta(hours=6))
    resp = client.post(f"/api/internal/shed-visits/{visit_id}/checksheet-work-package/generate",
                       json={"actor_user_id": 1}, headers=internal)
    assert resp.status_code == 200, resp.text
    package = db_session.query(models.ShedVisitChecksheetPackage).filter_by(shed_visit_id=visit_id).one()
    return [r for r in package.requirements if r.section_name_snapshot == "M35-CP"]


@pytest.mark.parametrize("technology", list(TECH))
@pytest.mark.parametrize("variant", ["IOH", "TOH"])
def test_the_auxiliary_compressor_is_one_more_required_item(client, db_session, env, internal, technology, variant):
    loco, _loco_type, items = TECH[technology]

    rows = _package(client, db_session, internal, 700 + len(variant) + (10 if technology == "3_PHASE" else 0),
                    loco, variant)

    assert sorted((r.equipment_id_snapshot, r.template_id) for r in rows) == sorted((e, t) for e, _n, t in items)
    assert all(r.is_required and r.is_active for r in rows)
    assert sum(1 for r in rows if r.template_id in (403, 404)) == 1
    assert all(r.maintenance_type_snapshot is None for r in rows)


@pytest.mark.parametrize("technology", list(TECH))
def test_ioh_and_toh_packages_are_identical(client, db_session, env, internal, technology):
    loco, _loco_type, _items = TECH[technology]
    ioh = _package(client, db_session, internal, 721, loco, "IOH")
    toh = _package(client, db_session, internal, 722, SECOND[technology], "TOH")

    def shape(rows):
        return sorted((r.equipment_id_snapshot, r.template_id, r.is_required) for r in rows)

    assert shape(ioh) == shape(toh)


@pytest.mark.parametrize("technology", list(TECH))
def test_the_auxiliary_and_main_compressor_are_separate_requirements(client, db_session, env, internal, technology):
    loco, _loco_type, items = TECH[technology]
    rows = _package(client, db_session, internal, 731, loco, "TOH")

    compressor = [r for r in rows if r.equipment_id_snapshot == items[0][0]]
    auxiliary = [r for r in rows if r.equipment_id_snapshot == items[2][0]]
    assert len(compressor) == 1 and len(auxiliary) == 1
    assert compressor[0].id != auxiliary[0].id and compressor[0].template_id != auxiliary[0].template_id
