"""BL-DCMS tables that Operations Dashboard must delete from, declared as Core tables.

WHY THIS FILE EXISTS. Operations Dashboard and BL-DCMS share one PostgreSQL database, `rdcms`, but
each models only its own tables: app/db/models.py knows nothing of checksheet_header, and BL's
models know nothing of shed_visits. That separation is right for everyday work - neither service
should casually reach into the other's data - and it is exactly why there is no foreign key from
BL's checksheet_header.shed_visit_id to shed_visits.

Admin visit deletion is the one operation that cannot respect that separation. Deleting a shed visit
while leaving its BL checksheets behind would not fail, would not warn, and would not cascade: it
would silently create dangling rows pointing at an id that no longer exists. The production audit
confirmed the database currently has ZERO such orphans, and the deletion service must not be what
creates the first ones. Since both services address the same database, the correct boundary is one
transaction that removes both sides, which means this service needs to name BL's tables.

THESE ARE A DELIBERATE MIRROR, NOT A SECOND SOURCE OF TRUTH.
  * BL-DCMS owns these tables. Nothing here creates, alters or migrates them in production.
  * Only the columns the deletion actually needs are declared - the key columns it filters on, and
    the fields it snapshots into the ledger. A narrow mirror cannot copy a column it never names,
    which is the same reason the snapshot builder works from an allow-list.
  * NO foreign keys and NO ON DELETE actions are declared, on purpose. Production's real actions
    were read from the live catalog (see diagnostics/visit_deletion_audit.sql section 2) and they do
    NOT match BL's own models, which under-declare them: checksheet_value and digital_signatures are
    ON DELETE CASCADE in production while the models say nothing, and the booking/visit FKs are
    RESTRICT rather than NO ACTION. Declaring them here would invite the deletion service to rely on
    cascade behaviour. It does not - it deletes every child explicitly, in order, which is correct
    whatever the live FK says.
  * Their own MetaData, separate from models.Base, so BL's tables can never be created by an
    accidental Base.metadata.create_all against a real database.

WHAT PRODUCTION ACTUALLY DOES ON DELETE (read from the catalog, recorded here as documentation only):
    checksheet_value.checksheet_id            -> checksheet_header   CASCADE
    digital_signatures.checksheet_id          -> checksheet_header   CASCADE
    notifications.checksheet_id               -> checksheet_header   NO ACTION
    ..._signoff_checksheet.checksheet_header_id -> checksheet_header NO ACTION
    ..._signoff_checksheet.signoff_id         -> ..._section_signoff CASCADE
The two CASCADEs are traps rather than conveniences: the instant checksheet_header is deleted, its
values and signatures vanish with no further statement. They must therefore be snapshotted BEFORE
the header is touched, which is why the deletion order puts snapshotting first and deletion last.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

#: Separate from models.Base.metadata, so these can never be created alongside OD's own tables.
bl_metadata = MetaData()


checksheet_header = Table(
    "checksheet_header",
    bl_metadata,
    Column("id", Integer, primary_key=True),
    # The cross-system link. No foreign key in production either - this is the column that makes
    # orphans possible and therefore the column this whole feature exists to handle.
    Column("shed_visit_id", BigInteger, nullable=True),
    Column("locomotive_id", Integer),
    Column("section_id", Integer),
    Column("template_id", Integer),
    Column("equipment_id", Integer),
    Column("minor_inspection_equipment_id", Integer),
    Column("technician_mobile", String(15)),
    Column("supervisor_mobile", String(15)),
    Column("work_type", String(20)),
    Column("schedule_family", String(20)),
    Column("schedule_variant", String(20)),
    Column("workflow_stage_type", String(30)),
    Column("maintenance_type", String(20)),
    Column("traction_motor_number", String(20)),
    Column("status", String(20)),
    Column("submitted_at", DateTime),
    Column("submitted_by", Integer),
    Column("approved_at", DateTime),
    Column("approved_by", Integer),
    Column("rejected_at", DateTime),
    Column("rejected_by", Integer),
    Column("rejection_reason", Text),
    Column("last_modified_at", DateTime),
    Column("last_modified_by", Integer),
    # Names a file in BL's private storage directory. The file plan is built from this.
    Column("pdf_path", String),
    Column("created_at", DateTime),
)

checksheet_value = Table(
    "checksheet_value",
    bl_metadata,
    Column("id", Integer, primary_key=True),
    Column("checksheet_id", Integer, nullable=False),
    Column("field_id", Integer, nullable=False),
    Column("field_value", Text),
    Column("created_at", DateTime),
)

digital_signatures = Table(
    "digital_signatures",
    bl_metadata,
    Column("id", Integer, primary_key=True),
    Column("checksheet_id", Integer, nullable=False),
    Column("supervisor_id", Integer),
    Column("supervisor_name", String(100)),
    Column("supervisor_employee_id", String(20)),
    # Certificate metadata is PUBLIC material - the subject, issuer, serial and validity window of a
    # DSC. No private key or PIN is stored by BL-DCMS at all, so there is nothing secret to exclude
    # here; this is the evidentiary record the ledger must preserve.
    Column("certificate_subject", Text),
    Column("certificate_issuer", Text),
    Column("certificate_serial_number", String(100)),
    Column("certificate_thumbprint", String(128)),
    Column("certificate_valid_from", DateTime),
    Column("certificate_valid_to", DateTime),
    Column("signing_timestamp", DateTime),
    Column("signature_hash", String(128)),
    Column("verification_status", String(30)),
    Column("provider", String(30)),
    Column("created_at", DateTime),
)

notifications = Table(
    "notifications",
    bl_metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer),
    Column("title", String(200)),
    Column("message", Text),
    Column("type", String(30)),
    Column("checksheet_id", Integer),
    Column("is_read", Boolean),
    Column("created_at", DateTime),
)

minor_inspection_section_signoff = Table(
    "minor_inspection_section_signoff",
    bl_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("shed_visit_id", BigInteger, nullable=False),
    Column("section_id", Integer),
    Column("schedule_variant", String(20)),
    Column("technology", String(20)),
    Column("locomotive_id", Integer),
    Column("locomotive_number_snapshot", String(50)),
    Column("section_name_snapshot", String(100)),
    Column("status", String(20)),
    Column("signed_by_user_id", Integer),
    Column("signed_by_name", String(100)),
    Column("signed_by_employee_id", String(20)),
    Column("signed_at", DateTime),
    Column("signed_pdf_path", Text),
    Column("document_hash", String(128)),
    Column("certificate_subject", Text),
    Column("certificate_issuer", Text),
    Column("certificate_serial_number", String(100)),
    Column("certificate_thumbprint", String(128)),
    Column("certificate_valid_from", DateTime),
    Column("certificate_valid_to", DateTime),
    Column("signature_hash", String(128)),
    Column("verification_status", String(30)),
    Column("provider", String(30)),
    Column("created_at", DateTime),
    Column("updated_at", DateTime),
)

minor_inspection_section_signoff_checksheet = Table(
    "minor_inspection_section_signoff_checksheet",
    bl_metadata,
    # No surrogate key in production: the primary key is (signoff_id, checksheet_header_id). This is
    # why the ledger's item rows carry original_key as well as original_id - without it, the exact
    # set of checksheets a signature covered could not be recorded.
    Column("signoff_id", BigInteger, primary_key=True),
    Column("checksheet_header_id", Integer, primary_key=True),
    Column("display_order", Integer),
    Column("created_at", DateTime),
)

shed_visit_checksheet_signoff_mode = Table(
    "shed_visit_checksheet_signoff_mode",
    bl_metadata,
    Column("shed_visit_id", BigInteger, primary_key=True),
    Column("mode", String(30)),
    Column("created_at", DateTime),
)
