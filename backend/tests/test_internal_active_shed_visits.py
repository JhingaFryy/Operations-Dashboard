"""Operations Dashboard Phase 5B.4B: internal service-to-service active-shed-visit listing.

Exercises GET /api/internal/shed-visits/active - the strictly read-only endpoint BL-DCMS calls so
its Android app can offer a locomotive-selection screen for the Minor Schedule workflow without
already knowing a visit id. Auth is X-Internal-API-Key only, same trust boundary and same
dependency as the reconciliation callback (see tests/test_internal_reconciliation.py) - this file
focuses on what's specific to the new read endpoint: auth reuse, the active/open-visit filter
(shared with GET /api/shed-visits/current), response shape, and field exposure."""

import app.core.dependencies as dependencies_module
from tests.conftest import make_shed_visit

INTERNAL_KEY = "test-internal-key-not-for-production"


class _StubSettings:
    def __init__(self, operations_internal_api_key):
        self.operations_internal_api_key = operations_internal_api_key


def _set_internal_key(monkeypatch, key):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings(key))


def _list_active(client, key=INTERNAL_KEY, params=None):
    headers = {}
    if key is not None:
        headers["X-Internal-API-Key"] = key
    return client.get("/api/internal/shed-visits/active", headers=headers, params=params)


# ------------------------------------------------------------------------------------- auth --


def test_missing_key_401(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)

    resp = _list_active(client, key=None)

    assert resp.status_code == 401


def test_wrong_key_401(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)

    resp = _list_active(client, key="totally-wrong-key")

    assert resp.status_code == 401


def test_correct_key_accepted(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------------- listing --


def test_empty_active_shed_returns_empty_list(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_in_shed_visit_is_included(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 1, "39126", status="IN_SHED")

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["shed_visit_id"] == 1


def test_ready_visit_is_included(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 2, "39127", status="READY")

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["shed_visit_id"] == 2


def test_closed_visit_excluded(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 3, "39128", status="CLOSED")

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_departed_visit_excluded(client, db_session, monkeypatch):
    """Domain semantics: a visit is only "active" while status is IN_SHED/READY - a departed_at
    timestamp accompanies the CLOSED status transition (see shed_out_service.py), it isn't a
    separate signal this endpoint checks independently."""
    from datetime import datetime, timezone

    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(
        db_session,
        4,
        "39129",
        status="CLOSED",
        departed_at=datetime.now(timezone.utc),
        departure_source="DASHBOARD",
    )

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_response_shape_contains_expected_fields(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(
        db_session, 5, "39130", status="IN_SHED", schedule_family="MINOR", schedule_variant="IA"
    )

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    visit = body[0]
    assert visit["shed_visit_id"] == 5
    assert visit["loco_number"] == "39130"
    assert visit["schedule_family"] == "MINOR"
    assert visit["schedule_variant"] == "IA"
    assert visit["status"] == "IN_SHED"
    assert "arrival_at" in visit and visit["arrival_at"]


def test_no_internal_only_fields_or_secrets_exposed(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 6, "39131", status="IN_SHED", created_by=None)

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    visit = resp.json()[0]
    unexpected_fields = {
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
        "arrival_source",
        "arrival_condition",
        "ready_at",
        "ready_source",
        "departed_at",
        "departure_source",
    }
    assert not unexpected_fields & set(visit.keys())
    assert INTERNAL_KEY not in resp.text


def test_ordering_matches_arrival_at_descending(client, db_session, monkeypatch):
    """Mirrors the ordering convention already used by
    shed_visit_service.list_current_visits()/GET /api/shed-visits/current: newest arrival first."""
    from datetime import datetime, timedelta, timezone

    _set_internal_key(monkeypatch, INTERNAL_KEY)
    now = datetime.now(timezone.utc)
    make_shed_visit(db_session, 7, "39132", status="IN_SHED", arrival_at=now - timedelta(hours=2))
    make_shed_visit(db_session, 8, "39133", status="IN_SHED", arrival_at=now)
    make_shed_visit(db_session, 9, "39134", status="IN_SHED", arrival_at=now - timedelta(hours=1))

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    ids = [v["shed_visit_id"] for v in resp.json()]
    assert ids == [8, 9, 7]


# ------------------------------------------------------------------------ schedule_family filter --


def test_schedule_family_filter_returns_only_matching_family(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 10, "39135", status="IN_SHED", schedule_family="MINOR")
    make_shed_visit(db_session, 11, "39136", status="IN_SHED", schedule_family="MAJOR")

    resp = _list_active(client, params={"schedule_family": "MINOR"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["shed_visit_id"] == 10
    assert body[0]["schedule_family"] == "MINOR"


def test_no_schedule_family_param_returns_all_active_families(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_shed_visit(db_session, 12, "39137", status="IN_SHED", schedule_family="MINOR")
    make_shed_visit(db_session, 13, "39138", status="IN_SHED", schedule_family="MAJOR")

    resp = _list_active(client)

    assert resp.status_code == 200, resp.text
    ids = {v["shed_visit_id"] for v in resp.json()}
    assert ids == {12, 13}
