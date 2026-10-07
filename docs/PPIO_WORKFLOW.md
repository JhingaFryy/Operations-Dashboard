# PPIO planning workflow

## 1. What PPIO is

PPIO is a **planning section, not a work-performing section.** It decides which maintenance
sections are responsible for a piece of work; it never carries out that work itself.

Everything in this document follows from that one distinction. PPIO can introduce work and
direct it; it cannot execute, progress, or close it.

In code, planning sections are defined in exactly one place:

```python
# backend/app/core/authz.py
PLANNING_SECTION_CODES: frozenset[str] = frozenset({"PPIO"})
```

`app/core/authz.py` is the single module where section codes are interpreted. Add new
planning behaviour there — do not scatter `section.code == "PPIO"` checks through routes or
React components.

## 2. Access

**A PPIO Supervisor automatically gets planning behaviour. No manual `dashboard_access`
grant is required.**

PPIO identity *is* the organizational capability. `is_ppio_planner()` is satisfied by an
active Supervisor whose section is a planning section, and `can_route_bookings()` returns
true for such a user without consulting the per-user grant table.

The `dashboard_access.can_route_bookings` column (added by migration `015`) still exists and
is still honoured. It is there for **exceptional** cases — a non-planning account that
nonetheless needs to route bookings. It is not part of normal PPIO onboarding, and granting
it to a PPIO Supervisor is redundant.

## 3. What PPIO can see

- Overview
- Loco Workflow
- Shed Visits
- Global Booking Pool

The first three are **read-only** for a planning user. PPIO can see the state of the shed in
order to plan against it, but the mutations on those pages are withheld.

## 4. What PPIO cannot do

- Use the Section Dashboard / "PPIO Bookings" section work queue
- Shed In / Shed Out
- Start Schedule / Complete Schedule
- Mark Ready
- Start Work / Attend / Close / Reopen / Delete a booking
- Edit an existing booking's content, source, equipment, or locomotive
- Assign work to PPIO itself

The last point matters: PPIO is not a valid destination for work. A planning section cannot
route work to itself, because it does not perform work.

These restrictions are enforced in the backend, not merely hidden in the UI. The frontend
hides what a user cannot do, but the route guards and service-level checks are what actually
refuse the action — calling the endpoint directly is rejected.

## 5. What PPIO can do

Two actions, both additive:

1. **Add Sections** to an existing booking
2. **Create Planning Booking**

| Action | Endpoint |
| --- | --- |
| Add sections | `POST /api/bookings/{booking_id}/section-assignments` |
| Create planning booking | `POST /api/bookings/planning` |

Both are guarded by `require_route_bookings`.

> The older `POST /api/bookings/{booking_id}/sections` route is **retired** and returns
> `410 GONE` unconditionally. It is not the planning route and must not be revived.

## 6. Add Sections semantics

The operation is **strictly additive**:

- **Additive only** — sections are added, never removed
- Any valid **non-planning** section may be a destination
- **Existing assignments are preserved** exactly as they were
- **No removal**, under any circumstances
- **No replacement** — this is not a "set the sections to X" operation
- **Duplicates are a no-op** — adding a section that is already assigned changes nothing and
  is not an error

There is no code path that deletes a section assignment. The routing service contains no
delete call and no `replace_sections` function, and its result type has no
`removed_section_ids` field — the absence is deliberate, so that "additive only" is a
property of the code rather than a convention someone has to remember.

Destination eligibility is computed dynamically: a section is a valid destination if it is
not a planning section. There is no hardcoded list of "real" work sections, and eligibility
is **not** inferred from equipment mappings, historical booking counts, or whether a section
has received work before. A newly created maintenance section is immediately a valid
destination.

Each addition records a `FORWARDED` booking event, so provenance is auditable.

## 7. Planning booking creation

`POST /api/bookings/planning` reuses the ordinary booking creation service — the same entity,
the same `CREATED` event, the same Loco Master auto-mapping, the same refusal behaviour.
Planning is a different *entry point*, not a parallel booking system.

- **`booking_source` remains `MANUAL`**, fixed server-side. The client cannot supply it.
- **`created_by` is the PPIO user**, which is how provenance is established.
- The Booking Pool shows an **"Added by PPIO"** badge, derived from the creating user's
  section — not from a stored flag.
- **Auto-mapped sections are preserved.** The equipment's mapped section is always kept.
- **Additional sections are added alongside** it with `assignment_source = MANUAL`, never
  instead of it.
- **Unmapped equipment is allowed only if the planner explicitly selects at least one valid
  section.** Normal booking creation refuses equipment with no section mapping; the planning
  path permits it *only* when the planner has named the responsible section themselves. The
  general creation path is not relaxed.
- **A zero-section booking is rejected.** A booking nobody is responsible for is not a
  booking.

The planner never supplies `booking_source`, `status`, `created_by`, or timestamps. Those are
server-owned, and accepting them from the browser would make provenance unreliable.

## 8. Authorization model

| Rule | Value |
| --- | --- |
| Planner identity | active **Supervisor** + **planning section** ⇒ planner |
| `can_route_bookings` | **true automatically** for a PPIO Supervisor |
| `can_manage_loco_movement` | **false** for PPIO |
| `dashboard_access.can_route_bookings` | retained for exceptional non-planning users |

The role is unchanged — a PPIO planner is a Supervisor, not a new role. What distinguishes
them is their section.

`can_manage_loco_movement` is governed by the `LOCO_MOVEMENT_SECTION_CODES` setting, which
defaults to `SHIFT`. **PPIO must never be added to it.** Listing a planning section there
would grant it shed movement, contradicting everything above; see
[ENVIRONMENT.md](ENVIRONMENT.md#behaviour).

The backend resolves these decisions and publishes them to the frontend through
`GET /api/auth/me`, under `capabilities`:

```
can_access_operations_dashboard
can_manage_loco_movement
can_manage_own_section_bookings
can_access_all_sections
can_route_bookings
is_planning_section
can_admin
```

**The frontend should gate on these capability flags, never on a section code.** Planner
status must not be inferred from the *absence* of some other permission — that kind of
inference is wrong in both directions and has caused real defects here.

## 9. Testing checklist

Verify in a browser as a PPIO Supervisor who has **no** `dashboard_access` row:

1. **Sidebar** shows Overview, Loco Workflow, Shed Visits, Global Booking Pool — and no
   Section Dashboard entry.
2. **Add Sections** on an existing booking adds the chosen section, leaves existing
   assignments intact, and adding an already-assigned section changes nothing.
3. **Create Planning Booking** succeeds, the booking appears in the pool with the
   "Added by PPIO" badge and `booking_source = MANUAL`, and a booking with zero sections is
   refused.
4. **PPIO is not offered as a destination** in the section picker for either action.
5. **Movement controls are hidden** (Shed In / Shed Out, schedule actions) — and calling
   those endpoints directly is **refused by the backend**, not merely hidden.

Point 5 is the one worth testing properly. Confirming the button is gone only proves the UI
hides it; the endpoint check is what proves the restriction exists.

When writing tests, do not give the PPIO fixture a `dashboard_access` grant it would not have
in production. A fixture that is more privileged than the real account hides exactly the
defects these tests exist to catch.
