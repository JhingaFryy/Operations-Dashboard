"""HTTP client abstraction for BL-DCMS's read-only integration API.

BL-DCMS owns checksheet templates/submissions/Supervisor review/DSC digital
signing directly — this app must never connect to its database or mutate
its records. All checksheet data crosses the Dashboard/BL-DCMS boundary
over HTTP through this client, mirroring app/clients/loco_master.py's
structure and failure-handling conventions.

Endpoint contract (all paths relative to BLDCMS_BASE_URL; confirmed
against BL-DCMS's own Phase 3 report — app/api/integration.py,
app/schemas/integration.py in that project):

  GET  /integration/checksheets/visit/{shed_visit_id}?workflow_stage_type=<optional>
       -> 200 {"shed_visit_id": int, "items": [{checksheet_id, template_id,
          template_name, section_id, section_name, equipment_id,
          equipment_name, locomotive_id, locomotive_number,
          schedule_family, schedule_variant, workflow_stage_type, status,
          created_at, submitted_at, approved_at, rejected_at, is_final,
          digital_signature: {signed, signing_timestamp,
          verification_status} | null}]}

  GET  /integration/checksheets/visit/{shed_visit_id}/summary
       -> 200 {"shed_visit_id": int, "stages": [{workflow_stage_type,
          total_checksheets, approved_checksheets, pending_checksheets}]}

  GET  /integration/applicability/resolve?loco_type=&schedule_family=&schedule_variant=&workflow_stage_type=
       -> 200 [{applicability_id, template_id, template_name, technology,
          section_id, section_name, equipment_id, equipment_name,
          maintenance_type, schedule_family, schedule_variant,
          workflow_stage_type, is_required}]   (a bare JSON array, not
          wrapped in an object — confirmed against BL-DCMS's own Phase 5A
          report; ordered deterministically by (template_id,
          applicability_id) on BL-DCMS's side, preserved here)

          BL-DCMS Integration Phase 5A.1/5A.2 correction: this endpoint's
          query contract changed from `technology=` to `loco_type=` —
          BL-DCMS now resolves the Loco Master loco_type value (e.g.
          "WAG9HC", "WAP7"/"WAP-7") to its own internal technology
          vocabulary itself, including hyphen/case normalization. This
          client passes Loco Master's loco_type value through verbatim; it
          must never invent or duplicate that mapping locally (see
          checksheet_work_package_service.py, which is the sole caller).

Both routes require `X-Internal-API-Key: <key>` (BLDCMS_INTERNAL_API_KEY)
— BL-DCMS fails closed (401) if it has no key of its own configured, so a
401/403 here almost always means a config mismatch between the two
systems, not a problem with the request itself. Never include shed_visit_id
0 or a locomotive/loco of an unrelated visit in results — BL-DCMS's own
query already filters this; this client trusts and forwards that.

Every failure to reach BL-DCMS (connection error, timeout, 5xx, malformed
JSON/shape) is raised here as BLDCMSUnavailableError; a 401/403 as
BLDCMSAuthError. Both leave callers free to map them to a controlled
"integration unavailable" response rather than silently reporting zero
checksheets — see checksheet_integration_service.py.
"""

from __future__ import annotations

import httpx


class BLDCMSError(Exception):
    """Base class for errors raised by BLDCMSClient."""


class BLDCMSUnavailableError(BLDCMSError):
    """BL-DCMS could not be reached, returned a server error, or returned
    a response this client couldn't parse into the expected shape."""


class BLDCMSAuthError(BLDCMSError):
    """BL-DCMS rejected our internal API key (401/403)."""


class BLDCMSNotFoundError(BLDCMSError):
    """BL-DCMS answered, and the thing asked for does not exist there - today only
    GET /integration/locomotives/{loco_number} defines this case (a locomotive this shed's
    BL-DCMS has no record of). Distinct from BLDCMSUnavailableError: it is a real answer, not a
    transport failure, and the caller must report it as such rather than as an outage."""


class BLDCMSClient:
    def __init__(self, base_url: str, timeout: float = 10.0, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key

    def _headers(self) -> dict:
        if self._api_key:
            return {"X-Internal-API-Key": self._api_key}
        return {}

    def _request(self, method: str, path: str, not_found_is_defined: bool = False, **kwargs) -> httpx.Response:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            response = httpx.request(
                method, f"{self._base_url}{path}", timeout=self._timeout, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            # str(exc) for httpx transport errors never includes request headers, so the API
            # key (sent only in _headers() above, never interpolated into a URL/message) can
            # never leak through this path.
            raise BLDCMSUnavailableError(f"BL-DCMS request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise BLDCMSAuthError(f"BL-DCMS rejected the internal API key ({response.status_code})")
        if response.status_code >= 500:
            raise BLDCMSUnavailableError(f"BL-DCMS returned {response.status_code}")
        # A 404 from these particular routes isn't a defined "not found" case today (BL-DCMS's
        # own visit/summary queries always succeed with an empty result for an unknown visit,
        # per its Phase 3 report) - if BL-DCMS ever does 404 here, treat it the same as any
        # other unexpected status rather than inventing not-found semantics this contract
        # doesn't define.
        if response.status_code == 404 and not_found_is_defined:
            raise BLDCMSNotFoundError("BL-DCMS has no record of that resource")
        if response.status_code != 200:
            raise BLDCMSUnavailableError(f"BL-DCMS returned unexpected status {response.status_code}")

        return response

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise BLDCMSUnavailableError("BL-DCMS returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise BLDCMSUnavailableError("BL-DCMS returned an unexpected response shape")
        return body

    @staticmethod
    def _parse_json_list(response: httpx.Response) -> list[dict]:
        try:
            body = response.json()
        except ValueError as exc:
            raise BLDCMSUnavailableError("BL-DCMS returned a non-JSON response") from exc
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise BLDCMSUnavailableError("BL-DCMS returned an unexpected response shape")
        return body

    def get_visit_checksheets(self, shed_visit_id: int, workflow_stage_type: str | None = None) -> dict:
        params: dict = {}
        if workflow_stage_type is not None:
            params["workflow_stage_type"] = workflow_stage_type
        response = self._request(
            "GET", f"/integration/checksheets/visit/{shed_visit_id}", params=params
        )
        return self._parse_json(response)

    def get_visit_checksheet_summary(self, shed_visit_id: int) -> dict:
        response = self._request("GET", f"/integration/checksheets/visit/{shed_visit_id}/summary")
        return self._parse_json(response)

    def get_minor_section_signoffs(self, shed_visit_id: int) -> dict:
        """Minor Inspection section sign-off state for one visit (BL-DCMS migration 076).

        Answers whether every section that owes required Minor Inspection work for this visit has
        been DSC-signed by its Supervisor. BL owns checksheets, sections and the DSC subsystem, so
        it is the system that can answer this; OD does not keep its own copy of the rule.

        The response distinguishes a pre-cutover visit (section_signoff_workflow=false, apply the
        previous rule) from an unresolvable one (resolved=false, treat as unknown) - see
        checksheet_stage_reconciliation_service for why conflating those is not safe.
        """
        response = self._request(
            "GET",
            f"/integration/checksheets/visit/{shed_visit_id}/minor-inspection/section-signoffs",
        )
        return self._parse_json(response)

    def resolve_major_requirements(self, loco_type: str) -> dict:
        """Shed-wide MAJOR (IOH/TOH) requirement resolution.

        Major has no applicability rows in BL-DCMS, so unlike resolve_applicability() below there
        is no (schedule_family, schedule_variant, workflow_stage_type) to pass - Major is
        equipment-wise and stageless. BL-DCMS runs its own Major resolver across every section
        and returns the union plus the sections it has nothing configured for.
        """
        response = self._request(
            "GET",
            "/integration/applicability/resolve-major",
            params={"loco_type": loco_type},
        )
        return self._parse_json(response)

    def get_minor_inspection_configuration(self, loco_type: str, schedule_variant: str) -> dict:
        """{"all_required_sections_configured": bool, "configured_section_ids": [...], ...} -
        whether the WHOLE Minor Inspection stage is configured for this locomotive's technology."""
        response = self._request(
            "GET",
            "/integration/applicability/minor-inspection-configuration",
            params={"loco_type": loco_type, "schedule_variant": schedule_variant},
        )
        return self._parse_json(response)

    def resolve_applicability(
        self, loco_type: str, schedule_family: str, schedule_variant: str, workflow_stage_type: str
    ) -> list[dict]:
        response = self._request(
            "GET",
            "/integration/applicability/resolve",
            params={
                "loco_type": loco_type,
                "schedule_family": schedule_family,
                "schedule_variant": schedule_variant,
                "workflow_stage_type": workflow_stage_type,
            },
        )
        return self._parse_json_list(response)

    def redeem_operations_handoff(self, handoff_code: str) -> dict | None:
        """Exchange a BL-DCMS handoff code for the employee it was minted for.

        Returns the identity, or None when BL-DCMS refuses it (unknown, already used, expired,
        or a deactivated account - BL deliberately answers all four identically). A refusal is
        a normal outcome here, not a transport failure, so it is None rather than an exception.
        """
        try:
            response = self._request(
                "POST", "/integration/operations-handoff/redeem",
                json={"handoff_code": handoff_code},
            )
        except BLDCMSAuthError:
            # 401/403 from this endpoint is ambiguous: it is how BL reports a bad code AND how
            # it reports a bad internal key. Treat it as a refused code - the alternative is
            # telling the user the system is broken when they simply waited too long.
            return None
        return response.json()

    def get_locomotive_identity(self, loco_number: str) -> dict:
        """{"loco_number", "loco_model", "technology"} - BL-DCMS's authoritative technology for a
        locomotive ('3_PHASE' / 'CONVENTIONAL').

        Why this crosses the boundary at all: Loco Master holds loco_type (WAG9HC, WAP-7, ...) but
        no technology, and BL-DCMS already owns the loco_model -> technology mapping. Re-deriving
        it here would be a second implementation of that rule, which is exactly what this client's
        loco_type contract above exists to avoid. Raises BLDCMSNotFoundError for a locomotive
        BL-DCMS does not know - a real answer, not an outage."""
        response = self._request(
            "GET", f"/integration/locomotives/{loco_number}", not_found_is_defined=True
        )
        return self._parse_json(response)

    # --- Shed visit history (read-only, batched) -------------------------------------------

    def get_locomotive_briefs(
        self, loco_numbers: list[str] | None = None, loco_model: str | None = None
    ) -> list[dict]:
        """[{loco_number, loco_model, technology}] for one page of history rows, or for every
        locomotive of a model. One call either way - never one per row."""
        params: dict = {}
        if loco_numbers:
            params["loco_numbers"] = ",".join(loco_numbers)
        if loco_model:
            params["loco_model"] = loco_model
        response = self._request("GET", "/integration/locomotives", params=params)
        return self._parse_json_list(response)

    def get_locomotive_models(self) -> list[str]:
        response = self._request("GET", "/integration/locomotives/models")
        try:
            body = response.json()
        except ValueError as exc:
            raise BLDCMSUnavailableError("BL-DCMS returned a non-JSON response") from exc
        if not isinstance(body, list) or not all(isinstance(m, str) for m in body):
            raise BLDCMSUnavailableError("BL-DCMS returned an unexpected response shape")
        return body

    def count_visit_checksheets(
        self, shed_visit_ids: list[int], section_id: int | None = None
    ) -> dict[int, int]:
        if not shed_visit_ids:
            return {}
        params: dict = {"shed_visit_ids": ",".join(str(i) for i in shed_visit_ids)}
        if section_id is not None:
            params["section_id"] = section_id
        response = self._request("GET", "/integration/checksheets/visit-counts", params=params)
        rows = self._parse_json_list(response)
        try:
            return {int(r["shed_visit_id"]): int(r["checksheets"]) for r in rows}
        except (KeyError, TypeError, ValueError) as exc:
            raise BLDCMSUnavailableError("BL-DCMS returned an unexpected response shape") from exc

    def get_signed_document(self, shed_visit_id: int, checksheet_id: int) -> bytes:
        """The signed PDF of a checksheet that belongs to THIS visit - bytes only. Raises
        BLDCMSNotFoundError when BL-DCMS has no such checksheet on the visit, or no signed
        document for it. The caller must already have authorized its own user."""
        response = self._request(
            "GET",
            f"/integration/checksheets/visit/{shed_visit_id}/checksheet/{checksheet_id}/signed-document",
            not_found_is_defined=True,
        )
        if not response.headers.get("content-type", "").startswith("application/pdf"):
            raise BLDCMSUnavailableError("BL-DCMS returned something other than a PDF")
        return response.content
