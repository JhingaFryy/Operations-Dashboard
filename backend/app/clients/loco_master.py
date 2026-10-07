"""HTTP client abstraction for Loco Master.

Loco Master owns the `loco` PostgreSQL database (equipment_families,
equipment_nodes, equipment_aliases, equipment_section_map) directly — this
app must never connect to that database. All equipment data crosses the
Dashboard/Loco Master boundary over HTTP through this client.

Loco Master's internal API (/opt/loco-master/api) implements the contract
below — confirmed against its actual routes (api/equipment_routes.py,
api/locomotive_routes.py) and read-only production smoke tests, not just
the earlier speculative writeup:

Endpoint contract (all paths relative to LOCO_MASTER_BASE_URL):

  GET  /api/equipment/families
       -> 200 {"items": [{id, code, name}]}

  GET  /api/equipment/nodes?family_id=<int>&parent_id=<int optional>
       -> 200 {"items": [{id, family_id, parent_id, name, node_type, has_children}]}
       family_id is REQUIRED by Loco Master. parent_id omitted means root nodes.

  GET  /api/equipment/nodes/search?q=<str>&family_id=<int optional>
       -> 200 {"items": [{id, family_id, parent_id, name, node_type, path}]}

  GET  /api/equipment/nodes/{node_id}
       -> 200 {id, family_id, parent_id, name, node_type, source_level, is_active, path}
       -> 404 if the node does not exist

  POST /api/equipment/nodes
       body: {"family_id": int, "parent_id": int|null, "name": str,
              "node_type": str optional (default EQUIPMENT), "description": str|null,
              "actor_employee_id": str}
       -> 201 {id, family_id, parent_id, name, node_type, source_level, is_active, path}
       -> 404 unknown family or parent
       -> 409 a sibling already has that name (case-insensitively)
       -> 422 invalid body, or a parent in a different family
       Loco Master has no user identity: it authorizes the CALLING APPLICATION by
       internal API key and records actor_employee_id for audit. Deciding whether a
       given human may create equipment is this app's job - see app/core/authz.py.

  GET  /api/equipment/nodes/match?family_id=<int>&name=<str>
       -> 200 {"items": [{id, family_id, parent_id, name, node_type, path, section_codes}]}
       Every node in that family whose name matches canonically (trimmed, whitespace
       collapsed, case-insensitive). Returns ALL matches and picks none - the same name
       occurs legitimately throughout this hierarchy under different parents.

  POST /api/equipment/nodes/{node_id}/sections
       body: {"section_codes": [str], "actor_employee_id": str}
       -> 200 {equipment_node_id, section_codes, already_present, newly_added}
       ADDITIVE and idempotent: removes nothing, re-adding an active section is a no-op.
       Deliberately not PUT /mapping, which is replace-set and would unmap any section
       the caller did not list.
       -> 404 unknown node, 422 empty/blank section_codes

  GET  /api/equipment/nodes/{node_id}/mapping
       -> 200 {equipment_node_id, section_codes: [str]}   (direct/exact mapping only,
          active mappings for this exact node; empty list if none)
       -> 404 if the node does not exist

  PUT  /api/equipment/nodes/{node_id}/mapping
       body: {"section_codes": [str], "actor_employee_id": str}
       -> 200 {equipment_node_id, section_codes: [str]}   (replace-set semantics:
          codes not in the request become inactive/removed, codes in the request
          are (re)activated, actor_employee_id is recorded for audit fields)
       -> 404 if the node does not exist
       -> 422 if section_codes is invalid

  GET  /api/locomotives/search?q=<str>&limit=<int optional>
       -> 200 {"items": [{loco_number, loco_type}]}   (active locomotives only)

  GET  /api/locomotives/{loco_number}
       -> 200 {loco_number, loco_type}
       -> 404 if unknown or inactive

  All /api/* routes optionally require `X-Internal-API-Key: <key>` (see
  LOCO_MASTER_INTERNAL_API_KEY) — enforced by Loco Master only when it has
  that env var configured. A 401/403 response here almost always means
  *this app's* key is missing/wrong, not a problem with the request.

All failures to reach Loco Master (connection error, timeout, 5xx) are
raised here as LocoMasterUnavailableError; a 401/403 as LocoMasterAuthError.
Both leave callers free to map them to a 502/503 API response without
leaking transport details.
"""

from __future__ import annotations

import httpx


class LocoMasterError(Exception):
    """Base class for errors raised by LocoMasterClient."""


class LocoMasterUnavailableError(LocoMasterError):
    """Loco Master could not be reached or returned a server error."""


class LocoMasterAuthError(LocoMasterError):
    """Loco Master rejected our internal API key (401/403)."""


class LocoMasterNotFoundError(LocoMasterError):
    """Loco Master reported the requested resource does not exist."""


class LocoMasterValidationError(LocoMasterError):
    """Loco Master rejected the request as invalid (422)."""

    def __init__(self, detail: str | dict):
        super().__init__(detail)
        self.detail = detail


class LocoMasterConflictError(LocoMasterError):
    """Loco Master refused the write because it would duplicate an existing row (409).

    Raised only by create_equipment_node today: equipment names are unique among
    siblings (uq_equipment_node_sibling), case-insensitively. `detail` carries Loco
    Master's structured body, including `existing_node_id`.
    """

    def __init__(self, detail: str | dict):
        super().__init__(detail)
        self.detail = detail


def _detail_of(response: httpx.Response) -> str | dict:
    """FastAPI errors are {"detail": ...}; fall back to the raw body for anything else.

    A dict detail is carried through as a dict - Loco Master's 409 names the existing
    node, and flattening that to text would throw away the only thing that lets the
    caller offer to reuse it. Only the detail crosses the boundary: never headers,
    never the API key.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, (str, dict)):
        return detail
    return response.text


class LocoMasterClient:
    def __init__(self, base_url: str, timeout: float = 10.0, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key

    def _headers(self) -> dict:
        if self._api_key:
            return {"X-Internal-API-Key": self._api_key}
        return {}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            response = httpx.request(
                method, f"{self._base_url}{path}", timeout=self._timeout, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise LocoMasterUnavailableError(str(exc)) from exc

        if response.status_code in (401, 403):
            raise LocoMasterAuthError(
                f"Loco Master rejected the internal API key ({response.status_code})"
            )
        if response.status_code == 404:
            raise LocoMasterNotFoundError(f"{path} not found")
        if response.status_code == 409:
            raise LocoMasterConflictError(_detail_of(response))
        if response.status_code == 422:
            raise LocoMasterValidationError(_detail_of(response))
        if response.status_code >= 500:
            raise LocoMasterUnavailableError(f"Loco Master returned {response.status_code}")

        response.raise_for_status()
        return response

    @staticmethod
    def _unwrap_items(response: httpx.Response) -> list[dict]:
        """Loco Master wraps list responses as {"items": [...]}. Tolerates a
        bare array too, so a future Loco Master change back to that shape
        wouldn't silently break every caller."""
        body = response.json()
        if isinstance(body, dict):
            return body.get("items", [])
        return body

    def get_equipment_families(self) -> list[dict]:
        return self._unwrap_items(self._request("GET", "/api/equipment/families"))

    def get_root_equipment(self, family_id: int) -> list[dict]:
        return self.get_equipment_children(node_id=None, family_id=family_id)

    def get_equipment_children(self, node_id: int | None, family_id: int) -> list[dict]:
        params: dict = {"family_id": family_id}
        if node_id is not None:
            params["parent_id"] = node_id
        return self._unwrap_items(self._request("GET", "/api/equipment/nodes", params=params))

    def search_equipment(self, query: str, family_id: int | None = None) -> list[dict]:
        params: dict = {"q": query}
        if family_id is not None:
            params["family_id"] = family_id
        return self._unwrap_items(self._request("GET", "/api/equipment/nodes/search", params=params))

    def get_equipment_node(self, node_id: int) -> dict:
        return self._request("GET", f"/api/equipment/nodes/{node_id}").json()

    def create_equipment_node(
        self,
        *,
        family_id: int,
        parent_id: int | None,
        name: str,
        node_type: str | None = None,
        description: str | None = None,
        actor_employee_id: str,
    ) -> dict:
        body: dict = {
            "family_id": family_id,
            "parent_id": parent_id,
            "name": name,
            "actor_employee_id": actor_employee_id,
        }
        if node_type is not None:
            body["node_type"] = node_type
        if description is not None:
            body["description"] = description
        return self._request("POST", "/api/equipment/nodes", json=body).json()

    def update_equipment_node(
        self,
        node_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        description_provided: bool = False,
        is_active: bool | None = None,
        actor_employee_id: str,
    ) -> dict:
        """PATCH one node's own metadata in Loco Master. Partial: only what is passed is sent.

        `description_provided` carries the "clear it" intent across the wire. Sending
        description=None and omitting description entirely mean different things to Loco Master,
        and only an explicit flag can distinguish them here - a plain `if description is not
        None` would make a description impossible to clear.

        Node MOVEMENT is not expressible: there is no parent_id or family_id parameter, matching
        the Loco Master endpoint, which ignores them.
        """
        body: dict = {"actor_employee_id": actor_employee_id}
        if name is not None:
            body["name"] = name
        if description_provided:
            body["description"] = description
        if is_active is not None:
            body["is_active"] = is_active
        return self._request("PATCH", f"/api/equipment/nodes/{node_id}", json=body).json()

    def match_equipment_by_name(self, family_id: int, name: str) -> list[dict]:
        params = {"family_id": family_id, "name": name}
        return self._unwrap_items(
            self._request("GET", "/api/equipment/nodes/match", params=params)
        )

    def add_equipment_sections(
        self, node_id: int, section_codes: list[str], actor_employee_id: str
    ) -> dict:
        body = {"section_codes": section_codes, "actor_employee_id": actor_employee_id}
        return self._request(
            "POST", f"/api/equipment/nodes/{node_id}/sections", json=body
        ).json()

    def get_equipment_mapping(self, node_id: int) -> dict:
        return self._request("GET", f"/api/equipment/nodes/{node_id}/mapping").json()

    def update_equipment_mapping(
        self, node_id: int, section_codes: list[str], actor_employee_id: str
    ) -> dict:
        body = {"section_codes": section_codes, "actor_employee_id": actor_employee_id}
        return self._request("PUT", f"/api/equipment/nodes/{node_id}/mapping", json=body).json()

    def search_locomotives(self, query: str, limit: int | None = None) -> list[dict]:
        params: dict = {"q": query}
        if limit is not None:
            params["limit"] = limit
        return self._unwrap_items(self._request("GET", "/api/locomotives/search", params=params))

    def get_locomotive(self, loco_number: str) -> dict:
        return self._request("GET", f"/api/locomotives/{loco_number}").json()
