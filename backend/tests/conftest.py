import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("RDCMS_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("LOCO_MASTER_BASE_URL", "http://loco-master.invalid")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, pwd_context
from app.db import models
from app.db.session import get_db
from app.main import app
from app.services.bldcms_client import get_bldcms_client
from app.services.loco_master_client import get_loco_master_client

# A disposable, in-memory SQLite database — never the real RDCMS. Its
# schema is built once per test session from app.db.models' metadata purely
# for test isolation; production code never calls create_all against RDCMS.
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_engine, "connect")
def _enable_fk(dbapi_connection, _):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


class MockLocoMasterClient:
    """Stands in for the not-yet-built Loco Master HTTP API in tests.
    Implements the same surface as app.clients.loco_master.LocoMasterClient
    against an in-memory tree instead of real HTTP calls."""

    def __init__(self):
        # Both production families, with Loco Master's own ids and codes.
        self.families = [
            {"id": 1, "code": "3PHASE", "name": "3-Phase", "description": None},
            {"id": 2, "code": "CONVENTIONAL", "name": "Conventional", "description": None},
        ]
        self.nodes: dict[int, dict] = {}
        self.mappings: dict[int, list[str]] = {}
        self.locomotives: dict[str, dict] = {}
        #: Audit trail of create_equipment_node calls, so tests can assert WHO created what.
        self.created_nodes: list[dict] = []
        #: Audit trail of add_equipment_sections calls.
        self.section_additions: list[dict] = []
        self.unavailable = False
        self.auth_error = False

    def add_locomotive(self, loco_number, loco_type="WAG9HC", is_active=True):
        self.locomotives[loco_number] = {
            "loco_number": loco_number,
            "loco_type": loco_type,
            "is_active": is_active,
        }

    def search_locomotives(self, query, limit=None):
        self._check_available()
        q = query.lower()
        matches = [
            {"loco_number": l["loco_number"], "loco_type": l["loco_type"]}
            for l in self.locomotives.values()
            if l["is_active"] and q in l["loco_number"].lower()
        ]
        return matches[:limit] if limit else matches

    def get_locomotive(self, loco_number):
        self._check_available()
        from app.clients.loco_master import LocoMasterNotFoundError

        loco = self.locomotives.get(loco_number)
        if loco is None or not loco["is_active"]:
            raise LocoMasterNotFoundError(f"locomotive {loco_number} not found")
        return {"loco_number": loco["loco_number"], "loco_type": loco["loco_type"]}

    def add_node(self, node_id, family_id, parent_id, name, node_type="EQUIPMENT"):
        self.nodes[node_id] = {
            "id": node_id,
            "family_id": family_id,
            "parent_id": parent_id,
            "name": name,
            "node_type": node_type,
            "description": None,
            "has_children": False,
        }
        if parent_id is not None and parent_id in self.nodes:
            self.nodes[parent_id]["has_children"] = True

    def set_mapping(self, node_id, section_codes):
        self.mappings[node_id] = list(section_codes)

    def _check_available(self):
        if self.unavailable:
            from app.clients.loco_master import LocoMasterUnavailableError

            raise LocoMasterUnavailableError("mock: unavailable")
        if self.auth_error:
            from app.clients.loco_master import LocoMasterAuthError

            raise LocoMasterAuthError("mock: auth rejected")

    def get_equipment_families(self):
        self._check_available()
        return self.families

    def get_equipment_children(self, node_id=None, family_id=None):
        self._check_available()
        return [
            n
            for n in self.nodes.values()
            if n["parent_id"] == node_id and (family_id is None or n["family_id"] == family_id)
        ]

    def get_root_equipment(self, family_id):
        return self.get_equipment_children(None, family_id)

    def _path_for(self, node):
        chain = [node]
        current = node
        while current["parent_id"] is not None:
            current = self.nodes[current["parent_id"]]
            chain.append(current)
        chain.reverse()
        return [{"id": n["id"], "name": n["name"]} for n in chain]

    def search_equipment(self, query, family_id=None):
        self._check_available()
        q = query.lower()
        return [
            {**n, "path": self._path_for(n)}
            for n in self.nodes.values()
            if q in n["name"].lower() and (family_id is None or n["family_id"] == family_id)
        ]

    def get_equipment_node(self, node_id):
        self._check_available()
        from app.clients.loco_master import LocoMasterNotFoundError

        node = self.nodes.get(node_id)
        if node is None:
            raise LocoMasterNotFoundError(f"node {node_id} not found")
        return node

    def get_equipment_mapping(self, node_id):
        self._check_available()
        from app.clients.loco_master import LocoMasterNotFoundError

        if node_id not in self.nodes:
            raise LocoMasterNotFoundError(f"node {node_id} not found")
        return {"equipment_node_id": node_id, "section_codes": self.mappings.get(node_id, [])}

    def create_equipment_node(
        self,
        *,
        family_id,
        parent_id,
        name,
        node_type=None,
        description=None,
        actor_employee_id,
    ):
        """Mirrors Loco Master's POST /api/equipment/nodes, including the duplicate rule
        the production index enforces: uq_equipment_node_sibling on
        (family_id, COALESCE(parent_id, 0), lower(name)) - unique among SIBLINGS,
        case-insensitively, never globally."""
        self._check_available()
        from app.clients.loco_master import (
            LocoMasterConflictError,
            LocoMasterNotFoundError,
            LocoMasterValidationError,
        )

        if not any(f["id"] == family_id for f in self.families):
            raise LocoMasterNotFoundError("family not found")
        if parent_id is not None:
            parent = self.nodes.get(parent_id)
            if parent is None:
                raise LocoMasterNotFoundError(f"node {parent_id} not found")
            if parent["family_id"] != family_id:
                raise LocoMasterValidationError("parent belongs to a different family")

        # Loco Master stores the whitespace-collapsed form, so "Traction  Motor" and
        # "Traction Motor" are the same equipment.
        cleaned = " ".join(name.split())
        for node in self.nodes.values():
            if (
                node["family_id"] == family_id
                and node["parent_id"] == parent_id
                and node["name"].lower() == cleaned.lower()
            ):
                raise LocoMasterConflictError(
                    {
                        "code": "DUPLICATE_EQUIPMENT",
                        "message": f"Equipment named '{node['name']}' already exists at this level",
                        "existing_node_id": node["id"],
                    }
                )

        node_id = max(self.nodes, default=0) + 1
        self.add_node(node_id, family_id, parent_id, cleaned, node_type or "EQUIPMENT")
        self.created_nodes.append({"id": node_id, "actor_employee_id": actor_employee_id})
        return self.nodes[node_id]

    def match_equipment_by_name(self, family_id, name):
        """Mirrors Loco Master's GET /api/equipment/nodes/match: every ACTIVE node in one
        family whose name matches canonically (trimmed, whitespace collapsed, case-folded).
        Returns all of them and picks none."""
        self._check_available()
        target = " ".join(name.split()).casefold()
        return [
            {
                **n,
                "path": self._path_for(n),
                "section_codes": sorted(self.mappings.get(n["id"], [])),
            }
            for n in self.nodes.values()
            if n["family_id"] == family_id and " ".join(n["name"].split()).casefold() == target
        ]

    def add_equipment_sections(self, node_id, section_codes, actor_employee_id):
        """Mirrors POST /api/equipment/nodes/{id}/sections - additive and idempotent,
        removing nothing and never creating a second row for the same section."""
        self._check_available()
        from app.clients.loco_master import LocoMasterNotFoundError

        if node_id not in self.nodes:
            raise LocoMasterNotFoundError(f"node {node_id} not found")
        current = list(self.mappings.get(node_id, []))
        already_present = [c for c in section_codes if c in current]
        newly_added = [c for c in dict.fromkeys(section_codes) if c not in current]
        self.mappings[node_id] = sorted({*current, *section_codes})
        self.section_additions.append(
            {"node_id": node_id, "newly_added": newly_added, "actor_employee_id": actor_employee_id}
        )
        return {
            "equipment_node_id": node_id,
            "section_codes": self.mappings[node_id],
            "already_present": already_present,
            "newly_added": newly_added,
        }

    def update_equipment_node(
        self,
        node_id,
        *,
        name=None,
        description=None,
        description_provided=False,
        is_active=None,
        actor_employee_id,
    ):
        """Mirrors Loco Master's PATCH /api/equipment/nodes/{id}, including the sibling
        duplicate rule and the refusal to move a node (no parent/family parameter exists)."""
        self._check_available()
        from app.clients.loco_master import LocoMasterConflictError, LocoMasterNotFoundError

        node = self.nodes.get(node_id)
        if node is None:
            raise LocoMasterNotFoundError(f"node {node_id} not found")

        if name is not None:
            cleaned = " ".join(name.split())
            for other in self.nodes.values():
                if (
                    other["id"] != node_id
                    and other["family_id"] == node["family_id"]
                    and other["parent_id"] == node["parent_id"]
                    and other["name"].lower() == cleaned.lower()
                ):
                    raise LocoMasterConflictError(
                        {
                            "code": "DUPLICATE_EQUIPMENT",
                            "message": (
                                f"Equipment named '{other['name']}' already exists at this level"
                            ),
                            "existing_node_id": other["id"],
                        }
                    )
            node["name"] = cleaned
        if description_provided:
            node["description"] = description
        if is_active is not None:
            node["is_active"] = bool(is_active)
        return {**node, "path": self._path_for(node)}

    def fail_mapping_updates(self):
        """Makes the next mapping write fail, for the partial-failure path."""
        self._mapping_update_fails = True

    def update_equipment_mapping(self, node_id, section_codes, actor_employee_id):
        self._check_available()
        from app.clients.loco_master import LocoMasterNotFoundError, LocoMasterValidationError

        if getattr(self, "_mapping_update_fails", False):
            raise LocoMasterValidationError("mapping rejected")
        if node_id not in self.nodes:
            raise LocoMasterNotFoundError(f"node {node_id} not found")
        self.mappings[node_id] = list(dict.fromkeys(section_codes))
        return {"equipment_node_id": node_id, "section_codes": self.mappings[node_id]}


class MockBLDCMSClient:
    """Stands in for app.clients.bldcms.BLDCMSClient in tests — same
    public surface, backed by an in-memory per-visit store instead of real
    HTTP calls. Data is stored in BL-DCMS's own raw JSON shape (matching
    that project's IntegrationChecksheetItem/IntegrationVisitSummaryResponse)
    so checksheet_integration_service's normalization logic is exercised
    the same way it would be against the real API."""

    def __init__(self):
        self.visits: dict[int, list[dict]] = {}
        self.unavailable = False
        self.auth_error = False
        self.malformed = False
        # Phase 5B.1: keyed by (technology, schedule_family, schedule_variant,
        # workflow_stage_type) -> list of raw applicability items, matching BL-DCMS's own
        # /integration/applicability/resolve response shape exactly.
        self.applicability: dict[tuple[str, str, str, str], list[dict]] = {}
        self._next_applicability_id = 1
        # Operational Control phase: keyed by loco_type -> BL-DCMS's own
        # /integration/applicability/resolve-major response shape.
        self.major_requirements: dict[str, list[dict]] = {}
        self.major_unconfigured_sections: list[str] = []
        # BL-DCMS is the authority for a locomotive's technology (Loco Master has loco_type only).
        # Unknown here means "BL-DCMS has no such locomotive"; the default keeps every existing
        # fixture 3-phase, matching the 3PHASE equipment those tests use.
        self.locomotive_technologies: dict[str, str] = {}
        # Shed visit history: loco_number -> model, and (visit_id, checksheet_id) -> PDF bytes.
        self.locomotive_models: dict[str, str] = {}
        self.signed_documents: dict[tuple[int, int], bytes] = {}
        self.signed_document_requests: list[tuple[int, int]] = []
        self.default_technology: str | None = "3_PHASE"
        #: Handoff codes BL-DCMS would consider valid, code -> identity. Popped on redemption.
        self.handoff_codes: dict[str, dict] = {}
        self.handoff_redemptions: list[str] = []
        #: Minor Inspection section sign-off state per visit (BL-DCMS migration 076).
        #: A visit absent from this dict is PRE-CUTOVER - section_signoff_workflow=false - which
        #: is what every visit in production is until BL-DCMS marks it, and what keeps every test
        #: written before the sign-off workflow existed meaningful and unchanged.
        self.minor_section_signoffs: dict[int, dict] = {}

    def add_checksheet(self, shed_visit_id: int, **fields):
        item = {
            "checksheet_id": 1,
            "template_id": 1,
            "template_name": "Template",
            "section_id": 1,
            "section_name": "M4-HR",
            "equipment_id": None,
            "equipment_name": None,
            "locomotive_id": 1,
            "locomotive_number": "39126",
            "schedule_family": "MINOR",
            "schedule_variant": "IA",
            "workflow_stage_type": "TEST_BEFORE",
            "maintenance_type": None,
            "status": "DRAFT",
            "created_at": "2026-01-01T00:00:00",
            "submitted_at": None,
            "approved_at": None,
            "rejected_at": None,
            "is_final": False,
            "digital_signature": None,
        }
        item.update(fields)
        self.visits.setdefault(shed_visit_id, []).append(item)
        return item

    def _check_available(self):
        if self.unavailable:
            from app.clients.bldcms import BLDCMSUnavailableError

            raise BLDCMSUnavailableError("mock: unavailable")
        if self.auth_error:
            from app.clients.bldcms import BLDCMSAuthError

            raise BLDCMSAuthError("mock: auth rejected")

    # --- Shed visit history --------------------------------------------------------------
    def get_locomotive_briefs(self, loco_numbers=None, loco_model=None):
        self._check_available()
        rows = [
            {"loco_number": n, "loco_model": m,
             "technology": self.locomotive_technologies.get(n, self.default_technology)}
            for n, m in sorted(self.locomotive_models.items())
        ]
        if loco_numbers:
            rows = [r for r in rows if r["loco_number"] in loco_numbers]
        if loco_model:
            norm = lambda v: (v or "").replace("-", "").replace(" ", "").upper()  # noqa: E731
            rows = [r for r in rows if norm(r["loco_model"]) == norm(loco_model)]
        return rows

    def get_locomotive_models(self):
        self._check_available()
        return sorted(set(self.locomotive_models.values()))

    def count_visit_checksheets(self, shed_visit_ids, section_id=None):
        self._check_available()
        return {
            v: len([i for i in self.visits.get(v, []) if section_id is None or i.get("section_id") == section_id])
            for v in shed_visit_ids
        }

    def get_signed_document(self, shed_visit_id, checksheet_id):
        self._check_available()
        from app.clients.bldcms import BLDCMSNotFoundError

        key = (shed_visit_id, checksheet_id)
        if key not in self.signed_documents:
            raise BLDCMSNotFoundError("no signed document")
        self.signed_document_requests.append(key)
        return self.signed_documents[key]

    def set_locomotive_technology(self, loco_number, technology):
        """technology=None makes this locomotive unknown to BL-DCMS."""
        self.locomotive_technologies[loco_number] = technology

    def redeem_operations_handoff(self, handoff_code):
        """Mirrors BL-DCMS's POST /integration/operations-handoff/redeem, including its
        single-use semantics: a code is spent the moment it is redeemed, and BL answers the
        same way for unknown, spent and expired."""
        self._check_available()
        identity = self.handoff_codes.pop(handoff_code, None)
        self.handoff_redemptions.append(handoff_code)
        return identity

    def issue_handoff_code(self, code, employee_id, role="Supervisor"):
        """Test helper - stands in for a code BL-DCMS minted for this employee."""
        self.handoff_codes[code] = {"employee_id": employee_id, "role": role}
        return code

    def get_locomotive_identity(self, loco_number):
        self._check_available()
        technology = self.locomotive_technologies.get(loco_number, self.default_technology)
        if technology is None:
            from app.clients.bldcms import BLDCMSNotFoundError

            raise BLDCMSNotFoundError(f"locomotive {loco_number} not found")
        return {"loco_number": loco_number, "loco_model": "WAG9HC", "technology": technology}

    def get_minor_section_signoffs(self, shed_visit_id):
        self._check_available()
        state = self.minor_section_signoffs.get(shed_visit_id)
        if state is None:
            return {
                "shed_visit_id": shed_visit_id,
                "section_signoff_workflow": False,
                "resolved": True,
                "sections_total": 0,
                "sections_signed": 0,
                "all_sections_signed": False,
                "sections": [],
            }
        return state

    def set_minor_section_signoffs(
        self, shed_visit_id, *, sections_total, sections_signed, resolved=True,
    ):
        """Test helper - opts a visit into the section sign-off workflow with the given progress."""
        self.minor_section_signoffs[shed_visit_id] = {
            "shed_visit_id": shed_visit_id,
            "section_signoff_workflow": True,
            "resolved": resolved,
            "sections_total": sections_total,
            "sections_signed": sections_signed,
            "all_sections_signed": sections_total > 0 and sections_signed == sections_total,
            "sections": [],
        }

    def get_visit_checksheets(self, shed_visit_id, workflow_stage_type=None):
        self._check_available()
        if self.malformed:
            # A bare missing "items" key would still be tolerated by .get("items", [])
            # downstream, so force a real shape violation to exercise the TypeError path.
            return {"items": "not-a-list"}
        items = self.visits.get(shed_visit_id, [])
        if workflow_stage_type is not None:
            items = [i for i in items if i.get("workflow_stage_type") == workflow_stage_type]
        return {"shed_visit_id": shed_visit_id, "items": items}

    def get_visit_checksheet_summary(self, shed_visit_id):
        self._check_available()
        if self.malformed:
            return {"stages": "not-a-list"}
        items = self.visits.get(shed_visit_id, [])
        by_stage: dict[str, list[dict]] = {}
        for item in items:
            by_stage.setdefault(item["workflow_stage_type"], []).append(item)
        stages = []
        for stage_type in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
            stage_items = by_stage.get(stage_type)
            if not stage_items:
                continue
            approved = sum(1 for i in stage_items if i.get("is_final"))
            stages.append(
                {
                    "workflow_stage_type": stage_type,
                    "total_checksheets": len(stage_items),
                    "approved_checksheets": approved,
                    "pending_checksheets": len(stage_items) - approved,
                }
            )
        return {"shed_visit_id": shed_visit_id, "stages": stages}

    def add_applicability(
        self, technology, schedule_family="MINOR", schedule_variant="IA", workflow_stage_type="TEST_BEFORE", **fields
    ):
        item = {
            "applicability_id": self._next_applicability_id,
            "template_id": self._next_applicability_id,
            "template_name": "Template",
            "technology": technology,
            "section_id": 1,
            "section_name": "M4-HR",
            "equipment_id": None,
            "equipment_name": None,
            "maintenance_type": None,
            "schedule_family": schedule_family,
            "schedule_variant": schedule_variant,
            "workflow_stage_type": workflow_stage_type,
            "is_required": True,
        }
        item.update(fields)
        self._next_applicability_id += 1
        key = (technology, schedule_family, schedule_variant, workflow_stage_type)
        self.applicability.setdefault(key, []).append(item)
        return item

    def add_major_requirement(self, loco_type="WAG9HC", **fields):
        item = {
            "section_id": 1,
            "section_name": "M4-HR",
            "template_id": 1,
            "template_name": "Major Template",
            "technology": "3_PHASE",
            "equipment_id": None,
            "equipment_name": None,
            "maintenance_type": None,
            "source": "MAJOR_EQUIPMENT",
            "is_required": True,
        }
        item.update(fields)
        self.major_requirements.setdefault(loco_type, []).append(item)
        return item

    def resolve_major_requirements(self, loco_type):
        self._check_available()
        if self.malformed:
            return {"requirements": [{"missing": "required keys"}]}
        return {
            "loco_type": loco_type,
            "technology": "3_PHASE",
            "requirements": list(self.major_requirements.get(loco_type, [])),
            "unconfigured_sections": list(self.major_unconfigured_sections),
        }

    def get_minor_inspection_configuration(self, loco_type, schedule_variant):
        self._check_available()
        return {
            "loco_type": loco_type,
            "schedule_variant": schedule_variant,
            "all_required_sections_configured": bool(getattr(self, "minor_inspection_configuration_complete", False)),
            "configured_section_ids": [],
        }

    def resolve_applicability(self, loco_type, schedule_family, schedule_variant, workflow_stage_type):
        # BL-DCMS Integration Phase 5A.1/5A.2: the real client now sends loco_type=, not
        # technology=. This mock's fixture store is still keyed by the string tests configure via
        # add_applicability(technology=...) - existing tests always set the mock locomotive's
        # loco_type equal to that same string (e.g. "3_PHASE"), so the lookup key matches without
        # any change to the fixture store's shape.
        self._check_available()
        if self.malformed:
            return [{"missing": "required keys"}]
        key = (loco_type, schedule_family, schedule_variant, workflow_stage_type)
        return list(self.applicability.get(key, []))


@pytest.fixture(autouse=True)
def _clear_handoff_bootstrap():
    """In-memory bootstrap state is per-process, so it would otherwise leak between tests."""
    from app.services import handoff_bootstrap

    handoff_bootstrap.reset_for_tests()
    yield
    handoff_bootstrap.reset_for_tests()


@pytest.fixture()
def db_session():
    models.Base.metadata.create_all(bind=_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        models.Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def mock_loco_client():
    return MockLocoMasterClient()


@pytest.fixture()
def mock_bldcms_client():
    return MockBLDCMSClient()


@pytest.fixture()
def client(db_session, mock_loco_client, mock_bldcms_client):
    def _get_db_override():
        yield db_session

    def _get_loco_client_override():
        return mock_loco_client

    def _get_bldcms_client_override():
        return mock_bldcms_client

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_loco_master_client] = _get_loco_client_override
    app.dependency_overrides[get_bldcms_client] = _get_bldcms_client_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_allow_500(db_session, mock_loco_client, mock_bldcms_client):
    """Same overrides as `client`, but with raise_server_exceptions=False —
    needed to assert on the 500 response of a simulated internal failure
    instead of having the exception re-raised into the test process. That
    flag is read from the TestClient instance at construction time, not
    settable afterward, so it needs its own fixture rather than a toggle on
    `client`."""

    def _get_db_override():
        yield db_session

    def _get_loco_client_override():
        return mock_loco_client

    def _get_bldcms_client_override():
        return mock_bldcms_client

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_loco_master_client] = _get_loco_client_override
    app.dependency_overrides[get_bldcms_client] = _get_bldcms_client_override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def make_section(db_session, id, code, name=None):
    section = models.Section(id=id, code=code, name=name or code, created_at=datetime.now(timezone.utc))
    db_session.add(section)
    db_session.commit()
    return section


def make_user(
    db_session,
    id,
    employee_id,
    name,
    role,
    password_hash,
    is_active=True,
    section_id=None,
):
    user = models.User(
        id=id,
        employee_id=employee_id,
        name=name,
        mobile=f"90000000{id:02d}",
        email=None,
        password_hash=password_hash,
        role=role,
        is_active=is_active,
        section_id=section_id,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.commit()
    return user


def grant_access(
    db_session,
    user_id,
    is_enabled=True,
    can_add_booking_sections=False,
    can_manage_equipment_mapping=False,
    can_route_bookings=False,
    granted_by=None,
):
    access = models.DashboardAccess(
        user_id=user_id,
        is_enabled=is_enabled,
        can_add_booking_sections=can_add_booking_sections,
        can_manage_equipment_mapping=can_manage_equipment_mapping,
        # Migration 015. Defaults to False here exactly as in the database, so no existing
        # fixture silently gains routing.
        can_route_bookings=can_route_bookings,
        granted_by=granted_by,
        granted_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(access)
    db_session.commit()
    return access


def make_defect_type(db_session, id, code, name=None, is_active=True, sort_order=0):
    defect_type = models.BookingDefectType(
        id=id,
        code=code,
        name=name or code,
        is_active=is_active,
        sort_order=sort_order,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(defect_type)
    db_session.commit()
    return defect_type


def make_shed_visit(
    db_session,
    id,
    loco_number,
    status="IN_SHED",
    schedule_family="MINOR",
    schedule_variant="IA",
    arrival_condition="WORKING",
    created_by=None,
    arrival_at=None,
    departed_at=None,
    departure_source=None,
):
    # Callers that pass departed_at usually build it from their own datetime.now(), which can
    # land a few microseconds BEFORE this default arrival - incoherent, and now correctly
    # rejected by chk_shed_visit_timestamp_order. Anchoring arrival before any supplied
    # departure keeps such fixtures valid without weakening the constraint.
    resolved_arrival = arrival_at or datetime.now(timezone.utc)
    if departed_at is not None and departed_at < resolved_arrival:
        resolved_arrival = departed_at - timedelta(hours=1)

    visit = models.ShedVisit(
        id=id,
        loco_number=loco_number,
        arrival_at=resolved_arrival,
        arrival_source="DASHBOARD",
        schedule_family=schedule_family,
        schedule_variant=schedule_variant,
        visit_type="SCHEDULED",
        status=status,
        arrival_condition=arrival_condition,
        departed_at=departed_at,
        departure_source=departure_source,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
        updated_by=created_by,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(visit)
    db_session.commit()
    return visit


def make_booking(
    db_session,
    id,
    shed_visit_id,
    equipment_node_id=1843,
    defect_type_id=None,
    description="Test booking",
    created_by=None,
    stage_id=None,
    booking_source="LOG_BOOK",
    status="OPEN",
):
    booking = models.Booking(
        id=id,
        shed_visit_id=shed_visit_id,
        stage_id=stage_id,
        booking_source=booking_source,
        description=description,
        equipment_node_id=equipment_node_id,
        defect_type_id=defect_type_id,
        status=status,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
        updated_by=created_by,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(booking)
    db_session.commit()
    return booking


def make_stage(
    db_session,
    id,
    shed_visit_id,
    stage_type,
    stage_order,
    status="PENDING",
    started_at=None,
    started_by=None,
    completed_at=None,
    completed_by=None,
    skipped_at=None,
    skipped_by=None,
    skip_reason=None,
):
    # chk_stage_skipped (mirrored in the model) requires skipped_at on a SKIPPED stage.
    if status == "SKIPPED" and skipped_at is None:
        skipped_at = datetime.now(timezone.utc)
    stage = models.ShedVisitStage(
        id=id,
        shed_visit_id=shed_visit_id,
        stage_type=stage_type,
        stage_order=stage_order,
        status=status,
        started_at=started_at,
        started_by=started_by,
        completed_at=completed_at,
        completed_by=completed_by,
        skipped_at=skipped_at,
        skipped_by=skipped_by,
        skip_reason=skip_reason,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(stage)
    db_session.commit()
    return stage


def make_minor_stages(db_session, shed_visit_id, base_id):
    """Seeds the standard 3 active Minor Schedule stages, all PENDING,
    matching what Shed In creates automatically (Phase 3D: SPECIAL_CHECKING
    was removed from the active workflow by business decision). Returns
    them in stage_order. See make_minor_stages_with_legacy_special_checking
    below for tests that specifically need a historical 4th stage row."""
    stage_types = ["TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"]
    return [
        make_stage(db_session, base_id + i, shed_visit_id, stage_type, i + 1)
        for i, stage_type in enumerate(stage_types)
    ]


def make_minor_stages_with_legacy_special_checking(db_session, shed_visit_id, base_id):
    """Seeds all 4 stages including a legacy SPECIAL_CHECKING row, for
    tests confirming that historical data with this stage still reads
    correctly (it's simply no longer created for new visits)."""
    stage_types = ["TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER", "SPECIAL_CHECKING"]
    return [
        make_stage(db_session, base_id + i, shed_visit_id, stage_type, i + 1)
        for i, stage_type in enumerate(stage_types)
    ]


def _assignment_timestamps(status, now):
    """Lifecycle timestamps a booking_section_assignments row in `status` must
    carry to be a state the real database would actually accept.

    The live rdcms schema constrains these (chk_booking_section_started,
    chk_booking_section_attended, plus chk_booking_section_attended_requires_
    start / chk_booking_section_reopened_requires_attend added by migration
    003), and app/db/models.py now mirrors all of them so the SQLite test
    schema enforces them too. Seeding e.g. an ATTENDED assignment with a NULL
    attended_at used to "work" in tests purely because the model carried no
    CHECK constraints — such a row could never have existed in production. This
    derives the timestamps from the status instead, so fixtures build only
    reachable states:

        OPEN        -> neither
        IN_PROGRESS -> started_at            (reached via start)
        ATTENDED    -> started_at, attended_at (reachable only via IN_PROGRESS)
        REOPENED    -> started_at, attended_at (reachable only via ATTENDED)
    """
    started_at = now if status in ("IN_PROGRESS", "ATTENDED", "REOPENED") else None
    attended_at = now if status in ("ATTENDED", "REOPENED") else None
    return started_at, attended_at


def make_assignment(db_session, id, booking_id, section_id, status="OPEN"):
    now = datetime.now(timezone.utc)
    started_at, attended_at = _assignment_timestamps(status, now)
    assignment = models.BookingSectionAssignment(
        id=id,
        booking_id=booking_id,
        section_id=section_id,
        assignment_source="AUTO_MAPPING",
        status=status,
        assigned_at=now,
        started_at=started_at,
        attended_at=attended_at,
        updated_at=now,
    )
    db_session.add(assignment)
    db_session.commit()
    return assignment


def set_assignment_status(db_session, assignment, status):
    """Moves an existing fixture assignment to `status`, keeping its lifecycle
    timestamps consistent with what the DB constraints require (see
    _assignment_timestamps). Use this instead of assigning .status directly —
    a bare `assignment.status = "ATTENDED"` produces a row the live PostgreSQL
    schema would reject."""
    now = datetime.now(timezone.utc)
    started_at, attended_at = _assignment_timestamps(status, now)
    assignment.status = status
    if started_at is not None and assignment.started_at is None:
        assignment.started_at = started_at
    if attended_at is not None and assignment.attended_at is None:
        assignment.attended_at = attended_at
    assignment.updated_at = now
    db_session.commit()
    return assignment


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def auth_header(employee_id, role, user_id):
    token = create_access_token({"sub": employee_id, "user_id": user_id, "role": role})
    return {"Authorization": f"Bearer {token}"}


def make_non_blocking_checksheet_package(db_session, shed_visit_id, package_id=None, stage="SCHEDULE_INSPECTION"):
    """A generated requirement snapshot whose single row is Optional, so the Operational Control
    checksheet gate is satisfied.

    Shed Out now blocks on checksheet requirements as well as bookings and stages. Tests whose
    subject is the BOOKING gate (or stage gate) need the checksheet gate out of the way without
    disabling it - an ungenerated package is itself a blocker, by design, so "no package" is not
    a neutral state. A generated package carrying only non-blocking rows is the neutral state.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    package = models.ShedVisitChecksheetPackage(
        id=package_id or (shed_visit_id * 100 + 1),
        shed_visit_id=shed_visit_id,
        generated_by=None,
        generated_at=now,
    )
    db_session.add(package)
    db_session.commit()
    db_session.add(
        models.ShedVisitChecksheetRequirement(
            package_id=package.id,
            # SCHEDULE_INSPECTION, not TEST_BEFORE: since migration 012 Test Before / Test After rows
            # are always required, so only a normal Inspection row can be the neutral Optional one.
            # MAJOR callers pass stage=None - Major requirements are stageless.
            workflow_stage_type=stage,
            # The DB CHECK keeps the two models coherent: a MINOR applicability row carries both
            # applicability_id and a stage; a stageless MAJOR row carries neither.
            applicability_id=1 if stage else None,
            template_id=1,
            requirement_source="APPLICABILITY" if stage else "MAJOR_EQUIPMENT",
            is_required=False,   # Optional -> never blocks
            is_active=True,
            template_name_snapshot="Optional Template",
            technology_snapshot="3_PHASE",
            section_id_snapshot=None,
            section_name_snapshot=None,
            equipment_id_snapshot=None,
            equipment_name_snapshot=None,
            maintenance_type_snapshot=None,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    return package


# Operations Dashboard is Supervisor-only as of the production-hardening phase. These helpers
# build the two identities every test needs, so no test has to restate the entitlement plumbing.

def ensure_section(db_session, id, code, name=None):
    """make_section, but idempotent on the unique `code`. Needed now that the shared movement
    Supervisor fixture may already have created the same section a test wants to build."""
    existing = (
        db_session.query(models.Section)
        .filter((models.Section.code == code) | (models.Section.id == id))
        .first()
    )
    if existing is not None:
        return existing
    return make_section(db_session, id=id, code=code, name=name)


MOVEMENT_SECTION_ID = 900
MOVEMENT_SECTION_CODE = "SHIFT"


def make_movement_supervisor_headers(
    db_session, user_id=1, employee_id="SHIFTSUP", section_id=MOVEMENT_SECTION_ID
):
    """An entitled Supervisor in a locomotive-movement section (SHIFT).

    This is the identity that replaced the old `_admin_headers` in workflow tests: under the new
    policy the account that may drive the whole shed workflow is a movement Supervisor, not an
    Admin - Admin is now an explicit, separate technical path that Supervisors never inherit.
    """
    # Reuse a movement section the test already created (several build their own "SHIFT"), since
    # sections.code is unique - only create one when none exists.
    existing = (
        db_session.query(models.Section)
        .filter(models.Section.code == MOVEMENT_SECTION_CODE)
        .first()
    )
    if existing is None:
        existing = make_section(
            db_session, id=section_id, code=MOVEMENT_SECTION_CODE, name=MOVEMENT_SECTION_CODE
        )
    section_id = existing.id
    user = db_session.get(models.User, user_id)
    if user is None:
        make_user(db_session, user_id, employee_id, "Shift Supervisor", "Supervisor", "hash",
                  section_id=section_id)
    else:
        user.role = "Supervisor"
        user.section_id = section_id
        db_session.commit()
    if db_session.query(models.DashboardAccess).filter_by(user_id=user_id).first() is None:
        grant_access(db_session, user_id, is_enabled=True,
                     can_add_booking_sections=True, can_manage_equipment_mapping=True)
    return auth_header(employee_id, "Supervisor", user_id)


def make_section_supervisor_headers(
    db_session, user_id, section_id, employee_id="SECSUP", is_enabled=True, **access
):
    """An entitled Supervisor in an ordinary (non-movement) section."""
    user = db_session.get(models.User, user_id)
    if user is None:
        make_user(db_session, user_id, employee_id, "Section Supervisor", "Supervisor", "hash",
                  section_id=section_id)
    if db_session.query(models.DashboardAccess).filter_by(user_id=user_id).first() is None:
        grant_access(db_session, user_id, is_enabled=is_enabled, **access)
    return auth_header(employee_id, "Supervisor", user_id)


def make_true_admin_headers(db_session, user_id=99, employee_id="TRUEADM"):
    """A genuine Admin.

    Admin is no longer an operational identity: it cannot use the Supervisor-only Operations
    Dashboard endpoints. It remains the explicit technical path for the few deliberately
    Admin-only capabilities (the global booking pool read, reopening an ATTENDED assignment,
    dashboard-access administration), which no Supervisor inherits.
    """
    if db_session.get(models.User, user_id) is None:
        make_user(db_session, user_id, employee_id, "Technical Admin", "Admin", "hash")
    return auth_header(employee_id, "Admin", user_id)


_checksheet_booking_seq = {"n": 0}


def make_checksheet_booking(
    db_session,
    mock_loco_client,
    shed_visit_id,
    booking_source,
    equipment_node_id=1843,
    defect_type_id=1,
    remarks="Checksheet finding",
    checksheet_id=1,
    technician_employee_id=None,
    client_booking_id=None,
    mock_bldcms_client=None,
):
    """A TEST_BEFORE / TEST_AFTER booking as it now reaches the Dashboard: raised on the Android
    checksheet and relayed by BL-DCMS. Goes through the real internal creation service (origin check,
    stage check, routing, CREATED/AUTO_ROUTED events) - the Dashboard itself can no longer create one."""
    from app.schemas.internal_bookings import InternalBookingCreateRequest
    from app.services.internal_booking_service import create_internal_booking

    _checksheet_booking_seq["n"] += 1
    if booking_source == "SCHEDULE_INSPECTION":
        # No client may create these any more; tests of reading/gating HISTORICAL rows seed them
        # through the shared creation core directly (routing, events and stage link unchanged).
        from datetime import datetime, timezone

        from app.services.booking_creation_service import create_booking

        stage = (
            db_session.query(models.ShedVisitStage)
            .filter_by(shed_visit_id=shed_visit_id, stage_type="SCHEDULE_INSPECTION")
            .first()
        )
        booking, _ = create_booking(
            db_session, mock_loco_client, shed_visit_id=shed_visit_id, stage_id=stage.id if stage else None,
            booking_source="SCHEDULE_INSPECTION", equipment_node_id=equipment_node_id,
            defect_type_id=defect_type_id, description=remarks, actor=None, now=datetime.now(timezone.utc),
        )
        db_session.commit()
        return booking
    payload = InternalBookingCreateRequest(
        client_booking_id=client_booking_id or f"test-{shed_visit_id}-{booking_source}-{_checksheet_booking_seq['n']}",
        shed_visit_id=shed_visit_id,
        equipment_node_id=equipment_node_id,
        booking_source=booking_source,
        defect_type_id=defect_type_id,
        description=remarks,
        checksheet_id=checksheet_id,
        technician_employee_id=technician_employee_id,
    )
    # The visit locomotive's equipment family is derived from BL-DCMS's technology, so this seam
    # needs the BL-DCMS double too; by default it answers 3_PHASE, matching the 3PHASE equipment
    # these fixtures use.
    return create_internal_booking(
        db_session, mock_loco_client, payload,
        bldcms_client=mock_bldcms_client or MockBLDCMSClient(),
    )
