-- 010_minor_inspection_equipment_identity.sql
--
-- Operations Dashboard side of BL-DCMS migration 028 (Minor Inspection equipment directory).
--
-- 1. shed_visit_checksheet_requirements gets the NEW Minor Inspection equipment identity as its own
--    columns - never overloaded into equipment_id_snapshot (which stays the LEGACY equipment id and
--    is NULL for these rows). The code/name snapshots keep a frozen requirement understandable even
--    if the directory entry is later renamed. Coherence: all three set or all three NULL, and when
--    set the row is a SCHEDULE_INSPECTION applicability requirement with no legacy equipment.
--
-- 2. shed_visit_checksheet_packages.minor_inspection_configuration_complete snapshots, per package,
--    whether BL-DCMS declared EVERY required section of the technology's Minor Inspection
--    configured. NULL for MAJOR packages (and legacy rows, which are treated as not complete).
--    Stage reconciliation never completes SCHEDULE_INSPECTION unless this is TRUE, so finishing
--    the only configured section (e.g. M2-HR) can never complete the Inspection stage.
--
-- Additive only: no existing row changes meaning. uq_checksheet_requirement_identity is unchanged
-- (each Minor Inspection equipment has its own template_id, so identities already differ).

BEGIN;

ALTER TABLE shed_visit_checksheet_requirements
    ADD COLUMN minor_inspection_equipment_id INTEGER NULL,
    ADD COLUMN minor_inspection_equipment_code_snapshot VARCHAR(10) NULL,
    ADD COLUMN minor_inspection_equipment_name_snapshot VARCHAR(200) NULL;

ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_checksheet_requirement_minor_inspection_equipment
    CHECK (
        (minor_inspection_equipment_id IS NULL
         AND minor_inspection_equipment_code_snapshot IS NULL
         AND minor_inspection_equipment_name_snapshot IS NULL)
        OR
        (minor_inspection_equipment_id IS NOT NULL
         AND minor_inspection_equipment_code_snapshot IS NOT NULL
         AND minor_inspection_equipment_name_snapshot IS NOT NULL
         AND equipment_id_snapshot IS NULL
         AND requirement_source IS NOT NULL AND requirement_source = 'APPLICABILITY'
         AND workflow_stage_type IS NOT NULL AND workflow_stage_type = 'SCHEDULE_INSPECTION')
    );

ALTER TABLE shed_visit_checksheet_packages
    ADD COLUMN minor_inspection_configuration_complete BOOLEAN NULL;

COMMIT;
