"""Seed a production-faithful scratch database with the visit shapes Admin deletion must handle.

HOW THIS IS PRODUCTION-FAITHFUL, AND WHY THAT MATTERS SO MUCH HERE. The schema is loaded verbatim
from `pg_dump --schema-only` of rdcms: 37 tables, 77 foreign keys with their real ON DELETE actions,
71 CHECK constraints and both triggers. Nothing is hand-written or simplified.

That is a direct response to how migration 077 failed in production. Its proof ran against a scratch
schema built by hand WITHOUT constraints, which removed the only thing that could have caught a
unique-key collision before deployment. A deletion feature proved against a permissive schema would
be worth even less: the entire question is whether RESTRICT blocks, whether CASCADE fires early, and
whether a trigger refuses.

WHAT IS REAL AND WHAT IS SYNTHESISED. The shapes come from the production audit
(diagnostics/visit_deletion_audit.sql and visit_deletion_fixtures.sql); the ROWS are synthesised,
because the dump carries schema only and no production data is used. Two shapes do not exist in
production at all and are built deliberately:

  * SAME LOCOMOTIVE, TWO VISITS. Production has 31 visits across 31 distinct locomotives, so the
    single most important isolation guarantee - deleting one visit must not touch another visit of
    the same locomotive - has no natural fixture.
  * A COMPLETED SECTION SIGN-OFF. minor_inspection_section_signoff is empty in production: three
    visits carry a SECTION_SIGNOFF mode marker but none has produced a signature. So the signed
    sign-off and its pinned checksheets are constructed here.

Run:  python scripts/build_deletion_scratch_fixtures.py <dsn>
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import psycopg

NOW = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)

#: Visit ids are fixed and meaningful so the proof script and its failures name a shape, not a
#: number. They deliberately do not collide with production ids.
V_SIMPLE = 9001          # shape A - prod visit 45: no bookings, no checksheets
V_BOOKINGS_ONLY = 9002   # shape B - prod visit 28
V_LEGACY_DSC = 9003      # shape C - prod visit 42: APPROVED + individual signatures + PDFs
V_ALL_STATUSES = 9004    # synth  - prod 47 + 48 spread in one visit
V_TB_TA = 9005           # shape E - prod visit 40
V_SECTION_SIGNOFF = 9006 # SYNTHESISED - absent from production
V_TWIN_A = 9007          # SYNTHESISED - same locomotive as V_TWIN_B
V_TWIN_B = 9008          # SYNTHESISED
V_MAJOR = 9009           # prod visit 52: MAJOR/TOH, workflow_stage_type must be NULL


def _visit(cur, visit_id: int, loco: str, family: str, variant: str, *, admin_id: int,
           closed: bool = False) -> None:
    """One shed visit, open or historically closed.

    `closed` exists because of uq_one_open_shed_visit_per_loco, a partial unique index on
    loco_number WHERE status IN ('IN_SHED','READY'): a locomotive can have only ONE open visit. The
    "same locomotive, two visits" fixture therefore has to be one CLOSED historical visit plus one
    open current visit - which is what a real repeat visit looks like anyway. A scratch schema
    without that index would have accepted two open visits and the fixture would have been testing a
    state production cannot reach.

    A CLOSED visit must satisfy chk_closed_has_departure, chk_departure_timestamp_source,
    chk_ready_timestamp_source and chk_shed_visit_timestamp_order, so the whole lifecycle is stamped
    in order.
    """
    arrival = NOW - timedelta(days=30) if closed else NOW
    started = arrival + timedelta(hours=1)
    if closed:
        cur.execute(
            """
            INSERT INTO shed_visits (id, loco_number, arrival_at, arrival_source, schedule_family,
                schedule_variant, visit_type, status, arrival_condition, schedule_started_at,
                inspection_completed_at, ready_at, ready_source, departed_at, departure_source,
                created_by, created_at, updated_at)
            VALUES (%s, %s, %s, 'DASHBOARD', %s, %s, 'SCHEDULED', 'CLOSED', 'WORKING', %s,
                    %s, %s, 'DASHBOARD', %s, 'DASHBOARD', %s, %s, %s)
            """,
            (visit_id, loco, arrival, family, variant, started,
             started + timedelta(hours=5), started + timedelta(hours=6),
             started + timedelta(hours=8), admin_id, arrival, arrival),
        )
        return
    cur.execute(
        """
        INSERT INTO shed_visits (id, loco_number, arrival_at, arrival_source, schedule_family,
            schedule_variant, visit_type, status, arrival_condition, schedule_started_at,
            created_by, created_at, updated_at)
        VALUES (%s, %s, %s, 'DASHBOARD', %s, %s, 'SCHEDULED', 'IN_SHED', 'WORKING', %s, %s, %s, %s)
        """,
        (visit_id, loco, arrival, family, variant, started, admin_id, arrival, arrival),
    )


def _stages(cur, visit_id: int, *, sup_id: int) -> dict[str, int]:
    """TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER, honouring chk_stage_started and
    chk_stage_completed: a non-PENDING, non-COMPLETED, non-SKIPPED stage must have started_at."""
    ids = {}
    for order, (stage, status) in enumerate(
        [("TEST_BEFORE", "COMPLETED"), ("SCHEDULE_INSPECTION", "IN_PROGRESS"), ("TEST_AFTER", "PENDING")],
        start=1,
    ):
        cur.execute(
            """
            INSERT INTO shed_visit_stages (shed_visit_id, stage_type, stage_order, status,
                started_at, started_by, completed_at, completed_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                visit_id, stage, order, status,
                NOW if status != "PENDING" else None,
                sup_id if status != "PENDING" else None,
                NOW + timedelta(hours=2) if status == "COMPLETED" else None,
                sup_id if status == "COMPLETED" else None,
                NOW, NOW,
            ),
        )
        ids[stage] = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO shed_visit_events (shed_visit_id, event_type, event_time, source, created_by,
               created_at) VALUES (%s, 'SHED_IN', %s, 'DASHBOARD', %s, %s)""",
        (visit_id, NOW, sup_id, NOW),
    )
    return ids


def _booking(cur, visit_id: int, stage_id: int, *, section_id: int, user_id: int,
             defect_id: int, node_id: int, status: str = "OPEN") -> int:
    cur.execute(
        """
        INSERT INTO bookings (shed_visit_id, stage_id, booking_source, description,
            equipment_node_id, status, defect_type_id, created_by, created_at, updated_at)
        VALUES (%s, %s, 'LOG_BOOK', %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (visit_id, stage_id, f"Fixture booking on visit {visit_id}", node_id, status,
         defect_id, user_id, NOW, NOW),
    )
    booking_id = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO booking_section_assignments (booking_id, section_id, status, assigned_by,
               assigned_at, assignment_source, updated_at)
           VALUES (%s, %s, %s, %s, %s, 'AUTO_MAPPING', %s)""",
        (booking_id, section_id, status, user_id, NOW, NOW),
    )
    cur.execute(
        """INSERT INTO booking_events (booking_id, event_type, to_section_id, created_by, created_at)
           VALUES (%s, 'AUTO_ROUTED', %s, %s, %s)""",
        (booking_id, section_id, user_id, NOW),
    )
    return booking_id


def _checksheet(cur, visit_id: int, *, loco_id: int, section_id: int, template_id: int,
                field_id: int, status: str, variant: str, stage_type: str | None,
                minor_equipment_id: int | None, family: str = "MINOR",
                pdf_path: str | None = None, sign: bool = False, sup_id: int | None = None,
                values: int = 3) -> int:
    cur.execute(
        """
        INSERT INTO checksheet_header (locomotive_id, section_id, template_id, technician_mobile,
            status, shed_visit_id, schedule_family, schedule_variant, workflow_stage_type,
            minor_inspection_equipment_id, pdf_path, approved_at, approved_by, created_at)
        VALUES (%s, %s, %s, '9000000011', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (loco_id, section_id, template_id, status, visit_id, family, variant, stage_type,
         minor_equipment_id, pdf_path,
         NOW + timedelta(hours=3) if status == "APPROVED" else None,
         sup_id if status == "APPROVED" else None, NOW),
    )
    checksheet_id = cur.fetchone()[0]
    for index in range(values):
        cur.execute(
            """INSERT INTO checksheet_value (checksheet_id, field_id, field_value, created_at)
               VALUES (%s, %s, %s, %s)""",
            (checksheet_id, field_id, f"OK-{index}", NOW),
        )
    if sign:
        cur.execute(
            """
            INSERT INTO digital_signatures (checksheet_id, supervisor_id, supervisor_name,
                supervisor_employee_id, certificate_subject, certificate_issuer,
                certificate_serial_number, certificate_thumbprint, certificate_valid_from,
                certificate_valid_to, signing_timestamp, signature_hash, verification_status,
                provider, created_at)
            VALUES (%s, %s, 'Fixture Supervisor', 'SUP1', 'CN=Fixture Supervisor', 'CN=Fixture CA',
                %s, %s, %s, %s, %s, %s, 'VERIFIED', 'EMBRIDGE', %s)
            """,
            (checksheet_id, sup_id, f"SER{checksheet_id:06d}", f"{checksheet_id:064d}",
             NOW - timedelta(days=365), NOW + timedelta(days=365), NOW, f"{checksheet_id:064d}", NOW),
        )
    cur.execute(
        """INSERT INTO notifications (user_id, title, message, type, checksheet_id, is_read,
               created_at) VALUES (%s, 'Fixture', 'Fixture notification', 'INFO', %s, false, %s)""",
        (sup_id, checksheet_id, NOW),
    )
    return checksheet_id


def seed(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # ---------------------------------------------------------------- masters ----
        cur.execute("INSERT INTO sections (name, code) VALUES ('M1-HR','M1-HR'),('M6-HR','M6-HR') RETURNING id")
        cur.execute("SELECT id FROM sections ORDER BY id")
        section_ids = [r[0] for r in cur.fetchall()]
        m1, m6 = section_ids[0], section_ids[1]

        cur.execute(
            """INSERT INTO users (employee_id, name, mobile, role, is_active, password_hash, section_id)
               VALUES ('ADM1','Admin One','9000000001','Admin',true,'x',NULL),
                      ('ADM2','Admin Two','9000000002','Admin',true,'x',NULL),
                      ('SUP1','Supervisor','9000000003','Supervisor',true,'x',%s),
                      ('TEC1','Technician','9000000011','Technician',true,'x',%s)
               RETURNING id""",
            (m1, m1),
        )
        cur.execute("SELECT id, employee_id FROM users ORDER BY id")
        users = {e: i for i, e in cur.fetchall()}
        admin, sup = users["ADM1"], users["SUP1"]

        cur.execute(
            """INSERT INTO locomotives (loco_number, loco_model, technology, is_active)
               VALUES ('30635','WAP-7','3_PHASE',true), ('43553','WAP-7','3_PHASE',true),
                      ('44432','WAG-9','3_PHASE',true), ('32032','WAP-7','3_PHASE',true),
                      ('30532','WAP-7','3_PHASE',true), ('30822','WAP-7','3_PHASE',true),
                      ('99999','WAP-7','3_PHASE',true), ('39078','WAP-7','3_PHASE',true)
               RETURNING id""")
        cur.execute("SELECT id, loco_number FROM locomotives ORDER BY id")
        locos = {n: i for i, n in cur.fetchall()}

        cur.execute("""INSERT INTO equipment (equipment_code, equipment_name, is_active)
               VALUES ('PANTO','Pantograph', true) RETURNING id""")
        equipment_id = cur.fetchone()[0]

        # SIX Minor equipment slots, each with its own performa. One is not enough:
        # uq_checksheet_minor_inspection_instance is UNIQUE (shed_visit_id, workflow_stage_type,
        # minor_inspection_equipment_id), so a visit can hold exactly ONE Schedule Inspection
        # checksheet per equipment. Seeding five statuses on one visit therefore needs five slots.
        # A hand-built scratch schema without that index would have accepted the duplicates and the
        # fixtures would have been quietly unlike production.
        slots: list[tuple[int, int, int]] = []
        for order, code in enumerate(("HB", "SB", "VCD", "FB", "PT", "CONV"), start=1):
            cur.execute(
                """INSERT INTO minor_inspection_equipment (section_id, technology, source_code,
                       source_code_is_printed, name, display_order, is_active)
                   VALUES (%s,'3_PHASE',%s,false,%s,%s,true) RETURNING id""",
                (m1, code, f"{code} CUBICLE", order))
            eq_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO checksheet_templates (template_code, version, section_id, technology,
                       template_name, is_active, minor_inspection_equipment_id,
                       minor_inspection_performa_variant)
                   VALUES (%s,1,%s,'3_PHASE',%s,true,%s,'STANDARD') RETURNING id""",
                (f"FIX-MINSP-{code}", m1, f"{code} CUBICLE", eq_id))
            tpl_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO template_fields (template_id, field_key, field_label, field_type,
                       required, display_order)
                   VALUES (%s,%s,%s,'select',true,1) RETURNING id""",
                (tpl_id, f"{code.lower()}_check", f"Check {code}"))
            slots.append((eq_id, tpl_id, cur.fetchone()[0]))
        minor_equipment_id, minor_template, minor_field = slots[0]
        cur.execute(
            """INSERT INTO checksheet_templates (template_code, version, equipment_id, technology,
                   template_name, is_active)
               VALUES ('FIX-TB',1,%s,'3_PHASE','Test Before',true) RETURNING id""", (equipment_id,))
        generic_template = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO template_fields (template_id, field_key, field_label, field_type,
                   required, display_order)
               VALUES (%s,'tb_check','Check TB','select',true,1) RETURNING id""", (generic_template,))
        generic_field = cur.fetchone()[0]

        cur.execute("INSERT INTO booking_defect_types (code, name) VALUES ('ISOLATED','Isolated') RETURNING id")
        defect_id = cur.fetchone()[0]

        # A real applicability row. chk_checksheet_requirement_model_coherent demands that a
        # requirement is either APPLICABILITY with an applicability_id AND a workflow_stage_type, or
        # one of the MAJOR_* sources with both NULL - so a Minor requirement cannot be faked without
        # one of these.
        cur.execute(
            """INSERT INTO checksheet_template_applicability (template_id, schedule_family,
                   schedule_variant, workflow_stage_type, is_required, is_active)
               VALUES (%s,'MINOR','IA','SCHEDULE_INSPECTION',true,true) RETURNING id""",
            (minor_template,))
        applicability_id = cur.fetchone()[0]

        def slot(index: int, loco_id: int) -> dict:
            """Keyword arguments for one Minor checksheet, using slot `index`. Each index is a
            distinct equipment, which is what uq_checksheet_minor_inspection_instance requires."""
            eq_id, tpl_id, field_id = slots[index % len(slots)]
            return dict(loco_id=loco_id, section_id=m1, template_id=tpl_id, field_id=field_id,
                        minor_equipment_id=eq_id, sup_id=sup)

        # ------------------------------------------------- A. simplest possible visit ----
        _visit(cur, V_SIMPLE, "30635", "MINOR", "IC", admin_id=admin)
        _stages(cur, V_SIMPLE, sup_id=sup)

        # ------------------------------------------------------- B. bookings, no sheets ----
        _visit(cur, V_BOOKINGS_ONLY, "44432", "MINOR", "IB", admin_id=admin)
        stages = _stages(cur, V_BOOKINGS_ONLY, sup_id=sup)
        for _ in range(2):
            _booking(cur, V_BOOKINGS_ONLY, stages["SCHEDULE_INSPECTION"], section_id=m1,
                     user_id=admin, defect_id=defect_id, node_id=1843)

        # ------------------------------- C. legacy per-checksheet DSC, with PDFs on disk ----
        _visit(cur, V_LEGACY_DSC, "43553", "MINOR", "IC", admin_id=admin)
        stages = _stages(cur, V_LEGACY_DSC, sup_id=sup)
        for _ in range(3):
            _booking(cur, V_LEGACY_DSC, stages["SCHEDULE_INSPECTION"], section_id=m1,
                     user_id=admin, defect_id=defect_id, node_id=1843)
        legacy_signed = []
        for index in range(4):
            legacy_signed.append(_checksheet(
                cur, V_LEGACY_DSC, status="APPROVED", variant="IC",
                stage_type="SCHEDULE_INSPECTION", sign=True,
                pdf_path=f"__STORAGE__/checksheet_legacy_{index}_signed.pdf",
                **slot(index, locos["43553"])))
        cur.execute(
            "INSERT INTO shed_visit_checksheet_signoff_mode (shed_visit_id, mode) VALUES (%s,'PER_CHECKSHEET_DSC')",
            (V_LEGACY_DSC,))

        # ------------------------------------------ D. every checksheet status in one visit ----
        _visit(cur, V_ALL_STATUSES, "32032", "MINOR", "IA", admin_id=admin)
        _stages(cur, V_ALL_STATUSES, sup_id=sup)
        for index, status in enumerate(("DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED")):
            _checksheet(cur, V_ALL_STATUSES, status=status, variant="IA",
                        stage_type="SCHEDULE_INSPECTION", **slot(index, locos["32032"]))

        # ------------------------------------------------------------- E. TB / TA sheets ----
        _visit(cur, V_TB_TA, "30532", "MINOR", "IC", admin_id=admin)
        _stages(cur, V_TB_TA, sup_id=sup)
        for stage_type in ("TEST_BEFORE", "TEST_AFTER"):
            # TB/TA instances carry no Minor equipment: they use the generic equipment template.
            _checksheet(cur, V_TB_TA, loco_id=locos["30532"], section_id=m1,
                        template_id=generic_template, field_id=generic_field, status="APPROVED",
                        variant="IC", stage_type=stage_type, minor_equipment_id=None,
                        sign=True, sup_id=sup,
                        pdf_path=f"__STORAGE__/checksheet_{stage_type.lower()}_signed.pdf")
        _checksheet(cur, V_TB_TA, status="SUBMITTED", variant="IC",
                    stage_type="SCHEDULE_INSPECTION", **slot(0, locos["30532"]))

        # -------------------------------- F. COMPLETED SECTION SIGN-OFF (synthesised) ----
        # Production has zero minor_inspection_section_signoff rows, so this whole shape is built
        # here. The pins must reference MINOR equipment checksheets or
        # trg_minor_signoff_checksheet_is_minor refuses the insert - which is itself worth exercising.
        _visit(cur, V_SECTION_SIGNOFF, "30822", "MINOR", "IC", admin_id=admin)
        _stages(cur, V_SECTION_SIGNOFF, sup_id=sup)
        pinned = [
            _checksheet(cur, V_SECTION_SIGNOFF, status="APPROVED", variant="IC",
                        stage_type="SCHEDULE_INSPECTION",
                        pdf_path=f"__STORAGE__/checksheet_pinned_{i}_signed.pdf",
                        **slot(i, locos["30822"]))
            for i in range(3)
        ]
        cur.execute(
            """
            INSERT INTO minor_inspection_section_signoff (shed_visit_id, section_id,
                schedule_variant, technology, locomotive_id, locomotive_number_snapshot,
                section_name_snapshot, status, signed_by_user_id, signed_by_name,
                signed_by_employee_id, signed_at, signed_pdf_path, document_hash,
                certificate_subject, certificate_issuer, certificate_serial_number,
                certificate_thumbprint, certificate_valid_from, certificate_valid_to,
                signature_hash, verification_status, provider, created_at, updated_at)
            VALUES (%s,%s,'IC','3_PHASE',%s,'30822','M1-HR','SIGNED',%s,'Fixture Supervisor','SUP1',
                %s,'__STORAGE__/section_signoff_signed.pdf',%s,'CN=Fixture Supervisor','CN=Fixture CA',
                'SER-SOF-1',%s,%s,%s,%s,'VERIFIED','EMBRIDGE',%s,%s) RETURNING id
            """,
            (V_SECTION_SIGNOFF, m1, locos["30822"], sup, NOW, "d" * 64, "t" * 64,
             NOW - timedelta(days=365), NOW + timedelta(days=365), "s" * 64, NOW, NOW),
        )
        signoff_id = cur.fetchone()[0]
        for order, checksheet_id in enumerate(pinned, start=1):
            cur.execute(
                """INSERT INTO minor_inspection_section_signoff_checksheet (signoff_id,
                       checksheet_header_id, display_order, created_at) VALUES (%s,%s,%s,%s)""",
                (signoff_id, checksheet_id, order, NOW),
            )
        cur.execute(
            "INSERT INTO shed_visit_checksheet_signoff_mode (shed_visit_id, mode) VALUES (%s,'SECTION_SIGNOFF')",
            (V_SECTION_SIGNOFF,))

        # ------------------------- G/H. SAME LOCOMOTIVE, TWO VISITS (synthesised) ----
        # Production has 31 visits across 31 distinct locomotives, so the isolation guarantee that
        # matters most has no natural fixture.
        # A closed historical visit and the current open one, both for locomotive 99999.
        for visit_id, variant, closed in ((V_TWIN_A, "IA", True), (V_TWIN_B, "IB", False)):
            _visit(cur, visit_id, "99999", "MINOR", variant, admin_id=admin, closed=closed)
            stages = _stages(cur, visit_id, sup_id=sup)
            _booking(cur, visit_id, stages["SCHEDULE_INSPECTION"], section_id=m1, user_id=admin,
                     defect_id=defect_id, node_id=1843)
            for index, status in enumerate(("APPROVED", "SUBMITTED")):
                _checksheet(cur, visit_id, status=status, variant=variant,
                            stage_type="SCHEDULE_INSPECTION", sign=(status == "APPROVED"),
                            pdf_path=(f"__STORAGE__/twin_{visit_id}_{status}.pdf"
                                      if status == "APPROVED" else None),
                            **slot(index, locos["99999"]))
        cur.execute(
            "INSERT INTO shed_visit_checksheet_signoff_mode (shed_visit_id, mode) VALUES (%s,'PER_CHECKSHEET_DSC')",
            (V_TWIN_A,))

        # ------------------------------------------------------- I. MAJOR / TOH visit ----
        # workflow_stage_type MUST be NULL for MAJOR - chk_checksheet_visit_family_model.
        _visit(cur, V_MAJOR, "39078", "MAJOR", "TOH", admin_id=admin)
        _checksheet(cur, V_MAJOR, loco_id=locos["39078"], section_id=m6,
                    template_id=generic_template, field_id=generic_field, status="REJECTED",
                    variant="TOH", stage_type=None, minor_equipment_id=None, family="MAJOR",
                    sup_id=sup)

        # ---------------------------------------------- work packages for two visits ----
        for visit_id in (V_LEGACY_DSC, V_ALL_STATUSES):
            cur.execute(
                """INSERT INTO shed_visit_checksheet_packages (shed_visit_id, generated_at,
                       generated_by, minor_inspection_configuration_complete)
                   VALUES (%s,%s,%s,true) RETURNING id""", (visit_id, NOW, admin))
            package_id = cur.fetchone()[0]
            # One requirement per TEMPLATE: uq_checksheet_requirement_identity is UNIQUE
            # (package_id, template_id, workflow_stage_type, section_id_snapshot,
            # equipment_id_snapshot, maintenance_type_snapshot), so three rows for one template
            # collide. Production generates one requirement per equipment slot, which is what this
            # mirrors.
            for eq_id, tpl_id, _field in slots[:3]:
                cur.execute(
                    """INSERT INTO shed_visit_checksheet_requirements (package_id, template_id,
                           applicability_id, workflow_stage_type, is_required, is_active,
                           technology_snapshot, section_id_snapshot, section_name_snapshot,
                           template_name_snapshot, requirement_source, created_at, updated_at)
                       VALUES (%s,%s,%s,'SCHEDULE_INSPECTION',true,true,'3_PHASE',%s,'M1-HR',
                               'HB CUBICLE','APPLICABILITY',%s,%s)""",
                    (package_id, tpl_id, applicability_id, m1, NOW, NOW))

        conn.commit()
        return {
            "sections": m1, "admin": admin, "admin2": users["ADM2"], "supervisor": sup,
            "technician": users["TEC1"], "minor_equipment": minor_equipment_id,
            "minor_template": minor_template, "generic_template": generic_template,
            "equipment": equipment_id, "signoff": signoff_id,
        }


if __name__ == "__main__":
    ids = seed(sys.argv[1])
    print("seeded:", ids)
