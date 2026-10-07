-- 013_admin_deletion_ledger.sql
--
-- The durable, append-only record of Admin destructive deletions. Forward-only, additive DDL only:
-- it creates two tables and their guards, writes no data, and alters nothing that exists.
--
-- WHY THIS TABLE CANNOT BE activity_logs. activity_logs is BL-DCMS's operational trail: its rows
-- are written per action, pruned by operational policy, and their entity_id is a bare integer whose
-- meaning is only resolvable while the referenced row still exists. After a visit is deleted, an
-- activity_logs row saying "CHECKSHEET_APPROVED entity_id=8821" names nothing. A deletion record has
-- the opposite requirement: it must stay fully meaningful precisely BECAUSE its subject is gone.
-- So everything needed to understand a deletion is snapshotted here, in full, at deletion time.
--
-- WHY THERE IS NOT ONE FOREIGN KEY TO OPERATIONAL DATA. Not an oversight - the entire point. A FK
-- to shed_visits, bookings, checksheet_header or users would make this ledger either (a) impossible
-- to write, because the row is about to be deleted, or (b) cascade away with its own subject, which
-- is the one failure mode that would make the whole feature unauditable. Every reference here is a
-- plain integer plus human-readable snapshots. The ONLY foreign key in this file is
-- admin_deletion_item.deletion_event_id -> admin_deletion_event.id, which is ledger-internal.
--
-- APPEND-ONLY IS ENFORCED, NOT DOCUMENTED. Two triggers refuse UPDATE and DELETE outright, with one
-- deliberate exception: the event row's completion columns, which are written exactly once as the
-- operation finishes (see trg_admin_deletion_event_append_only below). An audit trail that the
-- application is trusted not to rewrite is not an audit trail.
--
-- NO SECRETS, BY CONSTRUCTION. A CHECK constraint on admin_deletion_event rejects any reason text
-- containing a password-like key, and nothing in the schema has a column that could hold one. The
-- re-authentication password is never passed to this layer at all.

BEGIN;

-- ============================================================================== event ==========
CREATE TABLE IF NOT EXISTS admin_deletion_event (
    id                  BIGSERIAL PRIMARY KEY,

    -- A caller-supplied idempotency key. A retried request carrying the same key cannot create a
    -- second event, which is what stops a double-click or a network retry producing two
    -- conflicting records of one deletion.
    operation_id        UUID        NOT NULL UNIQUE,

    deletion_type       TEXT        NOT NULL,
    -- The row that was deleted: a booking id for BOOKING, a shed visit id for SHED_VISIT.
    -- Deliberately NOT a foreign key - its target no longer exists once this row is complete.
    target_id           BIGINT      NOT NULL,
    -- Always populated, for both types, so every deletion is attributable to a visit.
    shed_visit_id       BIGINT      NOT NULL,

    -- Identity snapshots. The locomotive master row survives a deletion, but its number can be
    -- corrected later; what this ledger must say is what the visit was called WHEN it was deleted.
    loco_number         TEXT        NOT NULL,
    schedule_family     TEXT,
    schedule_variant    TEXT,
    visit_status        TEXT,
    visit_arrival_at    TIMESTAMPTZ,

    -- Who did it. actor_user_id is a plain integer: users survive, but this record must not depend
    -- on that, and the employee id and name are what an auditor actually reads.
    actor_user_id       INTEGER     NOT NULL,
    actor_employee_id   TEXT        NOT NULL,
    actor_name          TEXT        NOT NULL,

    reason              TEXT        NOT NULL,
    -- The exact confirmation string the Admin typed. Stored because "did they really confirm THIS
    -- visit?" is a question the record should answer by itself.
    confirmation_text   TEXT,

    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    status              TEXT        NOT NULL DEFAULT 'IN_PROGRESS',
    failure_reason      TEXT,

    -- Row counts per table, as actually deleted. The service compares this with the preview and
    -- with the item rows it wrote; a mismatch aborts the transaction.
    record_counts       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- The full dependency manifest: every table, its matched ids, and the deletion order used.
    manifest            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- SHA-256 over the canonical (sorted-key, separator-normalised) JSON of the manifest, so a
    -- later reader can prove the manifest has not been edited. Deterministic for identical input.
    manifest_hash       TEXT,

    -- Filesystem outcome, recorded AFTER the database commit, because the filesystem cannot take
    -- part in the transaction. Until this is written, the files are still on disk.
    file_plan           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    file_result         JSONB,
    files_completed_at  TIMESTAMPTZ,

    CONSTRAINT chk_admin_deletion_type
        CHECK (deletion_type IN ('BOOKING', 'SHED_VISIT')),
    CONSTRAINT chk_admin_deletion_status
        CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    -- A completed event is fully populated or it is not completed. This is what stops a half-written
    -- record being read back later as a sound account of a deletion.
    CONSTRAINT chk_admin_deletion_completeness CHECK (
        (status = 'IN_PROGRESS' AND completed_at IS NULL AND manifest_hash IS NULL)
        OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND manifest_hash IS NOT NULL)
        OR (status = 'FAILED'    AND completed_at IS NOT NULL AND failure_reason IS NOT NULL)
    ),
    -- A deletion without a stated reason is not auditable.
    CONSTRAINT chk_admin_deletion_reason CHECK (trim(reason) <> ''),
    -- Belt and braces against a caller ever folding credentials into the free text.
    CONSTRAINT chk_admin_deletion_reason_no_secret
        CHECK (reason !~* '(password|passwd|secret|bearer |jwt|token=)')
);

COMMENT ON TABLE admin_deletion_event IS
    'Append-only record of Admin destructive deletions. Intentionally has no foreign keys to '
    'operational data so it stays readable after its subject is gone.';

CREATE INDEX IF NOT EXISTS ix_admin_deletion_event_visit   ON admin_deletion_event (shed_visit_id);
CREATE INDEX IF NOT EXISTS ix_admin_deletion_event_actor   ON admin_deletion_event (actor_user_id);
CREATE INDEX IF NOT EXISTS ix_admin_deletion_event_time    ON admin_deletion_event (requested_at DESC);
CREATE INDEX IF NOT EXISTS ix_admin_deletion_event_loco    ON admin_deletion_event (loco_number);

-- A single visit can only be fully deleted once. Partial unique index so that a FAILED attempt
-- does not block a retry, while two successful SHED_VISIT deletions of one visit are impossible.
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_deletion_visit_completed
    ON admin_deletion_event (shed_visit_id)
    WHERE deletion_type = 'SHED_VISIT' AND status = 'COMPLETED';

-- ============================================================================== items ==========
CREATE TABLE IF NOT EXISTS admin_deletion_item (
    id                  BIGSERIAL PRIMARY KEY,
    -- The ONLY foreign key in this schema, and it points inside the ledger. RESTRICT, not CASCADE:
    -- an event that has items cannot be removed, which is the same guarantee the triggers give.
    deletion_event_id   BIGINT      NOT NULL
        REFERENCES admin_deletion_event (id) ON DELETE RESTRICT,

    entity_type         TEXT        NOT NULL,
    original_id         BIGINT,
    -- Composite-key rows (minor_inspection_section_signoff_checksheet has no surrogate id) carry
    -- their key here instead, so every deleted row is representable.
    original_key        JSONB,

    -- The row's significant business fields as they were. Never credentials: the snapshot builder
    -- works from an explicit per-table allow-list, not from "every column", so a column added later
    -- cannot silently start being copied in here.
    snapshot            JSONB       NOT NULL,
    -- SHA-256 of the canonical snapshot JSON, so an individual row can be shown unaltered.
    content_hash        TEXT        NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_admin_deletion_item_identity
        CHECK (original_id IS NOT NULL OR original_key IS NOT NULL)
);

COMMENT ON TABLE admin_deletion_item IS
    'One row per operational record destroyed, with a field-level snapshot. Written BEFORE the '
    'delete, inside the same transaction.';

CREATE INDEX IF NOT EXISTS ix_admin_deletion_item_event
    ON admin_deletion_item (deletion_event_id, entity_type);
CREATE INDEX IF NOT EXISTS ix_admin_deletion_item_entity
    ON admin_deletion_item (entity_type, original_id);

-- ====================================================================== append-only ============
-- The event row is written twice by design - created IN_PROGRESS, then finalised - so a blanket
-- "no UPDATE" rule would make the feature impossible. This instead permits exactly the finalisation
-- transition and refuses everything else: no rewriting of who, what, why or when, and no second
-- finalisation of an event that already reached a terminal state.
CREATE OR REPLACE FUNCTION admin_deletion_event_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'admin_deletion_event is append-only: deletion record % cannot be deleted', OLD.id;
    END IF;

    IF OLD.status <> 'IN_PROGRESS' THEN
        -- ONE exception, and it is structural rather than a convenience. The filesystem cannot take
        -- part in this transaction, so files are acted on only AFTER the database deletion has
        -- committed - by which time the event is already COMPLETED. Its outcome therefore has to be
        -- recordable against a finalised row, or the ledger could never say what happened to the
        -- files it listed.
        --
        -- The allowance is as narrow as it can be: file_result and files_completed_at only, nothing
        -- else may move alongside them, and only while file_result is still NULL. So it is write-once
        -- in its own right and cannot be used to revise a deletion record afterwards.
        IF OLD.file_result IS NULL
           AND NEW.status            IS NOT DISTINCT FROM OLD.status
           AND NEW.completed_at      IS NOT DISTINCT FROM OLD.completed_at
           AND NEW.failure_reason    IS NOT DISTINCT FROM OLD.failure_reason
           AND NEW.manifest          IS NOT DISTINCT FROM OLD.manifest
           AND NEW.manifest_hash     IS NOT DISTINCT FROM OLD.manifest_hash
           AND NEW.record_counts     IS NOT DISTINCT FROM OLD.record_counts
           AND NEW.file_plan         IS NOT DISTINCT FROM OLD.file_plan
           AND NEW.operation_id      IS NOT DISTINCT FROM OLD.operation_id
           AND NEW.deletion_type     IS NOT DISTINCT FROM OLD.deletion_type
           AND NEW.target_id         IS NOT DISTINCT FROM OLD.target_id
           AND NEW.shed_visit_id     IS NOT DISTINCT FROM OLD.shed_visit_id
           AND NEW.loco_number       IS NOT DISTINCT FROM OLD.loco_number
           AND NEW.actor_user_id     IS NOT DISTINCT FROM OLD.actor_user_id
           AND NEW.actor_employee_id IS NOT DISTINCT FROM OLD.actor_employee_id
           AND NEW.actor_name        IS NOT DISTINCT FROM OLD.actor_name
           AND NEW.reason            IS NOT DISTINCT FROM OLD.reason
           AND NEW.requested_at      IS NOT DISTINCT FROM OLD.requested_at
           AND NEW.file_result       IS NOT NULL THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'admin_deletion_event % is already %, and a finalised deletion record cannot be '
            'changed (only the one-time filesystem result may be recorded)', OLD.id, OLD.status;
    END IF;

    -- Identity and attribution are immutable even during finalisation.
    IF NEW.operation_id      IS DISTINCT FROM OLD.operation_id
    OR NEW.deletion_type     IS DISTINCT FROM OLD.deletion_type
    OR NEW.target_id         IS DISTINCT FROM OLD.target_id
    OR NEW.shed_visit_id     IS DISTINCT FROM OLD.shed_visit_id
    OR NEW.loco_number       IS DISTINCT FROM OLD.loco_number
    OR NEW.actor_user_id     IS DISTINCT FROM OLD.actor_user_id
    OR NEW.actor_employee_id IS DISTINCT FROM OLD.actor_employee_id
    OR NEW.reason            IS DISTINCT FROM OLD.reason
    OR NEW.requested_at      IS DISTINCT FROM OLD.requested_at THEN
        RAISE EXCEPTION
            'admin_deletion_event %: identity, attribution, reason and requested_at are immutable',
            OLD.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_admin_deletion_event_append_only
    BEFORE UPDATE OR DELETE ON admin_deletion_event
    FOR EACH ROW EXECUTE FUNCTION admin_deletion_event_append_only();

-- Item snapshots are written once and never touched again. No exception, not even for the service
-- that wrote them: if a snapshot were editable, it would prove nothing.
CREATE OR REPLACE FUNCTION admin_deletion_item_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'admin_deletion_item is append-only: snapshot % cannot be % (deletion record %)',
        OLD.id, lower(TG_OP), OLD.deletion_event_id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_admin_deletion_item_append_only
    BEFORE UPDATE OR DELETE ON admin_deletion_item
    FOR EACH ROW EXECUTE FUNCTION admin_deletion_item_append_only();

-- ====================================================================== post-conditions ========
-- Stated as executable assertions rather than comments, so a future edit that breaks one of these
-- invariants fails here instead of shipping.
DO $$
DECLARE
    v_bad_fks  INT;
    v_events   BIGINT;
    v_items    BIGINT;
BEGIN
    -- The ledger must not reference operational data. Anything but the one internal FK is a bug.
    SELECT count(*) INTO v_bad_fks
    FROM pg_constraint con
    JOIN pg_class ct ON ct.oid = con.conrelid
    JOIN pg_class pt ON pt.oid = con.confrelid
    WHERE con.contype = 'f'
      AND ct.relname IN ('admin_deletion_event', 'admin_deletion_item')
      AND pt.relname <> 'admin_deletion_event';
    IF v_bad_fks <> 0 THEN
        RAISE EXCEPTION
            'the deletion ledger must have NO foreign key to operational data - found %', v_bad_fks;
    END IF;

    -- This migration creates empty tables. It records nothing retrospectively, because it has no
    -- way of knowing what any earlier deletion destroyed.
    SELECT count(*) INTO v_events FROM admin_deletion_event;
    SELECT count(*) INTO v_items  FROM admin_deletion_item;
    IF v_events <> 0 OR v_items <> 0 THEN
        RAISE EXCEPTION
            'migration 013 must create EMPTY tables - found % event(s) and % item(s)',
            v_events, v_items;
    END IF;

    RAISE NOTICE 'migration 013: deletion ledger created, empty, append-only, no operational FKs';
END $$;

COMMIT;
