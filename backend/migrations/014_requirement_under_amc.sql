-- 014_requirement_under_amc.sql
--
-- "Under AMC": a per-visit requirement is maintained by outside Firm Staff under an Annual
-- Maintenance Contract, so workshop technicians are not responsible for its checksheet.
--
-- Forward-only, additive. Adds five columns to the existing per-visit snapshot and writes no data:
-- every existing row gets under_amc = false, which is exactly its present behaviour.
--
-- ------------------------------------------------------------------------------------------------
-- WHY HERE, AND NOT ANYWHERE ELSE
-- ------------------------------------------------------------------------------------------------
-- The production audit (diagnostics/under_amc_audit.sql) settled the model with two measurements.
--
--   * Section 4: of 2,735 requirements, 914 ACTIVE ones have no checksheet_header row at all - 788
--     MINOR and 126 MAJOR. That is not an anomaly, it is what the Pending Checksheets panel is for:
--     work nobody has started yet. So AMC cannot be a checksheet status, a value, or anything hanging
--     off checksheet_header. It has to live on the requirement.
--   * Section 6b: 7 templates serve 2,735 requirements across 31 visits. A flag on
--     checksheet_templates would therefore mark that equipment AMC for every locomotive at once,
--     which is precisely what the requirement forbids: the same equipment may be AMC on visit A and
--     workshop-maintained on visit B.
--
-- shed_visit_checksheet_requirements is already the per-visit override model - it carries is_required
-- (Optional) and is_active (Deactivated), reaches its visit through package_id, and is written
-- through one service function. Extending it keeps all three states in one row, so no query can read
-- one and miss another.
--
-- ------------------------------------------------------------------------------------------------
-- THREE STATES, NEVER COLLAPSED
-- ------------------------------------------------------------------------------------------------
--   is_active = false                      -> DEACTIVATED : not expected on this visit at all
--   is_required = false                    -> OPTIONAL    : may be done, never blocks
--   under_amc = true                       -> UNDER AMC   : applicable, but Firm Staff own it
--
-- They mean different things and are reported separately. The CHECK below makes the one genuinely
-- contradictory pair impossible at the storage layer rather than merely unlikely: a DEACTIVATED
-- requirement cannot also be UNDER AMC, because "not expected at all" and "expected, by someone
-- else" cannot both be true of the same row.
--
-- OPTIONAL + AMC is NOT blocked by a constraint, because it is not contradictory - merely pointless,
-- since an Optional row already blocks nothing. The service refuses the transition so the panel never
-- shows two badges, but the database does not need to.
--
-- ------------------------------------------------------------------------------------------------
-- WHAT THIS DOES NOT DO
-- ------------------------------------------------------------------------------------------------
-- It does not touch the frozen package's identity. The requirement stays in the visit's package with
-- its template, section and equipment snapshots unchanged, so clearing AMC restores it exactly, and
-- the history still shows it was originally applicable. AMC is a responsibility layer on top of a
-- frozen requirement, never a rewrite of it.

BEGIN;

ALTER TABLE shed_visit_checksheet_requirements
    -- NOT NULL DEFAULT false: every existing row keeps its current behaviour, and no query has to
    -- cope with a third, unknown state.
    ADD COLUMN IF NOT EXISTS under_amc       BOOLEAN NOT NULL DEFAULT false,
    -- Attribution for both directions. Marked_* survives after clearing, so the history shows who
    -- marked it as well as who cleared it; they are not a single mutable pair.
    ADD COLUMN IF NOT EXISTS amc_marked_by   INTEGER REFERENCES users (id),
    ADD COLUMN IF NOT EXISTS amc_marked_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS amc_cleared_by  INTEGER REFERENCES users (id),
    ADD COLUMN IF NOT EXISTS amc_cleared_at  TIMESTAMPTZ;

COMMENT ON COLUMN shed_visit_checksheet_requirements.under_amc IS
    'TRUE when this requirement is maintained by Firm Staff under an Annual Maintenance Contract for '
    'THIS visit only. Excluded from completion gates and from the technician work list, never '
    'presented as submitted, approved or signed. Distinct from is_required (Optional) and is_active '
    '(Deactivated).';

-- A deactivated requirement cannot also be under AMC: "not expected on this visit" and "expected,
-- but Firm Staff own it" are mutually exclusive claims about the same row. Deactivating an AMC
-- requirement therefore clears AMC in the same statement (see pending_requirement_service), and this
-- constraint is what guarantees no code path can skip that.
ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_requirement_amc_not_deactivated
        CHECK (NOT (under_amc AND NOT is_active));

-- Marked/cleared stamps must come in pairs, so a row can never claim an actor without a time or the
-- reverse - the same completeness rule migration 076 applies to a signature.
ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_requirement_amc_marked_pair
        CHECK ((amc_marked_by IS NULL) = (amc_marked_at IS NULL)),
    ADD CONSTRAINT chk_requirement_amc_cleared_pair
        CHECK ((amc_cleared_by IS NULL) = (amc_cleared_at IS NULL));

-- An AMC row must say who marked it. Without this, a row could be excluded from a completion gate
-- with nothing recording who excused it - which is the whole point of the attribution columns.
ALTER TABLE shed_visit_checksheet_requirements
    ADD CONSTRAINT chk_requirement_amc_attributed
        CHECK (NOT under_amc OR amc_marked_by IS NOT NULL);

-- Partial index: the AMC rows are a small minority (zero today), and every read that cares about
-- them filters on under_amc = true - completion gates, the work list, and the panel's AMC count.
CREATE INDEX IF NOT EXISTS ix_requirement_under_amc
    ON shed_visit_checksheet_requirements (package_id)
    WHERE under_amc;

-- ====================================================================== post-conditions ========
DO $$
DECLARE
    v_marked        BIGINT;
    v_total         BIGINT;
    v_contradictory BIGINT;
BEGIN
    -- This migration marks nothing. AMC is an operational decision per visit, and inferring it from
    -- existing data would be inventing a contract that nobody recorded.
    SELECT count(*) INTO v_marked FROM shed_visit_checksheet_requirements WHERE under_amc;
    IF v_marked <> 0 THEN
        RAISE EXCEPTION
            'migration 014 must not mark any requirement Under AMC - found %', v_marked;
    END IF;

    -- Every existing row keeps its present behaviour, so nothing that blocks today stops blocking.
    SELECT count(*) INTO v_total FROM shed_visit_checksheet_requirements;
    SELECT count(*) INTO v_contradictory
      FROM shed_visit_checksheet_requirements WHERE under_amc AND NOT is_active;
    IF v_contradictory <> 0 THEN
        RAISE EXCEPTION 'AMC and deactivated cannot coexist - found % row(s)', v_contradictory;
    END IF;

    RAISE NOTICE
        'migration 014: under_amc added to % requirement(s), all false; Optional and Deactivated '
        'untouched', v_total;
END $$;

COMMIT;
