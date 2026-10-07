"""SQLAlchemy ORM mappings for the `rdcms` PostgreSQL database.

Most classes here describe tables that already exist in `rdcms`, created and
owned by BL-DCMS (see each class's own history below) — Base.metadata.
create_all()/Alembic must never target those, and never will; see
app/db/session.py.

A smaller set of tables are owned by *this* application instead
(dashboard_access, Phase 1; ShedVisitChecksheetPackage/
ShedVisitChecksheetRequirement, Phase 5B.1) — Operations Dashboard's own
schema, coexisting in the same physical `rdcms` Postgres instance rather
than a separate database, exactly like BL-DCMS's own dashboard_access
precedent. Those tables' actual DDL still isn't run via create_all()
against the real database either — see backend/migrations/ and each
migration's own "do not execute automatically" note — but they are this
app's tables to define, not tables to merely mirror.

Tables mapped: users, sections, dashboard_access (Phase 1); shed_visits,
shed_visit_events, shed_visit_stages, bookings, booking_events,
booking_section_assignments, booking_defect_types (Phase 2B); shed_visit_
checksheet_packages, shed_visit_checksheet_requirements (Phase 5B.1, this
app's own tables). Column shapes and CHECK constraints for the RDCMS/
BL-DCMS-owned tables were confirmed against the live schema with `\\d
<table>` for each — see the Phase 2B report for the exact columns/
constraints found. Tests build an equivalent throwaway SQLite schema from
this same metadata (see tests/conftest.py).

CHECK constraint mirroring: Booking, BookingEvent and BookingSectionAssignment
now declare CheckConstraint()s matching the live rdcms definitions (verified
with pg_get_constraintdef), as of migration 003. SQLite *does* enforce CHECK
constraints, so mirroring is what lets the test suite catch a status/timestamp
coupling mismatch instead of it surfacing as a live 500 one HTTP call at a time
— which is exactly how the chk_booking_started failure was found. These are
still never created against the real database. ShedVisitEvent's event_type CHECK is
mirrored as of migration 009. ShedVisit and ShedVisitStage
are NOT yet mirrored: doing so surfaces ~204 pre-existing test-fixture states
(stages seeded COMPLETED with no completed_at, visits seeded CLOSED with no
departed_at) that PostgreSQL would already reject today, and fixing those
fixtures is a separate piece of work — see the audit report.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy import event, inspect, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import get_history


class Base(DeclarativeBase):
    pass


# SQLite only auto-increments a primary key column declared as plain
# INTEGER (its rowid alias); BIGINT does not qualify. This variant keeps
# BIGSERIAL/BIGINT semantics on PostgreSQL (matching the real RDCMS schema)
# while still auto-incrementing under the SQLite engine tests use — same
# pattern already used in /opt/loco-master/equipment/models.py.
_BigIntegerPK = Integer().with_variant(BigInteger(), "postgresql")


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mobile: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sections.id"), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    section: Mapped["Section | None"] = relationship("Section")
    dashboard_access: Mapped["DashboardAccess | None"] = relationship(
        "DashboardAccess",
        back_populates="user",
        uselist=False,
        foreign_keys="DashboardAccess.user_id",
    )


class DashboardAccess(Base):
    __tablename__ = "dashboard_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, unique=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    can_add_booking_sections: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_manage_equipment_mapping: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Migration 015. May replace a booking's maintenance-section assignments via
    # PUT /api/bookings/{id}/sections. DISTINCT from can_add_booking_sections above, which gates
    # the retired 410 add-section route and is inert - reusing that flag would retroactively
    # re-arm routing for every account that still holds it. Granted to planning sections (PPIO).
    can_route_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped["User"] = relationship(
        "User", back_populates="dashboard_access", foreign_keys=[user_id]
    )


class ShedVisit(Base):
    __tablename__ = "shed_visits"
    __table_args__ = (
        # Mirrors RDCMS's own uq_one_open_shed_visit_per_loco partial unique
        # index — reproduced here (not created; only ever read against
        # RDCMS) so the disposable SQLite test DB enforces the same race
        # condition the service's 409 handling is written for.
        Index(
            "uq_one_open_shed_visit_per_loco",
            "loco_number",
            unique=True,
            postgresql_where=text("status IN ('IN_SHED', 'READY')"),
            sqlite_where=text("status IN ('IN_SHED', 'READY')"),
        ),
        # Mirrors migrations/008 exactly, so the disposable SQLite test DB enforces the same
        # ordering rules as production. Every clause is NULL-tolerant on purpose: a CHECK only
        # fails on FALSE (it PASSES on NULL), so each comparison is either genuinely FALSE for a
        # bad ordering or skipped when one side is absent - which is also what keeps historical
        # rows (NULL schedule_started_at) valid without rewriting data.
        CheckConstraint(
            "(schedule_started_at IS NULL OR schedule_started_at >= arrival_at) "
            "AND (ready_at IS NULL OR schedule_started_at IS NULL OR ready_at >= schedule_started_at) "
            "AND (ready_at IS NULL OR ready_at >= arrival_at) "
            "AND (departed_at IS NULL OR ready_at IS NULL OR departed_at >= ready_at) "
            "AND (departed_at IS NULL OR departed_at >= arrival_at) "
            "AND (inspection_completed_at IS NULL OR inspection_completed_at >= arrival_at) "
            "AND (inspection_completed_at IS NULL OR schedule_started_at IS NULL "
            "     OR inspection_completed_at >= schedule_started_at) "
            "AND (ready_at IS NULL OR inspection_completed_at IS NULL OR ready_at >= inspection_completed_at) "
            "AND (departed_at IS NULL OR inspection_completed_at IS NULL OR departed_at >= inspection_completed_at)",
            name="chk_shed_visit_timestamp_order",
        ),
        CheckConstraint(
            "(ready_at IS NULL OR schedule_started_at IS NOT NULL OR status = 'CLOSED') "
            "AND (departed_at IS NULL OR status = 'CLOSED') "
            "AND (inspection_completed_at IS NULL OR schedule_started_at IS NOT NULL)",
            name="chk_shed_visit_phase_progression",
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    loco_number: Mapped[str] = mapped_column(String(20), nullable=False)
    arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_source: Mapped[str] = mapped_column(String(30), nullable=False, default="DASHBOARD")
    schedule_family: Mapped[str | None] = mapped_column(String(20), nullable=True)
    schedule_variant: Mapped[str | None] = mapped_column(String(30), nullable=True)
    visit_type: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_SHED")
    # New shed workflow: when actual schedule work began (the Start Schedule action). NULL while
    # the locomotive is in shed but Spare, and NULL for visits predating migration 008. Actor and
    # source are recorded in shed_visit_events, not duplicated here.
    schedule_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Migration 012, MINOR only: the actual Minor Inspection ended (the Complete Schedule action).
    # Inspection duration = inspection_completed_at - schedule_started_at, excluding Test Before and
    # Test After. NULL for MAJOR and for visits predating migration 012.
    inspection_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # MINOR (migration 012+): READY after Test After (the Mark Ready action). MAJOR, and MINOR visits
    # completed before 012: the Complete Schedule action. NOT departure eligibility - that stays
    # computed from the stage/booking/checksheet gates in shed_out_service.
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    departure_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_condition: Mapped[str | None] = mapped_column(String(20), nullable=True)


# Authoritative shed_visit_events.event_type vocabulary - must equal the live rdcms
# chk_shed_visit_event_type as of migrations/009 (tests/test_shed_visit_event_type_contract.py
# enforces that against the migration file). MARK_READY / SCHEDULE_CHANGED / MANUAL_CORRECTION
# are legacy values no code emits today, preserved for existing/external rows.
SHED_VISIT_EVENT_TYPES = (
    "SHED_IN",
    "MARK_READY",
    "SHED_OUT",
    "SCHEDULE_CHANGED",
    "MANUAL_CORRECTION",
    "SCHEDULE_STARTED",
    "SCHEDULE_COMPLETED",
    "TEST_BEFORE_SKIPPED",
)


class ShedVisitEvent(Base):
    __tablename__ = "shed_visit_events"
    __table_args__ = (
        # Mirrors the live chk_shed_visit_event_type (migration 009). Its absence is how
        # SCHEDULE_STARTED passed every SQLite test yet failed in PostgreSQL.
        CheckConstraint(
            "event_type IN (" + ", ".join(f"'{t}'" for t in SHED_VISIT_EVENT_TYPES) + ")",
            name="chk_shed_visit_event_type",
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    shed_visit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shed_visits.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="DASHBOARD")
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShedVisitStage(Base):
    __tablename__ = "shed_visit_stages"
    __table_args__ = (
        Index("uq_shed_visit_stage_order", "shed_visit_id", "stage_order", unique=True),
        Index("uq_shed_visit_stage_type", "shed_visit_id", "stage_type", unique=True),
        # Mirrors migrations/012's status vocabulary and skip rule in the SQLite test DB.
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')",
            name="chk_shed_visit_stage_status",
        ),
        # chk_stage_started / chk_stage_completed are deliberately not mirrored: many historical test
        # fixtures flip a stage's status directly. Service code sets the timestamps itself.
        # Only TEST_BEFORE may be SKIPPED; a skipped stage is never also completed.
        CheckConstraint(
            "(status = 'SKIPPED' AND stage_type = 'TEST_BEFORE' AND skipped_at IS NOT NULL AND completed_at IS NULL) "
            "OR (status <> 'SKIPPED' AND skipped_at IS NULL AND skipped_by IS NULL AND skip_reason IS NULL)",
            name="chk_stage_skipped",
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    shed_visit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shed_visits.id"), nullable=False
    )
    stage_type: Mapped[str] = mapped_column(String(30), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    completion_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Migration 012: Admin skipped Test Before for this visit (status SKIPPED, TEST_BEFORE only).
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # Mirrors of the live rdcms CHECK constraints on `bookings` AS OF
        # migration 003 (see migrations/003_decouple_booking_status_from_
        # legacy_timestamps.sql). Deliberately absent, because 003 drops them:
        # chk_booking_started / chk_booking_attended / chk_booking_reopened,
        # which coupled `status` to the parent-level started_at/attended_at/
        # reopened_at columns. `status` is a DERIVED aggregate over this
        # booking's booking_section_assignments rows (see
        # section_dashboard_service.recompute_booking_status()); the parent
        # timestamp columns below are vestigial and written by nothing, so any
        # such coupling is unsatisfiable by construction.
        #
        # NOTE: until 003 is applied by a DBA, the live database still has
        # those three constraints and every assignment transition fails there,
        # even though this model (and therefore the SQLite test schema) does
        # not.
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'ATTENDED', 'REOPENED')", name="chk_booking_status"
        ),
        # Widened by migration 005 to admit TRIP_INSPECTION and GENERAL_CHECKING - a strict
        # superset of the previous six values, so no existing row is invalidated. See that
        # migration for why neither SCHEDULE_INSPECTION nor MANUAL is an acceptable stand-in for
        # a Trip Inspection / General Checking finding.
        CheckConstraint(
            "booking_source IN ('LOG_BOOK', 'TEST_BEFORE', 'SCHEDULE_INSPECTION', "
            "'TEST_AFTER', 'SPECIAL_CHECKING', 'MANUAL', 'TRIP_INSPECTION', "
            "'GENERAL_CHECKING')",
            name="chk_booking_source",
        ),
        CheckConstraint("length(trim(description)) > 0", name="chk_booking_description"),
        # Migration 004. Unique among NON-NULL values only: every Dashboard-native flow leaves
        # client_booking_id NULL and must stay able to create any number of such rows. Mirrored
        # here (not created against rdcms) so the SQLite test schema enforces the same race
        # protection the idempotent-create path is written against - same precedent as
        # ShedVisit.uq_one_open_shed_visit_per_loco above.
        Index(
            "uq_bookings_client_booking_id",
            "client_booking_id",
            unique=True,
            postgresql_where=text("client_booking_id IS NOT NULL"),
            sqlite_where=text("client_booking_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    shed_visit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shed_visits.id"), nullable=False
    )
    stage_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("shed_visit_stages.id"), nullable=True
    )
    booking_source: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    equipment_node_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attended_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    attended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attendance_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    defect_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("booking_defect_types.id"), nullable=True
    )
    # Migration 004: stable client-generated idempotency key for externally-originated bookings
    # (Android's StagedBooking.localBookingId, relayed by BL-DCMS through
    # POST /api/internal/bookings). NULL for every booking created by a Dashboard-native flow -
    # Shed In's Log Book bookings and the Test Before / Schedule Inspection / Test After finding
    # flows all leave it unset, unchanged. It deduplicates a RETRANSMISSION of one single
    # client-side row and nothing else; it is not, and must never become, a business-level
    # "same defect already booked" check (the same defect wording legitimately recurs across
    # different checksheets and visits).
    client_booking_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # VESTIGIAL parent-level lifecycle columns. Migration 002 (Common
    # Booking Pool reform) briefly made started_by/started_at/attended_by/
    # attended_at/attendance_remarks/reopened_by/reopened_at plus the two
    # *_section_id columns below the authoritative record of who started/
    # attended/reopened a booking. That reform was superseded: the
    # section-execution lifecycle lives on booking_section_assignments
    # again (see section_dashboard_service.py), and Booking.status is a
    # derived aggregate over those rows. As of this audit NO code path
    # writes any of these columns - the only remaining reference is a
    # read in booking_pool_service.py's Admin display projection, which
    # therefore reports NULL for every row. They are retained physically
    # (nothing dropped, no data altered); migration 003 removes the CHECK
    # constraints that coupled them to `status` and records the same note
    # as a COMMENT ON COLUMN in the database itself.
    started_by_section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sections.id"), nullable=True
    )
    attended_by_section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sections.id"), nullable=True
    )


class BookingEvent(Base):
    __tablename__ = "booking_events"
    __table_args__ = (
        # Mirrors the live rdcms chk_booking_event_type. Unchanged by migration
        # 003 - every event type the assignment lifecycle emits (CREATED,
        # AUTO_ROUTED, STARTED, ATTENDED, REOPENED) is already permitted here.
        CheckConstraint(
            "event_type IN ('CREATED', 'AUTO_ROUTED', 'FORWARDED', 'STARTED', "
            "'ATTENDED', 'REOPENED')",
            name="chk_booking_event_type",
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sections.id"), nullable=True
    )
    to_section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sections.id"), nullable=True
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookingSectionAssignment(Base):
    __tablename__ = "booking_section_assignments"
    __table_args__ = (
        Index("uq_booking_section", "booking_id", "section_id", unique=True),
        # This is where the section-execution lifecycle actually lives, so this
        # is where the status/timestamp coupling belongs. The first four mirror
        # constraints already present in live rdcms; the last three are added by
        # migration 003, pushing down the integrity that the (now dropped)
        # parent-level bookings constraints used to imply:
        #   ATTENDED is reachable only from IN_PROGRESS -> must have started_at
        #   REOPENED is reachable only from ATTENDED    -> must have attended_at
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'ATTENDED', 'REOPENED')",
            name="chk_booking_section_status",
        ),
        CheckConstraint(
            "assignment_source IN ('AUTO_MAPPING', 'MANUAL')", name="chk_booking_section_source"
        ),
        CheckConstraint(
            "status <> 'IN_PROGRESS' OR started_at IS NOT NULL",
            name="chk_booking_section_started",
        ),
        CheckConstraint(
            "status <> 'ATTENDED' OR attended_at IS NOT NULL", name="chk_booking_section_attended"
        ),
        CheckConstraint(
            "status <> 'ATTENDED' OR started_at IS NOT NULL",
            name="chk_booking_section_attended_requires_start",
        ),
        CheckConstraint(
            "status <> 'REOPENED' OR attended_at IS NOT NULL",
            name="chk_booking_section_reopened_requires_attend",
        ),
        # Deliberately NOT constrained: attended_at >= started_at. It looks
        # obviously true but is false across a reopen cycle - REOPENED ->
        # IN_PROGRESS (start_assignment) writes a fresh started_at while
        # leaving the previous attended_at in place as audit history, so an
        # in-flight restarted assignment legitimately carries attended_at <
        # started_at until it is attended again. A draft of migration 003 did
        # propose this constraint; the mirrored model constraints above are
        # what caught it (tests/test_section_dashboard.py::test_start_reopened_
        # assignment_succeeds), before it reached the live database.
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"), nullable=False)
    section_id: Mapped[int] = mapped_column(Integer, ForeignKey("sections.id"), nullable=False)
    assignment_source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    assigned_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attended_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    attended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attendance_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookingDefectType(Base):
    __tablename__ = "booking_defect_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- BL-DCMS Integration Phase 5B.1: materialized checksheet work packages -------------
#
# Operations Dashboard-owned tables (see the module docstring) — NOT mirrors of anything
# BL-DCMS owns. BL-DCMS's checksheet_template_applicability (a separate database, reached only
# over HTTP — see app/clients/bldcms.py) is mutable configuration; a work package is a frozen,
# visit-specific snapshot of what that configuration said at the moment an Admin generated it.
# Deliberately NO foreign key to anything BL-DCMS owns — applicability_id/template_id below are
# plain integers, external identifiers trusted the same way LOCO_MASTER-sourced ids already are
# elsewhere in this codebase, not referential-integrity-checked against a database this app has
# no connection to.
#
# Header + detail, not one flat table with generated_by/generated_at repeated on every
# requirement row: a package is generated once per visit (never re-generated - see
# checksheet_work_package_service.py's module docstring on idempotency), so "has a package been
# generated for this visit" and "who/when" belong on a single header row, not duplicated across
# what could be a dozen+ requirement rows. This exactly mirrors how shed_visit_stages (detail)
# hangs off shed_visits (header) rather than repeating visit-level fields per stage.


class ShedVisitChecksheetPackage(Base):
    __tablename__ = "shed_visit_checksheet_packages"

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    shed_visit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shed_visits.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    generated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Migration 010: did BL-DCMS declare EVERY required section of this technology's Minor
    # Inspection configured when this package was generated/refreshed? NULL for MAJOR. Stage
    # reconciliation never completes SCHEDULE_INSPECTION unless this is True.
    minor_inspection_configuration_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    requirements: Mapped[list["ShedVisitChecksheetRequirement"]] = relationship(
        "ShedVisitChecksheetRequirement", back_populates="package", cascade="all, delete-orphan"
    )


class ShedVisitChecksheetRequirement(Base):
    __tablename__ = "shed_visit_checksheet_requirements"
    __table_args__ = (
        # One row per (package, stage, applicability decision) - prevents generating the same
        # BL-DCMS applicability requirement twice for the same visit. A separate UNIQUE on
        # (package_id, workflow_stage_type, template_id) was considered and deliberately not
        # added: BL-DCMS's own uq_applicability_identity constraint already guarantees at most
        # one applicability row exists per (template_id, schedule_family, schedule_variant,
        # workflow_stage_type) - so for the one fixed schedule_variant a whole package is
        # generated against, a given template_id can only ever arrive via one applicability_id
        # in the first place. Adding a second constraint here would be redundant with an
        # invariant this table doesn't own and can't independently verify - see the Phase 5B.1
        # report for the full reasoning.
        # Operational Control phase (migration 006): widened from (package, stage,
        # applicability_id) - which could only ever express a MINOR applicability decision - to
        # the FULL requirement identity. This is also the key the pending panel matches
        # checksheet instances on. postgresql_nulls_not_distinct is essential: without it two
        # MAJOR_DIRECT rows (equipment/stage/maintenance_type all NULL) for the same template
        # would both be permitted, because NULLs would compare as distinct.
        Index(
            "uq_checksheet_requirement_identity",
            "package_id",
            "template_id",
            "workflow_stage_type",
            "section_id_snapshot",
            "equipment_id_snapshot",
            "maintenance_type_snapshot",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # NULL stage is legal as of migration 006: MAJOR is equipment-wise and has no
        # TB/Inspection/TA stages.
        CheckConstraint(
            "workflow_stage_type IS NULL OR "
            "workflow_stage_type IN ('TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER')",
            name="chk_checksheet_requirement_workflow_stage_type",
        ),
        CheckConstraint(
            "requirement_source IN ('APPLICABILITY', 'MAJOR_EQUIPMENT', "
            "'MAJOR_EQUIPMENT_CHOICE', 'MAJOR_DIRECT')",
            name="chk_checksheet_requirement_source",
        ),
        # A MINOR applicability row carries both of its identifying dimensions; a MAJOR row
        # carries neither. Stops a half-populated row of either kind being written.
        CheckConstraint(
            "(requirement_source = 'APPLICABILITY' AND applicability_id IS NOT NULL "
            " AND workflow_stage_type IS NOT NULL) OR "
            "(requirement_source <> 'APPLICABILITY' AND applicability_id IS NULL "
            " AND workflow_stage_type IS NULL)",
            name="chk_checksheet_requirement_model_coherent",
        ),
        # Migration 010: the NEW Minor Inspection equipment identity, never overloaded into the
        # legacy equipment_id_snapshot. All three set together, only on a SCHEDULE_INSPECTION
        # applicability requirement with no legacy equipment.
        CheckConstraint(
            "(minor_inspection_equipment_id IS NULL AND minor_inspection_equipment_code_snapshot IS NULL "
            " AND minor_inspection_equipment_name_snapshot IS NULL) OR "
            "(minor_inspection_equipment_id IS NOT NULL AND minor_inspection_equipment_code_snapshot IS NOT NULL "
            " AND minor_inspection_equipment_name_snapshot IS NOT NULL AND equipment_id_snapshot IS NULL "
            " AND requirement_source IS NOT NULL AND requirement_source = 'APPLICABILITY' "
            " AND workflow_stage_type IS NOT NULL AND workflow_stage_type = 'SCHEDULE_INSPECTION')",
            name="chk_checksheet_requirement_minor_inspection_equipment",
        ),
        CheckConstraint(
            "minor_inspection_equipment_label_snapshot IS NULL OR minor_inspection_equipment_id IS NOT NULL",
            name="chk_checksheet_requirement_minor_inspection_label",
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True)
    package_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shed_visit_checksheet_packages.id", ondelete="CASCADE"), nullable=False
    )
    # NULL for MAJOR (equipment-wise, no stages) - see the CheckConstraint above.
    workflow_stage_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # External BL-DCMS identifiers - deliberately not ForeignKey()s (see the section header
    # comment above). Both retained (not just template_id) so a future work-package row can
    # always identify the exact applicability decision that produced it, even after that
    # decision is edited/deactivated in BL-DCMS later - see the Phase 5B.1 report's "Important
    # Identity Rule" section.
    # NULL for MAJOR: Major has no checksheet_template_applicability rows at all - it resolves
    # from section_equipment_map + section templates (BL-DCMS
    # major_checksheet_work_service.resolve_major_checksheet_work), so there is no applicability
    # decision to point at.
    applicability_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Which resolver produced this row: APPLICABILITY (Minor) or one of the three Major patterns.
    requirement_source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="APPLICABILITY"
    )

    # --- Per-visit operational control (migration 006) -------------------------------------
    # These two are the ONLY place the pending panel's Mark Optional / Deactivate actions write.
    # BL-DCMS's global checksheet_template_applicability.is_required/is_active are never touched
    # by those actions - that is master configuration for all future visits, this is one visit.
    #
    # is_required=False ("Optional")  -> still shown and still performable, but does not block
    #                                    visit/checksheet completion.
    # is_active=False   ("Deactivated") -> not expected for this visit; hidden from the default
    #                                    pending list, never deleted, re-enablable.
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Snapshotted human-readable context, frozen at generation time - never re-read from
    # BL-DCMS after this row is created. A later BL-DCMS template rename/technology change/
    # deactivation must never alter what this row says a past visit's requirement was.
    template_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    technology_snapshot: Mapped[str] = mapped_column(String(30), nullable=False)
    section_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_name_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    equipment_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    equipment_name_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    maintenance_type_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Migration 010: BL-DCMS Minor Inspection equipment directory identity + frozen code/name.
    minor_inspection_equipment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor_inspection_equipment_code_snapshot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    minor_inspection_equipment_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Migration 011: the human label BL-DCMS computed ("A - VCD" / "HB CUBICLE-1"); NULL on older rows.
    minor_inspection_equipment_label_snapshot: Mapped[str | None] = mapped_column(String(260), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Python-side default as well as the DB default: an ORM insert that omits this column sends
    # an explicit NULL, which NOT NULL rejects before the DB default can ever apply.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # Who last changed is_required/is_active for this visit. NULL = never overridden.
    changed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    # Migration 014: "Under AMC" - this requirement is maintained by outside Firm Staff under an
    # Annual Maintenance Contract, for THIS visit only.
    #
    # A THIRD, INDEPENDENT STATE, deliberately not folded into either of the two above. is_active
    # false means "not expected on this visit at all"; is_required false means "may be done, never
    # blocks"; under_amc means "applicable and expected, but somebody outside the workshop owns it".
    # Collapsing any pair would make the panel unable to say which is true, and would make a
    # completion gate unable to report why a requirement was excused.
    #
    # Lives here rather than on a checksheet because 914 of production's active requirements have no
    # checksheet_header row at all (audit section 4) - unstarted work is the panel's whole purpose.
    # And not on checksheet_templates, because one template serves every visit of a schedule, so a
    # flag there would mark the equipment AMC for every locomotive at once.
    under_amc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Both directions are recorded. marked_* is NOT cleared when AMC is lifted, so the history shows
    # who excused the requirement as well as who restored it.
    amc_marked_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    amc_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amc_cleared_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    amc_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def effective_state(self) -> str:
        """The ONE answer to "what is this requirement's state", in precedence order.

        Exposed as a single value so no caller has to combine three booleans and risk combining them
        differently from the next caller - which is how a panel ends up showing two badges or a gate
        ends up disagreeing with the page.
        """
        if not self.is_active:
            return "DEACTIVATED"
        if self.under_amc:
            return "UNDER_AMC"
        if not self.is_required:
            return "OPTIONAL"
        return "REQUIRED"

    @property
    def blocks_completion(self) -> bool:
        """Whether this requirement must be operationally satisfied before the visit may proceed.

        TEST_BEFORE / TEST_AFTER are NOT considered here: migration 012 made them always required
        regardless of the per-visit flags, and the completion service applies that rule itself before
        consulting this.
        """
        return self.effective_state == "REQUIRED"

    package: Mapped["ShedVisitChecksheetPackage"] = relationship(
        "ShedVisitChecksheetPackage", back_populates="requirements"
    )


# =================================================================================================
# ADMIN DELETION LEDGER (migration 013)
# =================================================================================================
#
# The durable record of Admin destructive deletions, and the one part of this schema that must
# remain fully meaningful AFTER its subject no longer exists.
#
# THERE IS NOT ONE FOREIGN KEY TO OPERATIONAL DATA HERE, and that is the entire design. A FK to
# shed_visits, bookings, checksheet_header or users would make these rows either impossible to write
# (the target is about to be deleted) or liable to cascade away with their own subject - the single
# failure that would make the feature unauditable. Every reference is a plain integer carried
# alongside human-readable snapshots, so a reader years later needs nothing but these two tables.
# Migration 013 asserts this with a post-condition that counts FKs and aborts if any appear.
#
# APPEND-ONLY IS ENFORCED IN THE DATABASE, by trg_admin_deletion_event_append_only and
# trg_admin_deletion_item_append_only. The listeners below mirror those triggers so the SQLite test
# schema enforces the same rules, because a guard that only holds in production is a guard that is
# never exercised. The one permitted mutation is finalising an IN_PROGRESS event.


class AdminDeletionEvent(Base):
    """One destructive deletion, authorised and attributed."""

    __tablename__ = "admin_deletion_event"
    __table_args__ = (
        CheckConstraint(
            "deletion_type IN ('BOOKING', 'SHED_VISIT')", name="chk_admin_deletion_type"
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')", name="chk_admin_deletion_status"
        ),
        # A completed event is fully populated or it is not completed - the same shape of guard
        # minor_inspection_section_signoff uses for a signature. It stops a half-written row being
        # read back later as a sound account of a deletion.
        CheckConstraint(
            "(status = 'IN_PROGRESS' AND completed_at IS NULL AND manifest_hash IS NULL)"
            " OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND manifest_hash IS NOT NULL)"
            " OR (status = 'FAILED' AND completed_at IS NOT NULL AND failure_reason IS NOT NULL)",
            name="chk_admin_deletion_completeness",
        ),
        CheckConstraint("trim(reason) <> ''", name="chk_admin_deletion_reason"),
        Index("ix_admin_deletion_event_visit", "shed_visit_id"),
        Index("ix_admin_deletion_event_actor", "actor_user_id"),
        Index("ix_admin_deletion_event_time", "requested_at"),
        Index("ix_admin_deletion_event_loco", "loco_number"),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True, autoincrement=True)

    #: Caller-supplied idempotency key. A retried request carrying the same key cannot create a
    #: second event, which is what stops a double-click or a network retry producing two
    #: conflicting records of one deletion.
    #: UUID in PostgreSQL, matching migration 013 exactly - the database type is what rejects a
    #: non-UUID idempotency key, and a VARCHAR column here would not compare against it at all
    #: (`operator does not exist: uuid = character varying`). VARCHAR under SQLite, which has no
    #: UUID type; as_uuid=False so callers on both engines deal in plain strings.
    operation_id: Mapped[str] = mapped_column(
        String(36).with_variant(PG_UUID(as_uuid=False), "postgresql"),
        nullable=False,
        unique=True,
    )

    deletion_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: The deleted row's id - a booking id, or a shed visit id. Deliberately not a FK: by the time
    #: this event is COMPLETED, nothing with that id exists.
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Populated for BOTH types, so every deletion is attributable to a visit.
    shed_visit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Identity as it stood AT DELETION TIME. The locomotive master row survives, but its number can
    # be corrected later; what this record must say is what the visit was called when it was deleted.
    loco_number: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_family: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    visit_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    visit_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Who. actor_user_id is a plain integer: users do survive, but this record must not depend on
    # that, and the employee id and name are what an auditor actually reads.
    actor_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str] = mapped_column(Text, nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: The exact confirmation string the Admin typed, because "did they really confirm THIS visit?"
    #: is a question this record should answer by itself.
    confirmation_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="IN_PROGRESS")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Rows deleted per table, as actually deleted. Compared against the preview and against the
    #: item rows written; a mismatch aborts the transaction.
    record_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: The full dependency manifest: every table, its matched ids, and the deletion order used.
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: SHA-256 over the canonical JSON of the manifest, so a later reader can prove it is unedited.
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: What the filesystem step intends to do, written BEFORE the commit.
    file_plan: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: What it actually did, written AFTER the commit - the filesystem cannot join the transaction.
    file_result: Mapped[list | None] = mapped_column(JSON, nullable=True)
    files_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list["AdminDeletionItem"]] = relationship(
        "AdminDeletionItem", back_populates="event"
    )


class AdminDeletionItem(Base):
    """One destroyed operational row, with a field-level snapshot taken before it was deleted."""

    __tablename__ = "admin_deletion_item"
    __table_args__ = (
        CheckConstraint(
            "original_id IS NOT NULL OR original_key IS NOT NULL",
            name="chk_admin_deletion_item_identity",
        ),
        Index("ix_admin_deletion_item_event", "deletion_event_id", "entity_type"),
        Index("ix_admin_deletion_item_entity", "entity_type", "original_id"),
    )

    id: Mapped[int] = mapped_column(_BigIntegerPK, primary_key=True, autoincrement=True)
    #: The ONLY foreign key in the ledger, and it points inside the ledger. RESTRICT, never CASCADE:
    #: an event that has items cannot be removed.
    deletion_event_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_deletion_event.id", ondelete="RESTRICT"),
        nullable=False,
    )

    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    original_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Composite-key rows carry their key here instead - minor_inspection_section_signoff_checksheet
    #: has no surrogate id - so every deleted row is representable.
    original_key: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: The row's significant fields as they were. Built from an explicit per-table allow-list, never
    #: from "every column", so a column added later cannot silently start being copied in here.
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    #: SHA-256 of the canonical snapshot JSON, so one row can be shown unaltered.
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    event: Mapped["AdminDeletionEvent"] = relationship("AdminDeletionEvent", back_populates="items")


class DeletionLedgerImmutableError(RuntimeError):
    """The deletion ledger is append-only."""


#: The only columns writable on an ALREADY-FINALISED event, and only once - see the listener.
_EVENT_FILE_RESULT_COLUMNS = frozenset({"file_result", "files_completed_at"})


def _file_result_already_set(connection, target) -> bool:
    """Whether this event has already recorded its filesystem outcome, read from the database rather
    than the instance - the attribute is typically expired at this point."""
    stored = connection.execute(
        select(AdminDeletionEvent.__table__.c.file_result).where(
            AdminDeletionEvent.__table__.c.id == target.id
        )
    ).scalar()
    return stored is not None


#: Columns the finalisation UPDATE is allowed to move. Everything else on an event is immutable even
#: during finalisation, which is what stops a deletion being re-attributed after the fact.
_EVENT_MUTABLE_COLUMNS = frozenset(
    {
        "status",
        "completed_at",
        "failure_reason",
        "record_counts",
        "manifest",
        "manifest_hash",
        "file_plan",
        "file_result",
        "files_completed_at",
        "visit_status",
        "visit_arrival_at",
        "schedule_family",
        "schedule_variant",
        "confirmation_text",
    }
)


@event.listens_for(AdminDeletionEvent, "before_update", propagate=True)
def _event_append_only(_mapper, connection, target) -> None:  # pragma: no cover - guard
    """Application-level half of the append-only guarantee for deletion events.

    PostgreSQL enforces this with trg_admin_deletion_event_append_only (migration 013), but that
    trigger cannot exist on the SQLite test schema, and a guard that only holds in production is a
    guard that is never exercised. This makes the same rule true everywhere, including in the tests
    that assert it.

    The event row is written twice BY DESIGN - created IN_PROGRESS, then finalised - so a blanket
    refusal would make the feature impossible. This permits exactly that transition and nothing
    else: no rewriting of who, what or why, and no second finalisation.
    """
    # The STORED status, read back on this connection rather than taken from the instance.
    # get_history() is not sufficient: after a commit the attribute is expired, and assigning a new
    # value then records an "added" entry with no "deleted" one - so the old value is simply not
    # there, and falling back to target.status would report the value someone is ATTEMPTING to set.
    # The message would then read as if the record were already in the state being requested, which
    # is the opposite of the truth. before_update fires before the UPDATE statement, so this SELECT
    # sees the row as it still stands.
    previous = connection.execute(
        select(AdminDeletionEvent.__table__.c.status).where(
            AdminDeletionEvent.__table__.c.id == target.id
        )
    ).scalar()
    if previous is None:
        previous = target.status
    changed_now = {
        attr.key
        for attr in inspect(target).mapper.column_attrs
        if get_history(target, attr.key).has_changes()
    }

    if previous != "IN_PROGRESS":
        # ONE structural exception, mirroring trg_admin_deletion_event_append_only. The filesystem
        # cannot join the transaction, so files are acted on only after the database deletion has
        # committed - by which time the event is already COMPLETED. Its outcome must still be
        # recordable, or the ledger could never say what happened to the files it listed. The
        # allowance covers file_result and files_completed_at only, and only while file_result is
        # still unset, so it is write-once in its own right.
        if changed_now <= _EVENT_FILE_RESULT_COLUMNS and not _file_result_already_set(connection, target):
            return
        raise DeletionLedgerImmutableError(
            f"admin_deletion_event {target.id} is already {previous}, and a finalised deletion "
            f"record cannot be changed (only the one-time filesystem result may be recorded)"
        )

    forbidden = sorted(changed_now - _EVENT_MUTABLE_COLUMNS)
    if forbidden:
        raise DeletionLedgerImmutableError(
            f"admin_deletion_event {target.id}: {', '.join(forbidden)} "
            f"{'is' if len(forbidden) == 1 else 'are'} immutable - identity, attribution, reason "
            f"and requested_at can never be rewritten"
        )


@event.listens_for(AdminDeletionEvent, "before_delete", propagate=True)
def _event_no_delete(_mapper, _connection, target) -> None:  # pragma: no cover - guard
    raise DeletionLedgerImmutableError(
        f"admin_deletion_event is append-only: deletion record {target.id} cannot be deleted"
    )


@event.listens_for(AdminDeletionItem, "before_update", propagate=True)
def _item_no_update(_mapper, _connection, target) -> None:  # pragma: no cover - guard
    """No exception at all for item snapshots, not even for the service that wrote them. If a
    snapshot were editable it would prove nothing."""
    raise DeletionLedgerImmutableError(
        f"admin_deletion_item is append-only: snapshot {target.id} cannot be updated "
        f"(deletion record {target.deletion_event_id})"
    )


@event.listens_for(AdminDeletionItem, "before_delete", propagate=True)
def _item_no_delete(_mapper, _connection, target) -> None:  # pragma: no cover - guard
    raise DeletionLedgerImmutableError(
        f"admin_deletion_item is append-only: snapshot {target.id} cannot be deleted "
        f"(deletion record {target.deletion_event_id})"
    )
