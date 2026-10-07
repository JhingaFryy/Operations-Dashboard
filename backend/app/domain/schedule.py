"""Single source of truth for the shed-visit schedule domain: which
(schedule_family, schedule_variant) pairs are valid, and how to render one
for a human.

Phase: MAJOR (IOH/TOH) support alongside the existing MINOR (IA/IA0/IB/IC/IC0)
schedules. schedule_family and schedule_variant remain two separate
columns/fields everywhere in this codebase (see ShedVisit and
ShedInRequest) — this module never collapses them into one combined
string like "MINOR IA"; that only happens, deliberately, in
display_schedule() for human-facing text.

Nothing here implements Major checksheet applicability/requirements —
that is out of scope (see checksheet_work_package_service.require_minor,
which still limits Minor Schedule to work-package generation). This
module only says which family/variant *pairs are legal to store on a shed
visit*, not what workflow, if any, applies to them afterwards.
"""

SCHEDULE_MATRIX: dict[str, frozenset[str]] = {
    # IA0/IC0 are distinct Minor variants, not aliases of IA/IC: never normalized, never given
    # IA/IC applicability. Until BL-DCMS has applicability for them, work-package generation
    # honestly fails NO_APPLICABILITY_CONFIGURED and Shed Out stays blocked.
    "MINOR": frozenset({"IA", "IA0", "IB", "IC", "IC0"}),
    "MAJOR": frozenset({"IOH", "TOH"}),
}

VALID_SCHEDULE_FAMILIES = frozenset(SCHEDULE_MATRIX)
VALID_SCHEDULE_VARIANTS = frozenset(v for variants in SCHEDULE_MATRIX.values() for v in variants)


def is_valid_schedule_pair(family: str, variant: str) -> bool:
    return variant in SCHEDULE_MATRIX.get(family, frozenset())


def validate_schedule_pair(family: str, variant: str) -> None:
    """Raises ValueError with a message suitable for a pydantic validator
    if (family, variant) is not one of the pairs in SCHEDULE_MATRIX."""
    if family not in SCHEDULE_MATRIX:
        raise ValueError(
            f"Unsupported schedule_family {family!r}; expected one of "
            f"{sorted(VALID_SCHEDULE_FAMILIES)}."
        )
    if variant not in SCHEDULE_MATRIX[family]:
        raise ValueError(
            f"Unsupported schedule_variant {variant!r} for schedule_family {family!r}; "
            f"expected one of {sorted(SCHEDULE_MATRIX[family])}."
        )


def display_schedule(family: str | None, variant: str | None) -> str:
    """Human-facing label for a schedule: just the variant (e.g. "IA",
    "TOH"). schedule_family is never shown — it's an internal grouping,
    not something a person needs to see. Callers still pass it (rather
    than a variant-only signature) so this stays the one place that ever
    reasons about the pairing, and so a missing/blank variant with a
    present family still renders sensibly."""
    if not family and not variant:
        return "—"
    return variant or "—"
