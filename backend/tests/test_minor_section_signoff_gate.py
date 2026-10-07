"""SCHEDULE_INSPECTION now also waits for BL-DCMS Minor Inspection section sign-offs.

The rule being proved: every required checksheet being satisfied is no longer enough for
SCHEDULE_INSPECTION to complete. Each section's Supervisor signs ONE combined document in
BL-DCMS, and this stage completes only once every required section is signed.

The three cases that must stay distinct are each covered here, because conflating any two of them
either strands an old visit forever or completes a new one on the strength of a failed request:

  * a PRE-CUTOVER visit - BL-DCMS reports section_signoff_workflow=false - completes exactly as it
    did before, on technician submission;
  * an UNRESOLVED answer (BL-DCMS unreachable, or its own work package unavailable) BLOCKS;
  * a resolved, in-force workflow completes only when all_sections_signed.

TEST_BEFORE and TEST_AFTER are deliberately covered too: this change must not touch them.
"""

from tests.test_checksheet_stage_reconciliation import (
    _admin_headers,
    _approve,
    _db_stage,
    _generate_package,
    _minor_visit,
    _reconcile,
    _requirement_for_stage,
    _stage_result,
)


def _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client):
    """Satisfy TEST_BEFORE and SCHEDULE_INSPECTION's checksheets, and complete TEST_BEFORE.

    Returns the reconciliation body from the call in which SCHEDULE_INSPECTION is first evaluated
    on its own merits (the second call - see the reconciliation module's Sequencing docstring).
    """
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    si_req = _requirement_for_stage(package, "SCHEDULE_INSPECTION")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE", checksheet_id=1)
    _approve(mock_bldcms_client, visit, si_req, "SCHEDULE_INSPECTION", checksheet_id=2)

    _reconcile(client, headers, visit.id)          # completes TEST_BEFORE
    return _reconcile(client, headers, visit.id)   # evaluates SCHEDULE_INSPECTION


def test_a_pre_cutover_visit_completes_exactly_as_before(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """No sign-off state registered for this visit, so BL-DCMS reports the old workflow. This is
    every visit that existed before the feature shipped - none of them may become un-completable."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=901, loco_number="39201")

    body = _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "COMPLETED"
    assert _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION").status == "COMPLETED"


def test_an_unsigned_section_blocks_the_inspection_stage(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=902, loco_number="39202")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=2, sections_signed=1)

    body = _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert si["reason"] == "SECTION_SIGNOFF_PENDING"
    # The checksheets themselves ARE ready - it is only signing that is outstanding, and the
    # response says so rather than blaming the technicians' work.
    assert si["checksheets_ready"] is True
    assert _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION").status == "PENDING"


def test_every_section_signed_completes_the_inspection_stage(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=903, loco_number="39203")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=2, sections_signed=2)

    body = _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "COMPLETED"
    assert _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION").status == "COMPLETED"


def test_an_unresolved_signoff_state_blocks_rather_than_completing(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """BL-DCMS follows the new workflow for this visit but cannot resolve its own state. Treating
    an unanswered question as "nothing to sign" would complete the stage on a failed request."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=904, loco_number="39204")
    mock_bldcms_client.set_minor_section_signoffs(
        visit.id, sections_total=0, sections_signed=0, resolved=False,
    )

    body = _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert si["reason"] == "SECTION_SIGNOFF_UNKNOWN"
    assert _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION").status == "PENDING"


def test_a_visit_owing_no_sections_is_not_blocked_by_signing(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """sections_total = 0 is not a signing problem - whatever such a visit means is decided by the
    stage's own configuration handling, not by this gate."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=905, loco_number="39205")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=0, sections_signed=0)

    body = _advance_to_inspection(client, headers, visit, mock_loco_client, mock_bldcms_client)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "COMPLETED"


def test_test_before_is_not_affected_by_section_signoff(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """TB keeps completing on submission even while the visit's sections are unsigned."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=906, loco_number="39206")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=3, sections_signed=0)

    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE", status="SUBMITTED")

    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"
    assert tb["reason"] == "COMPLETED"


def test_test_after_still_completes_once_inspection_is_signed(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """TEST_AFTER's own gate is unchanged: it waits for SCHEDULE_INSPECTION as it always did, and
    once that completes it completes on submission, with no signing requirement of its own."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=907, loco_number="39207")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=1, sections_signed=1)

    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    for stage, checksheet_id in (
        ("TEST_BEFORE", 1), ("SCHEDULE_INSPECTION", 2), ("TEST_AFTER", 3),
    ):
        _approve(
            mock_bldcms_client, visit, _requirement_for_stage(package, stage), stage,
            checksheet_id=checksheet_id, status="SUBMITTED",
        )

    _reconcile(client, headers, visit.id)   # TEST_BEFORE
    _reconcile(client, headers, visit.id)   # SCHEDULE_INSPECTION
    body = _reconcile(client, headers, visit.id)

    assert _stage_result(body, "TEST_AFTER")["current_status"] == "COMPLETED"


def test_bldcms_unreachable_blocks_the_inspection_stage(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """The sign-off question cannot be asked at all. The stage must not complete on that basis."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=908, loco_number="39208")
    mock_bldcms_client.set_minor_section_signoffs(visit.id, sections_total=1, sections_signed=1)

    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    si_req = _requirement_for_stage(package, "SCHEDULE_INSPECTION")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE", checksheet_id=1)
    _approve(mock_bldcms_client, visit, si_req, "SCHEDULE_INSPECTION", checksheet_id=2)
    _reconcile(client, headers, visit.id)

    mock_bldcms_client.unavailable = True
    body = _reconcile(client, headers, visit.id)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION").status == "PENDING"
