"""Integration-style tests for BLDCMSClient against a mocked HTTP transport
(httpx.MockTransport) — mirrors tests/test_loco_master_client.py exactly,
exercising the real request/response handling (base URL, headers, timeout,
status-code mapping, malformed-response handling) rather than going through
MockBLDCMSClient, which bypasses HTTP entirely."""

import httpx
import pytest

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError


@pytest.fixture()
def patch_httpx(monkeypatch):
    def _install(handler):
        transport = httpx.MockTransport(handler)

        def patched_request(method, url, **kwargs):
            with httpx.Client(transport=transport) as c:
                return c.request(method, url, **kwargs)

        import app.clients.bldcms as module

        monkeypatch.setattr(module.httpx, "request", patched_request)

    return _install


def test_base_url_and_path_used(patch_httpx):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.get_visit_checksheets(501)

    assert captured["url"] == "http://bldcms.invalid/integration/checksheets/visit/501"


def test_base_url_trailing_slash_stripped(patch_httpx):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid/")

    client.get_visit_checksheets(501)

    assert "//integration" not in captured["url"]


def test_workflow_stage_type_forwarded_as_query_param(patch_httpx):
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.get_visit_checksheets(501, workflow_stage_type="TEST_BEFORE")

    assert captured["params"] == {"workflow_stage_type": "TEST_BEFORE"}


def test_no_workflow_stage_type_sends_no_param(patch_httpx):
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.get_visit_checksheets(501)

    assert captured["params"] == {}


def test_no_api_key_sends_no_header(patch_httpx):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.get_visit_checksheets(501)

    assert "x-internal-api-key" not in captured["headers"]


def test_api_key_sent_as_header(patch_httpx):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid", api_key="supersecret")

    client.get_visit_checksheets(501)

    assert captured["headers"]["x-internal-api-key"] == "supersecret"


def test_summary_path_used(patch_httpx):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"shed_visit_id": 501, "stages": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.get_visit_checksheet_summary(501)

    assert captured["url"] == "http://bldcms.invalid/integration/checksheets/visit/501/summary"


def test_timeout_configured(patch_httpx):
    captured = {}

    def handler(request):
        return httpx.Response(200, json={"shed_visit_id": 501, "items": []})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid", timeout=3.5)

    # Just confirms construction accepts/stores the timeout without error and requests still
    # succeed — the mocked transport doesn't itself observe the timeout value.
    client.get_visit_checksheets(501)
    assert client._timeout == 3.5


def test_401_raises_auth_error(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"detail": "Not authenticated"})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSAuthError):
        client.get_visit_checksheets(501)


def test_403_raises_auth_error(patch_httpx):
    def handler(request):
        return httpx.Response(403, json={"detail": "forbidden"})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSAuthError):
        client.get_visit_checksheets(501)


def test_500_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(500, text="internal error")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_503_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(503, text="service unavailable")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheet_summary(501)


def test_connection_error_raises_unavailable(patch_httpx):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_timeout_error_raises_unavailable(patch_httpx):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_malformed_non_json_response_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(200, text="not json at all")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_malformed_non_object_response_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_unexpected_status_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(302, text="redirect")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.get_visit_checksheets(501)


def test_key_never_appears_in_exception_text(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"detail": "nope"})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid", api_key="supersecret")

    with pytest.raises(BLDCMSAuthError) as exc_info:
        client.get_visit_checksheets(501)

    assert "supersecret" not in str(exc_info.value)


def test_key_never_appears_in_unavailable_exception_text(patch_httpx):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid", api_key="supersecret")

    with pytest.raises(BLDCMSUnavailableError) as exc_info:
        client.get_visit_checksheets(501)

    assert "supersecret" not in str(exc_info.value)


# ------------------------------------------------------ resolve_applicability (Phase 5B.1) --


def test_resolve_applicability_url(patch_httpx):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url).split("?")[0]
        return httpx.Response(200, json=[])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.resolve_applicability(
        loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
    )

    assert captured["url"] == "http://bldcms.invalid/integration/applicability/resolve"


def test_resolve_applicability_query_params_exact(patch_httpx):
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    client.resolve_applicability(
        loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
    )

    assert captured["params"] == {
        "loco_type": "3_PHASE",
        "schedule_family": "MINOR",
        "schedule_variant": "IA",
        "workflow_stage_type": "TEST_BEFORE",
    }


def test_resolve_applicability_internal_key_header_sent(patch_httpx):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json=[])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid", api_key="supersecret")

    client.resolve_applicability(
        loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
    )

    assert captured["headers"]["x-internal-api-key"] == "supersecret"


def test_resolve_applicability_returns_parsed_list(patch_httpx):
    def handler(request):
        return httpx.Response(200, json=[{"applicability_id": 1, "template_id": 2, "is_required": True}])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    result = client.resolve_applicability(
        loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
    )

    assert result == [{"applicability_id": 1, "template_id": 2, "is_required": True}]


def test_resolve_applicability_timeout_raises_unavailable(patch_httpx):
    def handler(request):
        raise httpx.TimeoutException("timed out")

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.resolve_applicability(
            loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
        )


def test_resolve_applicability_401_raises_auth_error(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"detail": "nope"})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSAuthError):
        client.resolve_applicability(
            loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
        )


def test_resolve_applicability_non_list_response_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(200, json={"not": "a list"})

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.resolve_applicability(
            loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
        )


def test_resolve_applicability_list_of_non_dicts_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(200, json=["not", "dicts"])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")

    with pytest.raises(BLDCMSUnavailableError):
        client.resolve_applicability(
            loco_type="3_PHASE", schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
        )
