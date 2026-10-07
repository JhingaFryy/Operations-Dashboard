-- BL-DCMS Integration Phase 5B.1: materialized checksheet work packages.
--
-- Operations Dashboard-owned tables, coexisting in the same `rdcms` Postgres instance BL-DCMS's
-- own tables live in - exactly like this app's existing dashboard_access table (see
-- app/db/models.py's module docstring). Nothing here touches, alters, or references a
-- BL-DCMS-owned table.
--
-- Why a snapshot at all: BL-DCMS's checksheet_template_applicability is mutable configuration -
-- an Admin can edit/deactivate a mapping at any time. A shed visit's checksheet requirements
-- must NOT silently change because someone edited BL-DCMS configuration after the visit's
-- requirements were already generated - see the Phase 5B.1 report's "Architecture" section.
-- So generation reads BL-DCMS once, and every field below is frozen at that moment - re-reading
-- BL-DCMS later must never be able to mutate an existing row.
--
-- Deliberately NO foreign key to anything in BL-DCMS's database - applicability_id/template_id
-- are external identifiers, trusted the same way Loco Master-sourced ids already are elsewhere
-- in this codebase (e.g. bookings.equipment_node_id), not referential-integrity-checked against
-- a database this app has no connection to and no migration authority over.
--
-- Header (shed_visit_checksheet_packages) + detail (shed_visit_checksheet_requirements) rather
-- than one flat table: a package is generated at most once per visit (see
-- app/services/checksheet_work_package_service.py's idempotency docstring - repeated generation
-- returns the existing snapshot unchanged, it never re-generates), so "has this visit been
-- generated, by whom, when" belongs on a single header row, not repeated across what could be a
-- dozen+ requirement rows. Mirrors this schema's own shed_visits -> shed_visit_stages shape.
--
-- workflow_stage_type is deliberately constrained to the three active Minor workflow stages
-- (TEST_BEFORE, SCHEDULE_INSPECTION, TEST_AFTER) - no SPECIAL_CHECKING, matching the same
-- Phase 3D scope correction already applied to shed_visit_stages/bookings.

CREATE TABLE IF NOT EXISTS shed_visit_checksheet_packages (
    id BIGSERIAL PRIMARY KEY,
    shed_visit_id BIGINT NOT NULL REFERENCES shed_visits(id) ON DELETE CASCADE,
    generated_by INTEGER REFERENCES users(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_checksheet_package_shed_visit UNIQUE (shed_visit_id)
);

CREATE TABLE IF NOT EXISTS shed_visit_checksheet_requirements (
    id BIGSERIAL PRIMARY KEY,
    package_id BIGINT NOT NULL REFERENCES shed_visit_checksheet_packages(id) ON DELETE CASCADE,
    workflow_stage_type VARCHAR(30) NOT NULL,

    -- External BL-DCMS identifiers - intentionally NOT foreign keys. Both retained (not just
    -- template_id) so a work-package row can always identify the exact applicability decision
    -- that produced it, even after that decision is later edited/deactivated in BL-DCMS.
    applicability_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,

    is_required BOOLEAN NOT NULL,

    -- Snapshotted human-readable context, frozen at generation time.
    template_name_snapshot VARCHAR(200) NOT NULL,
    technology_snapshot VARCHAR(30) NOT NULL,
    section_id_snapshot INTEGER,
    section_name_snapshot VARCHAR(100),
    equipment_id_snapshot INTEGER,
    equipment_name_snapshot VARCHAR(100),
    maintenance_type_snapshot VARCHAR(30),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_checksheet_requirement_workflow_stage_type
        CHECK (workflow_stage_type IN ('TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER')),
    -- Prevents generating the same BL-DCMS applicability requirement twice for the same visit
    -- and stage. No separate UNIQUE on (package_id, workflow_stage_type, template_id): BL-DCMS's
    -- own uq_applicability_identity constraint already guarantees at most one applicability row
    -- exists per (template_id, schedule_family, schedule_variant, workflow_stage_type) - so for
    -- the one fixed schedule_variant a whole package is generated against, a given template_id
    -- can only ever arrive here via one applicability_id in the first place. See the Phase 5B.1
    -- report for the full reasoning behind not adding a redundant second constraint.
    CONSTRAINT uq_checksheet_requirement_identity
        UNIQUE (package_id, workflow_stage_type, applicability_id)
);

CREATE INDEX IF NOT EXISTS ix_checksheet_requirements_package_id
    ON shed_visit_checksheet_requirements (package_id);
