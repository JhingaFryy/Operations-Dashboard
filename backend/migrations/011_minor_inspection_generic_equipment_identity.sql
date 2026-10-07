-- 011_minor_inspection_generic_equipment_identity.sql
--
-- OD side of BL-DCMS migration 030 (Minor Inspection generalized beyond M2-HR).
--
-- 1. minor_inspection_equipment_code_snapshot VARCHAR(10) -> VARCHAR(40): section-local synthetic
--    codes such as HB_CUBICLE_1 (M2-HR's printed letters still fit).
-- 2. minor_inspection_equipment_label_snapshot: the human label BL-DCMS computed when the package was
--    generated ("A - VCD" for a printed code, otherwise the equipment name). NULL on rows snapshotted
--    before this migration - consumers fall back to code/name for those.
--
-- Satisfaction semantics for Minor Inspection requirements become equipment-level in application code
-- (one requirement per equipment; any of its alternative performas satisfies it), which the existing
-- columns already express - no further schema change. Additive, no row changes meaning.

BEGIN;

ALTER TABLE shed_visit_checksheet_requirements
    ALTER COLUMN minor_inspection_equipment_code_snapshot TYPE VARCHAR(40);
ALTER TABLE shed_visit_checksheet_requirements
    ADD COLUMN minor_inspection_equipment_label_snapshot VARCHAR(260) NULL;
ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_checksheet_requirement_minor_inspection_label
    CHECK (minor_inspection_equipment_label_snapshot IS NULL OR minor_inspection_equipment_id IS NOT NULL);

COMMIT;
