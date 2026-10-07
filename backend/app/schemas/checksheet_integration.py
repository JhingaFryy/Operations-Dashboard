"""Dashboard-owned response schemas for the BL-DCMS read-only checksheet
integration (Phase 4). Deliberately not a passthrough of BL-DCMS's own
integration schemas — narrower field set, Dashboard's own naming, and an
explicit `available` flag so a caller can always tell "BL-DCMS reachable,
zero checksheets" apart from "BL-DCMS unavailable" without inspecting an
HTTP status code. See app/services/checksheet_integration_service.py.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChecksheetIntegrationItem(BaseModel):
    id: int
    template_id: int
    template_name: Optional[str] = None
    section: Optional[str] = None
    equipment: Optional[str] = None
    loco: Optional[str] = None
    schedule_family: Optional[str] = None
    schedule_variant: Optional[str] = None
    workflow_stage_type: Optional[str] = None
    status: str
    # True exactly when BL-DCMS reports this checksheet's status as APPROVED - BL-DCMS's own
    # authoritative "signed/final" signal (see that project's Phase 3 report). Never a second,
    # independently-derived notion of "done".
    approved: bool
    signed: bool
    signing_timestamp: Optional[datetime] = None
    verification_status: Optional[str] = None


class ChecksheetIntegrationListResponse(BaseModel):
    shed_visit_id: int
    source: str = "BLDCMS"
    # False when BL-DCMS could not be reached/authenticated/parsed - `items` is always []
    # in that case, and callers must never read an empty `items` list as "zero checksheets
    # exist" unless `available` is True. See the module docstring.
    available: bool
    items: List[ChecksheetIntegrationItem] = []


class ChecksheetStageSummary(BaseModel):
    workflow_stage_type: str
    total_checksheets: int
    approved_checksheets: int
    pending_checksheets: int


class ChecksheetSummaryResponse(BaseModel):
    shed_visit_id: int
    source: str = "BLDCMS"
    available: bool
    stages: List[ChecksheetStageSummary] = []
