"""Integration-style tests for LocoMasterClient against a mocked HTTP
transport (httpx.MockTransport) — these exercise the real request/response
handling (headers, query params, JSON envelope unwrapping, status-code
mapping) rather than going through MockLocoMasterClient, which bypasses
HTTP entirely."""

import httpx
import pytest

from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
    LocoMasterValidationError,
)


@pytest.fixture()
def patch_httpx(monkeypatch):
    """Patches app.clients.loco_master's module-level httpx.request to route
    through an httpx.MockTransport built from the given handler."""

    def _install(handler):
        transport = httpx.MockTransport(handler)

        def patched_request(method, url, **kwargs):
            with httpx.Client(transport=transport) as c:
                return c.request(method, url, **kwargs)

        import app.clients.loco_master as module

        monkeypatch.setattr(module.httpx, "request", patched_request)

    return _install


def test_families_unwraps_items(patch_httpx):
    def handler(request):
        assert request.url.path == "/api/equipment/families"
        return httpx.Response(200, json={"items": [{"id": 1, "code": "3PHASE", "name": "3-Phase"}]})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    result = client.get_equipment_families()

    assert result == [{"id": 1, "code": "3PHASE", "name": "3-Phase"}]


def test_nodes_unwraps_items_and_tolerates_bare_array(patch_httpx):
    def handler(request):
        return httpx.Response(200, json=[{"id": 5, "family_id": 1, "parent_id": None}])

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    result = client.get_equipment_children(node_id=None, family_id=1)

    assert result == [{"id": 5, "family_id": 1, "parent_id": None}]


def test_search_unwraps_items(patch_httpx):
    def handler(request):
        return httpx.Response(200, json={"items": [{"id": 9, "name": "IGBT"}]})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    result = client.search_equipment("igbt")

    assert result == [{"id": 9, "name": "IGBT"}]


def test_get_equipment_children_sends_numeric_family_id(patch_httpx):
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    client.get_equipment_children(node_id=42, family_id=7)

    assert captured["params"] == {"family_id": "7", "parent_id": "42"}


def test_search_equipment_sends_numeric_family_id(patch_httpx):
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    client.search_equipment("igbt", family_id=7)

    assert captured["params"]["family_id"] == "7"


def test_no_api_key_sends_no_header(patch_httpx):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json={"items": []})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    client.get_equipment_families()

    assert "x-internal-api-key" not in captured["headers"]


def test_api_key_sent_as_header(patch_httpx):
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, json={"items": []})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid", api_key="supersecret")

    client.get_equipment_families()

    assert captured["headers"]["x-internal-api-key"] == "supersecret"


def test_401_raises_auth_error(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"detail": "Missing or invalid internal API key."})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterAuthError):
        client.get_equipment_families()


def test_403_raises_auth_error(patch_httpx):
    def handler(request):
        return httpx.Response(403, json={"detail": "forbidden"})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterAuthError):
        client.get_equipment_families()


def test_404_raises_not_found(patch_httpx):
    def handler(request):
        return httpx.Response(404, json={"detail": "not found"})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterNotFoundError):
        client.get_equipment_node(999)


def test_422_raises_validation_error(patch_httpx):
    def handler(request):
        return httpx.Response(422, text="bad section codes")

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterValidationError):
        client.update_equipment_mapping(1, ["M1-HR"], "E1")


def test_500_raises_unavailable(patch_httpx):
    def handler(request):
        return httpx.Response(500, text="internal error")

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterUnavailableError):
        client.get_equipment_families()


def test_connection_error_raises_unavailable(patch_httpx):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid")

    with pytest.raises(LocoMasterUnavailableError):
        client.get_equipment_families()


def test_key_never_appears_in_exception_text(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"detail": "nope"})

    patch_httpx(handler)
    client = LocoMasterClient(base_url="http://loco-master.invalid", api_key="supersecret")

    with pytest.raises(LocoMasterAuthError) as exc_info:
        client.get_equipment_families()

    assert "supersecret" not in str(exc_info.value)
