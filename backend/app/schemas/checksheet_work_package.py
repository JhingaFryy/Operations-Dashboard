from datetime import datetime

from pydantic import BaseModel


class ChecksheetRequirementOut(BaseModel):
    applicability_id: int
    template_id: int
    template_name: str
    technology: str
    section_id: int | None
    section_name: str | None
    equipment_id: int | None
    equipment_name: str | None
    maintenance_type: str | None
    minor_inspection_equipment_id: int | None = None
    minor_inspection_equipment_code: str | None = None
    minor_inspection_equipment_name: str | None = None
    minor_inspection_equipment_label: str | None = None
    is_required: bool


class ChecksheetRequirementStageOut(BaseModel):
    workflow_stage_type: str
    requirements: list[ChecksheetRequirementOut]


class ChecksheetWorkPackageOut(BaseModel):
    shed_visit_id: int
    # False when no package has ever been generated for this visit - distinct from a generated
    # package (which, by construction, always has all three stages populated: see
    # checksheet_work_package_service.py's "EMPTY APPLICABILITY" handling — generation itself
    # refuses to create a package unless every one of TEST_BEFORE/SCHEDULE_INSPECTION/TEST_AFTER
    # resolved at least one applicability row).
    generated: bool
    generated_by: int | None = None
    generated_at: datetime | None = None
    minor_inspection_configuration_complete: bool | None = None
    stages: list[ChecksheetRequirementStageOut] = []


class MajorChecksheetWorkPackageOut(BaseModel):
    """Result of generating a MAJOR (IOH/TOH) visit's work package.

    Deliberately NOT ChecksheetWorkPackageOut: that shape groups requirements under the three
    MINOR workflow stages, and a stageless Major package would render as three empty stages (see
    checksheet_work_package_service.generate_major_work_package). Major requirements are read
    through the operational pending-requirements endpoint; this response only reports that the
    snapshot now exists and how large it is.
    """

    shed_visit_id: int
    generated: bool
    requirement_count: int
