-- 006_visit_requirement_operational_control.sql
--
-- Turns the existing per-visit checksheet requirement snapshot into the authoritative model for
-- the operational "Pending Checksheets" control panel.
--
-- WHY THIS TABLE AND NOT A NEW ONE
-- shed_visit_checksheet_requirements (migration 001) is already exactly the concept the panel
-- needs: a frozen, per-visit snapshot of "what this visit requires", generated once at Shed In
-- and deliberately immune to later master-configuration changes. Introducing a second
-- requirement table in BL-DCMS would duplicate that ownership. So this migration extends it
-- rather than replacing it. Everything here is ADDITIVE - no column is dropped, no row is
-- deleted, and every existing row keeps its current meaning.
--
-- WHAT CHANGES
--   1. is_active / updated_at / changed_by  - per-visit Deactivate + its audit trail. Global
--      applicability (BL-DCMS checksheet_template_applicability.is_active/is_required) is NEVER
--      touched by the panel; these columns are where operational overrides live instead.
--   2. requirement_source                    - which resolver produced the row.
--   3. applicability_id becomes NULLable     - MAJOR has no applicability rows at all (its
--      requirements come from section_equipment_map + section templates), so the old NOT NULL
--      made Major impossible to snapshot.
--   4. workflow_stage_type becomes NULLable  - MAJOR is equipment-wise and has no
--      TB/Inspection/TA stages. The CHECK is rewritten to allow NULL, and to require a stage
--      exactly when the row came from the MINOR applicability model.
--   5. The identity constraint is widened from (package, stage, applicability_id) - which can
--      only express Minor - to the full requirement identity including template, section,
--      equipment and maintenance_type.

BEGIN;

-- 1. Operational-control columns -------------------------------------------------------------
ALTER TABLE shed_visit_checksheet_requirements
    ADD COLUMN IF NOT EXISTS is_active   boolean     NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS updated_at  timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS changed_by  integer     NULL REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS requirement_source varchar(30) NOT NULL DEFAULT 'APPLICABILITY';

COMMENT ON COLUMN shed_visit_checksheet_requirements.is_active IS
    'Per-visit Deactivate. False = not expected for THIS visit; never blocks completion, hidden '
    'from the default pending panel, never deleted. Global applicability is unaffected.';
COMMENT ON COLUMN shed_visit_checksheet_requirements.is_required IS
    'Per-visit Mark Optional. False = still performable and still shown (badged Optional), but '
    'does not block visit/checksheet completion. Global applicability is unaffected.';
COMMENT ON COLUMN shed_visit_checksheet_requirements.changed_by IS
    'User who last changed is_required/is_active for this visit. NULL = never overridden.';

-- 2. MAJOR support: these two are meaningless for equipment-wise Major work ---------------------
ALTER TABLE shed_visit_checksheet_requirements ALTER COLUMN applicability_id   DROP NOT NULL;
ALTER TABLE shed_visit_checksheet_requirements ALTER COLUMN workflow_stage_type DROP NOT NULL;

ALTER TABLE shed_visit_checksheet_requirements
    DROP CONSTRAINT IF EXISTS chk_checksheet_requirement_workflow_stage_type;

ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_checksheet_requirement_workflow_stage_type
    CHECK (
        workflow_stage_type IS NULL
        OR workflow_stage_type IN ('TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER')
    );

ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_checksheet_requirement_source
    CHECK (requirement_source IN (
        'APPLICABILITY',            -- MINOR, from BL-DCMS checksheet_template_applicability
        'MAJOR_EQUIPMENT',          -- MAJOR Pattern A: one template per mapped equipment
        'MAJOR_EQUIPMENT_CHOICE',   -- MAJOR Pattern C: equipment + maintenance_type alternatives
        'MAJOR_DIRECT'              -- MAJOR Pattern B: section-level template, no equipment
    ));

-- A MINOR applicability row must carry both of its identifying dimensions; a MAJOR row must
-- carry neither. This is what stops a half-populated row of either kind being written.
ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_checksheet_requirement_model_coherent
    CHECK (
        (requirement_source = 'APPLICABILITY'
            AND applicability_id IS NOT NULL AND workflow_stage_type IS NOT NULL)
        OR
        (requirement_source <> 'APPLICABILITY'
            AND applicability_id IS NULL AND workflow_stage_type IS NULL)
    );

-- 3. Full requirement identity ------------------------------------------------------------------
-- The old constraint could only ever express a Minor applicability decision. The real identity of
-- a requirement - and the key the panel matches checksheet instances on - is the visit plus
-- template, section, equipment, stage and maintenance_type. NULLS NOT DISTINCT is essential:
-- without it two MAJOR_DIRECT rows (equipment NULL, stage NULL, maintenance_type NULL) for the
-- same template would both be allowed, because NULLs would compare as distinct.
-- Created by migration 001 as a table constraint (SQLAlchemy Index(unique=True) on an existing
-- table constraint name), so it must be dropped as a CONSTRAINT - DROP INDEX is refused while the
-- constraint still owns it.
ALTER TABLE shed_visit_checksheet_requirements
    DROP CONSTRAINT IF EXISTS uq_checksheet_requirement_identity;
DROP INDEX IF EXISTS uq_checksheet_requirement_identity;

CREATE UNIQUE INDEX uq_checksheet_requirement_identity
    ON shed_visit_checksheet_requirements (
        package_id,
        template_id,
        workflow_stage_type,
        section_id_snapshot,
        equipment_id_snapshot,
        maintenance_type_snapshot
    ) NULLS NOT DISTINCT;

CREATE INDEX IF NOT EXISTS ix_checksheet_requirements_active
    ON shed_visit_checksheet_requirements (package_id, is_active, is_required);

COMMIT;
