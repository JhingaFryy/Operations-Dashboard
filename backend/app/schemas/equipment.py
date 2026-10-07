from pydantic import BaseModel, Field, field_validator


class EquipmentFamily(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None


class EquipmentNode(BaseModel):
    id: int
    family_id: int
    parent_id: int | None
    name: str
    node_type: str
    description: str | None = None
    has_children: bool = False


class EquipmentPathItem(BaseModel):
    id: int
    name: str


class EquipmentNodeSearchResult(EquipmentNode):
    """Search results carry ancestry (root -> node) so the UI can tell
    apart same-named nodes living under different parents. Loco Master's
    search endpoint already returns this `path`; the plain EquipmentNode
    model used elsewhere deliberately omits it since e.g. /nodes/{id} and
    the children listing don't need it."""

    path: list[EquipmentPathItem] = []


class EquipmentMappingOut(BaseModel):
    equipment_node_id: int
    section_codes: list[str]


class ResolvedSectionsOut(BaseModel):
    equipment_node_id: int
    resolved_from_node_id: int | None
    resolution: str  # EXACT | ANCESTOR | NONE
    section_codes: list[str]


class EquipmentNodeCreated(EquipmentNode):
    """A newly created node plus the sections it was mapped to, so the caller can select it
    immediately without a second round trip."""

    path: list[EquipmentPathItem] = []
    section_codes: list[str] = []


class AdminEquipmentNodeUpdateRequest(BaseModel):
    """Edit an EXISTING equipment node from the Equipment Responsibility Mapping page. ADMIN only.

    PARTIAL: only the fields present are written. A dialog that changes a name must not silently
    blank a description it never displayed.

    THERE IS NO family_code AND NO parent_id, on purpose. Moving a node rewrites every
    descendant's path, changes which siblings its name must be unique among, and changes how
    historical bookings render their equipment - that is a separate feature with a separate blast
    radius, not something "edit this equipment" should be able to do by accident.

    actor_employee_id is NOT accepted from the browser. The Operations Dashboard derives it from
    the authenticated Admin and forwards it to Loco Master server-to-server.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    is_active: bool | None = None
    #: Replace-set. When present, this becomes the node's COMPLETE section mapping - any section
    #: not listed is removed. Omit it to leave the mapping untouched. The dialog must therefore
    #: load the full current mapping before sending one.
    section_codes: list[str] | None = None


class AdminEquipmentNodeCreateRequest(BaseModel):
    """Create equipment from the Equipment Responsibility Mapping page.

    This is the ONLY way equipment is created. The Shed In booking form used to have its
    own narrower request (a locomotive and a name, always creating a root); that route is
    withdrawn, because creating equipment is master-data administration rather than part of
    recording a defect. An administrator is looking at the hierarchy itself and says WHERE
    the equipment belongs - which family, and under which parent - because that placement
    decides both its identity (uniqueness is per family and parent) and, through ancestor
    resolution, how its bookings route.
    """

    family_code: str = Field(..., min_length=1)
    #: None creates a root. Otherwise the new node's parent, which must be in `family_code`.
    parent_id: int | None = None
    name: str = Field(..., min_length=1, max_length=200)
    section_codes: list[str] = Field(default_factory=list)

    @field_validator("family_code", "name")
    @classmethod
    def admin_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("section_codes")
    @classmethod
    def admin_sections_not_blank(cls, value: list[str]) -> list[str]:
        for code in value:
            if not code or not code.strip():
                raise ValueError("section_codes must not contain blank values")
        return value


class EquipmentNodeMatch(EquipmentNode):
    """An existing node whose name matches what the user typed, with everything needed to
    tell it apart from its namesakes."""

    path: list[EquipmentPathItem] = []
    section_codes: list[str] = []


class EquipmentSectionAddRequest(BaseModel):
    section_codes: list[str] = Field(..., min_length=1)

    @field_validator("section_codes")
    @classmethod
    def add_not_blank(cls, value: list[str]) -> list[str]:
        for code in value:
            if not code or not code.strip():
                raise ValueError("section_codes must not contain blank values")
        return value


class EquipmentSectionAddResponse(BaseModel):
    equipment_node_id: int
    section_codes: list[str]
    already_present: list[str]
    newly_added: list[str]


class EquipmentMappingUpdateRequest(BaseModel):
    section_codes: list[str]

    @field_validator("section_codes")
    @classmethod
    def not_blank(cls, value: list[str]) -> list[str]:
        for code in value:
            if not code or not code.strip():
                raise ValueError("section_codes must not contain blank values")
        return value
