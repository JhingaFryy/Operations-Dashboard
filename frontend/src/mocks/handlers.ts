import { http, HttpResponse } from 'msw'
import {
  ASSIGNMENTS,
  BOOKINGS,
  BOOKING_EVENTS,
  CHECKSHEET_INTEGRATION,
  CHECKSHEET_WORK_PACKAGES,
  DEFECT_TYPES,
  FAMILIES,
  LOCO_FAMILY,
  LOCOMOTIVES,
  MAPPINGS,
  NODES,
  SECTIONS,
  SHED_VISITS,
  derivePhaseFixture,
  WORKFLOW_VISITS,
  addBooking,
  addBookingEvent,
  addShedVisit,
  nodePath,
  recomputeBookingStatus,
  resolveSections,
  type FixtureBooking,
  type FixtureNode,
} from './fixtures'
import { HISTORY, SIGNED_PDF_BYTES } from './visitHistory'

interface MockUser {
  token: string
  employee_id: string
  password: string
  me: {
    id: number
    employee_id: string
    name: string
    role: string
    section: { id: number; code: string; name: string } | null
    dashboard_access: boolean
    permissions: {
      can_add_booking_sections: boolean
      can_manage_equipment_mapping: boolean
      /** Derived, never fixture-set: authz.can_route_bookings = Admin OR a PPIO planner OR the
       *  explicit dashboard_access flag. Fixtures that need the exceptional third case set
       *  `can_route_bookings_grant`. */
      can_route_bookings?: boolean
    }
    capabilities?: {
      can_access_operations_dashboard: boolean
      can_manage_loco_movement: boolean
      can_manage_own_section_bookings: boolean
      can_access_all_sections: boolean
      can_create_equipment: boolean
      can_admin: boolean
      is_planning_section: boolean
    }
  }
  /** The exceptional explicit routing grant, for a NON-planning account. */
  can_route_bookings_grant?: boolean
}

/** Mirrors the server's app/core/authz.capabilities_for, so the mock never describes a policy the
 *  backend does not actually implement. Operations Dashboard is Supervisor-only; locomotive
 *  movement additionally requires a movement section. */
// PPIO WAS REMOVED FROM THIS SET. It was listed here, matching the old
// LOCO_MOVEMENT_SECTION_CODES default, on the assumption that PPIO would handle shed movement.
// PPIO is a PLANNING section: read-only across the workflow surfaces, with booking routing as its
// only write. The backend default is now "SHIFT" alone.
const MOVEMENT_SECTION_CODES = new Set(['SHIFT'])
/** app/core/authz.PLANNING_SECTION_CODES. A planning section performs no work and is never a
 *  routing destination; an account in one has no section work queue. */
const PLANNING_SECTION_CODES = new Set(['PPIO'])
/** app/core/authz.EQUIPMENT_CREATION_SECTION_CODE - deliberately NOT the movement set. */
const EQUIPMENT_CREATION_SECTION_CODE = 'SHIFT'

function deriveCapabilities(me: MockUser['me']): NonNullable<MockUser['me']['capabilities']> {
  // Admin is authorised by ROLE ALONE - no dashboard_access row required. Supervisors need the
  // entitlement, and movement additionally needs a movement section.
  const admin = me.role === 'Admin'
  const entitledSupervisor = me.role === 'Supervisor' && me.dashboard_access
  const access = admin || entitledSupervisor
  const planning = Boolean(me.section && PLANNING_SECTION_CODES.has(me.section.code))
  return {
    can_access_operations_dashboard: access,
    // authz.is_planning_user - the RESOLVED section kind, which the frontend reads rather than
    // inferring "planner" from the absence of another capability.
    is_planning_section: planning,
    can_manage_loco_movement:
      admin || (entitledSupervisor && Boolean(me.section && MOVEMENT_SECTION_CODES.has(me.section.code))),
    can_manage_own_section_bookings: access && (me.section !== null || admin),
    can_access_all_sections: admin,
    can_create_equipment:
      admin ||
      (entitledSupervisor && me.section?.code === EQUIPMENT_CREATION_SECTION_CODE),
    can_admin: admin,
  }
}

/** Mirrors authz.can_route_bookings: Admin, OR a Supervisor in a planning section (no row
 *  required - PPIO identity IS the capability), OR an explicit grant on an exceptional account. */
function derivePermissions(user: MockUser): NonNullable<MockUser['me']['permissions']> {
  const me = user.me
  const admin = me.role === 'Admin'
  const planning = Boolean(me.section && PLANNING_SECTION_CODES.has(me.section.code))
  const ppioPlanner = me.role === 'Supervisor' && planning
  return {
    ...me.permissions,
    can_route_bookings: admin || ppioPlanner || Boolean(user.can_route_bookings_grant),
  }
}

export const USERS: MockUser[] = [
  {
    token: 'token-admin',
    employee_id: 'ADMIN1',
    password: 'pass',
    me: {
      id: 1,
      employee_id: 'ADMIN1',
      name: 'Alice Admin',
      role: 'Admin',
      section: null,
      dashboard_access: true,
      permissions: { can_add_booking_sections: true, can_manage_equipment_mapping: true },
    },
  },
  {
    token: 'token-sup-permitted',
    employee_id: 'SUPOK',
    password: 'pass',
    me: {
      id: 2,
      employee_id: 'SUPOK',
      name: 'Sam Supervisor',
      role: 'Supervisor',
      section: null,
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: true },
    },
  },
  {
    token: 'token-sup-denied',
    employee_id: 'SUPNO',
    password: 'pass',
    me: {
      id: 3,
      employee_id: 'SUPNO',
      name: 'Sue Supervisor',
      role: 'Supervisor',
      section: null,
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
    },
  },
  {
    // Supervisor of a MOVEMENT section (SHIFT) - normal Supervisor rights plus locomotive
    // movement, and no admin capability whatsoever.
    // PPIOTEST, as it exists in production: role Supervisor, section PPIO, and NO
    // dashboard_access routing grant. Planning capability comes from the section itself, which is
    // exactly what this fixture exists to prove.
    token: 'token-sup-ppio',
    employee_id: 'PPIOTEST',
    password: 'pass',
    me: {
      id: 90,
      employee_id: 'PPIOTEST',
      name: 'Priya Planner',
      role: 'Supervisor',
      section: { id: 90, code: 'PPIO', name: 'PPIO' },
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
    },
  },
  {
    token: 'token-sup-shift',
    employee_id: 'SHIFTSUP',
    password: 'pass',
    me: {
      id: 20,
      employee_id: 'SHIFTSUP',
      name: 'Sam Shift',
      role: 'Supervisor',
      section: { id: 18, code: 'SHIFT', name: 'SHIFT' },
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
    },
  },
  {
    // Supervisor with their own section (M1-HR), no permission to add sections.
    token: 'token-sup-m1hr',
    employee_id: 'SUPM1',
    password: 'pass',
    me: {
      id: 4,
      employee_id: 'SUPM1',
      name: 'Priya Supervisor',
      role: 'Supervisor',
      section: { id: 9, code: 'M1-HR', name: 'M1-HR' },
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
    },
  },
  {
    // Supervisor with their own section (M1-HR) AND can_add_booking_sections.
    token: 'token-sup-m1hr-addperm',
    employee_id: 'SUPM1B',
    password: 'pass',
    me: {
      id: 5,
      employee_id: 'SUPM1B',
      name: 'Ravi Supervisor',
      role: 'Supervisor',
      section: { id: 9, code: 'M1-HR', name: 'M1-HR' },
      dashboard_access: true,
      permissions: { can_add_booking_sections: true, can_manage_equipment_mapping: false },
    },
  },
  {
    // Supervisor with a *different* section (M2-HR), for cross-section 403 tests.
    token: 'token-sup-m2hr',
    employee_id: 'SUPM2',
    password: 'pass',
    me: {
      id: 6,
      employee_id: 'SUPM2',
      name: 'Nina Supervisor',
      role: 'Supervisor',
      section: { id: 8, code: 'M2-HR', name: 'M2-HR' },
      dashboard_access: true,
      permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
    },
  },
]

const ADMIN_ROLE = 'Admin'

function canOperateSection(user: MockUser, sectionId: number): boolean {
  if (user.me.role === ADMIN_ROLE) return true
  return user.me.section?.id === sectionId
}

function userForToken(request: Request): MockUser | undefined {
  const auth = request.headers.get('Authorization')
  if (!auth?.startsWith('Bearer ')) return undefined
  const token = auth.slice('Bearer '.length)
  return USERS.find((u) => u.token === token)
}

function childrenOf(familyId: number, parentId: number | null): FixtureNode[] {
  return NODES.filter((n) => n.family_id === familyId && n.parent_id === parentId)
}

// Common Booking Pool + Equipment-Bifurcated Dashboard Reform: per-stage booking-list shape
// (TEST_BEFORE/SCHEDULE_INSPECTION/TEST_AFTER), now sourced from BOOKINGS directly rather than
// grouping legacy ASSIGNMENTS rows by booking_id. `assignments` is always empty now - new
// bookings never get booking_section_assignments rows - but the field is kept so the existing
// TestBeforeBookingList-shaped frontend component doesn't need a shape change.
function stageBookingListItems(stageId: number) {
  return BOOKINGS.filter((b) => b.stage_id === stageId).map((b) => ({
    id: b.id,
    status: b.status,
    description: b.description,
    equipment_node_id: b.equipment_node_id,
    equipment_node_name: b.equipment_node_name,
    equipment_path: b.equipment_node_id ? nodePath(b.equipment_node_id) : [],
    defect_type: b.defect_type,
    assignments: [] as unknown[],
  }))
}

// Booking Pool Hardening: matches the backend's frozen-mutation message (see
// app/services/section_dashboard_service.py's _DEPRECATION_MESSAGE).
const LEGACY_MUTATION_DEPRECATION_MESSAGE =
  'Section assignments are legacy historical records. Use booking-level workflow operations.'

// The global booking pool item shape (GET/POST /api/bookings) - see
// app/schemas/booking_pool.py's BookingPoolItemOut on the backend.
function bookingPoolItem(b: FixtureBooking) {
  return {
    id: b.id,
    status: b.status,
    description: b.description,
    booking_source: b.booking_source,
    workflow_stage_type: b.workflow_stage_type,
    equipment_node_id: b.equipment_node_id,
    equipment_node_name: b.equipment_node_name,
    equipment_path: b.equipment_node_id ? nodePath(b.equipment_node_id) : [],
    defect_type: b.defect_type,
    shed_visit: {
      id: b.shed_visit_id,
      loco_number: b.loco_number,
      schedule_family: b.schedule_family,
      schedule_variant: b.schedule_variant,
    },
    created_at: b.created_at,
    started_by_name: b.started_by_name,
    started_by_section_code: b.started_by_section_code,
    started_at: b.started_at,
    attended_by_name: b.attended_by_name,
    attended_by_section_code: b.attended_by_section_code,
    attended_at: b.attended_at,
    attendance_remarks: b.attendance_remarks,
  }
}

/** The server side of "technology is not the client's to choose": a request naming a locomotive
 * gets that locomotive's family, a request naming both is refused, exactly like the real API. */
function familyForRequest(url: URL): { family: { id: number; code: string } } | { error: Response } {
  const familyCode = url.searchParams.get('family')
  const locoNumber = url.searchParams.get('loco_number')
  if (locoNumber) {
    if (familyCode) {
      return {
        error: HttpResponse.json(
          { detail: { code: 'FAMILY_NOT_ACCEPTED', message: 'The server decides the family.' } },
          { status: 422 },
        ),
      }
    }
    const code = LOCO_FAMILY[locoNumber]
    const family = FAMILIES.find((f) => f.code === code)
    if (!family) {
      return {
        error: HttpResponse.json(
          { detail: { code: 'LOCOMOTIVE_NOT_FOUND', message: 'Unknown locomotive.' } },
          { status: 422 },
        ),
      }
    }
    return { family }
  }
  const family = FAMILIES.find((f) => f.code === familyCode)
  if (!family) return { error: HttpResponse.json({ detail: 'Equipment family not found' }, { status: 404 }) }
  return { family }
}

/** When set, every Ready-capable transition refuses with the backend's CHECKSHEETS_INCOMPLETE
 * detail - the fake server's stand-in for a visit with outstanding required checksheets. */
export let READY_BLOCKER: Record<string, unknown> | null = null

export function setReadyBlocker(detail: Record<string, unknown> | null): void {
  READY_BLOCKER = detail
}

/** When set, POST /out refuses with this status and detail - any Shed Out gate refusal. */
export let SHED_OUT_BLOCKER: { status: number; detail: unknown } | null = null

export function setShedOutBlocker(blocker: { status: number; detail: unknown } | null): void {
  SHED_OUT_BLOCKER = blocker
}

export const handlers = [
  http.post('/api/auth/login', async ({ request }) => {
    const body = (await request.json()) as { employee_id: string; password: string }
    const user = USERS.find((u) => u.employee_id === body.employee_id && u.password === body.password)
    if (!user) {
      return HttpResponse.json({ detail: 'Invalid employee ID or password' }, { status: 401 })
    }
    return HttpResponse.json({ access_token: user.token, token_type: 'bearer' })
  }),

  // No handoff bootstrap in ordinary tests: every signed-out page load asks once and is told
  // there is nothing to collect, which is exactly what production does for a normal visitor.
  http.post('/api/auth/handoff/finalize', () =>
    HttpResponse.json({ detail: 'This sign-in link is no longer valid. Please sign in.' }, { status: 401 }),
  ),

  http.get('/api/auth/me', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    // Capabilities AND permissions are always server-derived; the fixture never hand-writes
    // either, so a mock user cannot claim something the real policy would refuse - and a PPIO
    // fixture with no routing grant gets routing anyway, exactly as the backend now resolves it.
    return HttpResponse.json({
      ...user.me,
      capabilities: deriveCapabilities(user.me),
      permissions: derivePermissions(user),
    })
  }),

  http.get('/api/sections', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const q = new URL(request.url).searchParams.get('q')
    const items = q
      ? SECTIONS.filter((s) => s.code.toLowerCase().includes(q.toLowerCase()))
      : SECTIONS
    return HttpResponse.json(items)
  }),

  http.get('/api/equipment/families', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(FAMILIES)
  }),

  http.get('/api/equipment/nodes', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const url = new URL(request.url)
    const scoped = familyForRequest(url)
    if ('error' in scoped) return scoped.error
    const parentIdParam = url.searchParams.get('parent_id')
    const parentId = parentIdParam ? Number(parentIdParam) : null
    return HttpResponse.json(childrenOf(scoped.family.id, parentId))
  }),

  http.get('/api/equipment/nodes/search', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const url = new URL(request.url)
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    let family: { id: number } | undefined
    if (url.searchParams.get('family') || url.searchParams.get('loco_number')) {
      const scoped = familyForRequest(url)
      if ('error' in scoped) return scoped.error
      family = scoped.family
    }
    const matches = NODES.filter(
      (n) => n.name.toLowerCase().includes(q) && (!family || n.family_id === family!.id),
    )
    return HttpResponse.json(matches.map((n) => ({ ...n, path: nodePath(n.id) })))
  }),

  http.post('/api/equipment/nodes', async ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })

    // The real boundary is app/core/authz.require_equipment_creation. Mirrored here so a
    // test that drives an unauthorised user gets the 403 the server would send, rather
    // than a success the UI merely hid the button for.
    const caps = deriveCapabilities(user.me)
    if (!caps.can_create_equipment) {
      return HttpResponse.json(
        { detail: 'Creating equipment is restricted to Admin and the SHIFT section.' },
        { status: 403 },
      )
    }

    const body = (await request.json()) as {
      loco_number: string
      name: string
      section_codes: string[]
    }
    const scoped = familyForRequest(
      new URL(`http://x/?loco_number=${encodeURIComponent(body.loco_number)}`),
    )
    if ('error' in scoped) return scoped.error

    const name = (body.name ?? '').trim()
    if (!name) {
      return HttpResponse.json(
        { detail: { code: 'NAME_REQUIRED', message: 'Equipment name must not be blank.' } },
        { status: 422 },
      )
    }
    if (!body.section_codes?.length) {
      return HttpResponse.json(
        {
          detail: {
            code: 'SECTION_REQUIRED',
            message: 'New equipment must be mapped to at least one section.',
          },
        },
        { status: 422 },
      )
    }

    // uq_equipment_node_sibling: unique among siblings, case-insensitively. New equipment
    // is always a root, so the siblings are the family's other roots.
    const clash = NODES.find(
      (n) =>
        n.family_id === scoped.family.id &&
        n.parent_id === null &&
        n.name.toLowerCase() === name.toLowerCase(),
    )
    if (clash) {
      return HttpResponse.json(
        {
          detail: {
            code: 'DUPLICATE_EQUIPMENT',
            message: `Equipment named '${clash.name}' already exists at this level`,
          },
        },
        { status: 409 },
      )
    }

    const id = Math.max(0, ...NODES.map((n) => n.id)) + 1
    const node = {
      id,
      family_id: scoped.family.id,
      parent_id: null,
      name,
      node_type: 'EQUIPMENT',
      description: null,
      has_children: false,
    }
    NODES.push(node)
    MAPPINGS[id] = [...body.section_codes]
    return HttpResponse.json({ ...node, path: [{ id, name }], section_codes: MAPPINGS[id] }, { status: 201 })
  }),

  http.get('/api/equipment/nodes/match', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const url = new URL(request.url)
    const scoped = familyForRequest(url)
    if ('error' in scoped) return scoped.error
    const canonical = (v: string) => v.trim().replace(/\s+/g, ' ').toLowerCase()
    const target = canonical(url.searchParams.get('name') ?? '')
    const items = NODES.filter(
      (n) => n.family_id === scoped.family.id && canonical(n.name) === target,
    ).map((n) => ({ ...n, path: nodePath(n.id), section_codes: MAPPINGS[n.id] ?? [] }))
    return HttpResponse.json(items)
  }),

  http.post('/api/equipment/admin/nodes', async ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    // ADMIN ONLY - narrower than the Shed In create, which SHIFT may also use. Mirrors
    // require_admin on the real endpoint.
    if (!deriveCapabilities(user.me).can_admin) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }

    const body = (await request.json()) as {
      family_code: string
      parent_id: number | null
      name: string
      section_codes: string[]
    }
    const family = FAMILIES.find((f) => f.code === body.family_code)
    if (!family) return HttpResponse.json({ detail: 'Unknown family' }, { status: 404 })

    const name = (body.name ?? '').trim().replace(/\s+/g, ' ')
    if (!name) return HttpResponse.json({ detail: 'Name required' }, { status: 422 })
    const parentId = body.parent_id ?? null
    if (parentId === null && !body.section_codes?.length) {
      return HttpResponse.json(
        {
          detail: {
            code: 'SECTION_REQUIRED',
            message: 'New equipment must be mapped to at least one section.',
          },
        },
        { status: 422 },
      )
    }

    // uq_equipment_node_sibling: unique among siblings, case- and whitespace-insensitive.
    const clash = NODES.find(
      (n) =>
        n.family_id === family.id &&
        n.parent_id === parentId &&
        n.name.trim().replace(/\s+/g, ' ').toLowerCase() === name.toLowerCase(),
    )
    if (clash) {
      return HttpResponse.json(
        {
          detail: {
            code: 'DUPLICATE_EQUIPMENT',
            message: `Equipment named '${clash.name}' already exists at this level`,
            existing_node_id: clash.id,
          },
        },
        { status: 409 },
      )
    }

    const id = Math.max(0, ...NODES.map((n) => n.id)) + 1
    const node = {
      id,
      family_id: family.id,
      parent_id: parentId,
      name,
      node_type: 'EQUIPMENT',
      description: null,
      has_children: false,
    }
    NODES.push(node)
    if (body.section_codes?.length) MAPPINGS[id] = [...body.section_codes]
    return HttpResponse.json(
      { ...node, path: nodePath(id), section_codes: MAPPINGS[id] ?? [] },
      { status: 201 },
    )
  }),

  http.post('/api/equipment/nodes/:id/sections', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const me = user.me
    const mayMap = me.role === 'Admin' || me.permissions.can_manage_equipment_mapping
    if (!mayMap) {
      return HttpResponse.json(
        { detail: 'Equipment mapping management permission is required.' },
        { status: 403 },
      )
    }
    const nodeId = Number(params.id)
    if (!NODES.some((n) => n.id === nodeId)) {
      return HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 })
    }
    const body = (await request.json()) as { section_codes: string[] }
    if (!body.section_codes?.length) {
      return HttpResponse.json({ detail: 'section_codes must not be empty' }, { status: 422 })
    }
    const current = MAPPINGS[nodeId] ?? []
    const alreadyPresent = body.section_codes.filter((c) => current.includes(c))
    const newlyAdded = [...new Set(body.section_codes)].filter((c) => !current.includes(c))
    // Additive and idempotent - never a second row, never a removal.
    MAPPINGS[nodeId] = [...new Set([...current, ...body.section_codes])].sort()
    return HttpResponse.json({
      equipment_node_id: nodeId,
      section_codes: MAPPINGS[nodeId],
      already_present: alreadyPresent,
      newly_added: newlyAdded,
    })
  }),

  http.get('/api/equipment/nodes/:id', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const node = NODES.find((n) => n.id === Number(params.id))
    if (!node) return HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 })
    return HttpResponse.json(node)
  }),

  // PATCH /api/equipment/admin/nodes/:id - Admin-only edit of an existing node.
  http.patch('/api/equipment/admin/nodes/:id', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== 'Admin') {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const nodeId = Number(params.id)
    const node = NODES.find((n) => n.id === nodeId)
    if (!node) return HttpResponse.json({ detail: 'Equipment not found' }, { status: 404 })

    const body = (await request.json()) as {
      name?: string
      description?: string | null
      is_active?: boolean
      section_codes?: string[]
    }

    if (body.name !== undefined) {
      const cleaned = body.name.trim().replace(/\s+/g, ' ')
      // The sibling duplicate rule, mirrored so the dialog's 409 path is exercised.
      const clash = NODES.find(
        (n) =>
          n.id !== nodeId &&
          n.family_id === node.family_id &&
          n.parent_id === node.parent_id &&
          n.name.toLowerCase() === cleaned.toLowerCase(),
      )
      if (clash) {
        return HttpResponse.json(
          {
            detail: {
              code: 'DUPLICATE_EQUIPMENT',
              message: `Equipment named '${clash.name}' already exists at this level`,
              existing_node_id: clash.id,
            },
          },
          { status: 409 },
        )
      }
      node.name = cleaned
    }
    if ('description' in body) node.description = body.description ?? null
    if (body.is_active !== undefined) node.is_active = body.is_active
    if (body.section_codes !== undefined) MAPPINGS[nodeId] = [...body.section_codes]

    return HttpResponse.json({
      ...node,
      path: [],
      section_codes: MAPPINGS[nodeId] ?? [],
    })
  }),

  http.get('/api/equipment/nodes/:id/mapping', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const nodeId = Number(params.id)
    const node = NODES.find((n) => n.id === nodeId)
    if (!node) return HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 })
    return HttpResponse.json({ equipment_node_id: nodeId, section_codes: MAPPINGS[nodeId] ?? [] })
  }),

  http.get('/api/equipment/nodes/:id/resolved-sections', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const nodeId = Number(params.id)
    const node = NODES.find((n) => n.id === nodeId)
    if (!node) return HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 })
    return HttpResponse.json(resolveSections(nodeId))
  }),

  http.put('/api/equipment/nodes/:id/mapping', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (!user.me.permissions.can_manage_equipment_mapping) {
      return HttpResponse.json({ detail: 'Equipment mapping management permission is required.' }, { status: 403 })
    }
    const nodeId = Number(params.id)
    const node = NODES.find((n) => n.id === nodeId)
    if (!node) return HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 })
    const body = (await request.json()) as { section_codes: string[] }
    MAPPINGS[nodeId] = Array.from(new Set(body.section_codes))
    return HttpResponse.json({ equipment_node_id: nodeId, section_codes: MAPPINGS[nodeId] })
  }),

  http.get('/api/locomotives/search', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const q = (new URL(request.url).searchParams.get('q') ?? '').toLowerCase()
    return HttpResponse.json(LOCOMOTIVES.filter((l) => l.loco_number.toLowerCase().includes(q)))
  }),

  http.get('/api/booking-defect-types', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(DEFECT_TYPES)
  }),

  http.get('/api/shed-visits/current', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(SHED_VISITS.filter((v) => v.status === 'IN_SHED' || v.status === 'READY'))
  }),

  http.post('/api/shed-visits/:id/start-schedule', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = SHED_VISITS.find((v) => v.id === Number(params.id))
    if (!visit) return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
    if (visit.schedule_started_at) {
      return HttpResponse.json(
        { detail: { code: 'SCHEDULE_ALREADY_STARTED', message: "This visit's schedule has already been started." } },
        { status: 409 },
      )
    }
    const body = (await request.json()) as { started_at: string }
    visit.schedule_started_at = body.started_at
    Object.assign(visit, derivePhaseFixture(visit))
    return HttpResponse.json(visit)
  }),

  http.post('/api/shed-visits/:id/complete-schedule', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = SHED_VISITS.find((v) => v.id === Number(params.id))
    if (!visit) return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
    if (!visit.schedule_started_at) {
      return HttpResponse.json(
        { detail: { code: 'SCHEDULE_NOT_STARTED', message: "This visit's schedule has not been started yet." } },
        { status: 409 },
      )
    }
    if (visit.inspection_completed_at || visit.ready_at) {
      return HttpResponse.json(
        { detail: { code: 'SCHEDULE_ALREADY_COMPLETED', message: "This visit's schedule has already been completed." } },
        { status: 409 },
      )
    }
    const body = (await request.json()) as { completed_at: string }
    // Mirrors migration 012: MINOR completes the inspection (Test After next); MAJOR becomes Ready.
    if (visit.schedule_family === 'MINOR') {
      visit.inspection_completed_at = body.completed_at
    } else {
      // MAJOR becomes READY here, so this is a Ready-capable write and carries the checksheet gate.
      if (READY_BLOCKER) return HttpResponse.json({ detail: READY_BLOCKER }, { status: 409 })
      visit.ready_at = body.completed_at
      visit.status = 'READY'
    }
    Object.assign(visit, derivePhaseFixture(visit))
    return HttpResponse.json(visit)
  }),

  http.post('/api/shed-visits/:id/mark-ready', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = SHED_VISITS.find((v) => v.id === Number(params.id))
    if (!visit) return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
    if (!visit.inspection_completed_at || visit.ready_at) {
      return HttpResponse.json(
        { detail: { code: 'INSPECTION_NOT_COMPLETED', message: 'Mark Ready is only available after Complete Schedule, and only once.' } },
        { status: 409 },
      )
    }
    // Ready is also refused while required checksheets are outstanding; tests set READY_BLOCKER
    // to exercise that refusal.
    if (READY_BLOCKER) return HttpResponse.json({ detail: READY_BLOCKER }, { status: 409 })
    const body = (await request.json()) as { ready_at: string }
    visit.ready_at = body.ready_at
    visit.status = 'READY'
    Object.assign(visit, derivePhaseFixture(visit))
    return HttpResponse.json(visit)
  }),

  http.post('/api/shed-visits/in', async ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })

    const body = (await request.json()) as {
      loco_number: string
      schedule_family: string
      schedule_variant: string
      arrival_condition: string
      arrival_at: string
      log_book_bookings: { equipment_node_id: number; defect_type_id: number; remarks: string }[]
    }

    if (!LOCOMOTIVES.some((l) => l.loco_number === body.loco_number)) {
      return HttpResponse.json(
        {
          detail: {
            code: 'UNKNOWN_LOCOMOTIVE',
            message: `Unknown or inactive locomotive: ${body.loco_number}`,
          },
        },
        { status: 422 },
      )
    }

    if (
      SHED_VISITS.some(
        (v) => v.loco_number === body.loco_number && (v.status === 'IN_SHED' || v.status === 'READY'),
      )
    ) {
      return HttpResponse.json(
        {
          detail: {
            code: 'LOCO_ALREADY_IN_SHED',
            message: `${body.loco_number} already has an open shed visit.`,
          },
        },
        { status: 409 },
      )
    }

    // Common Booking Pool reform: equipment is validated for existence only - no
    // equipment->section mapping resolution/requirement anymore.
    for (let index = 0; index < body.log_book_bookings.length; index++) {
      const booking = body.log_book_bookings[index]
      // The server re-derives the family from the locomotive and refuses equipment of the other
      // technology, whatever the form showed or sent.
      const node = NODES.find((n) => n.id === booking.equipment_node_id)
      const expected = FAMILIES.find((f) => f.code === LOCO_FAMILY[body.loco_number])
      if (node && expected && node.family_id !== expected.id) {
        return HttpResponse.json(
          {
            detail: {
              code: 'EQUIPMENT_FAMILY_MISMATCH',
              message: "That equipment does not belong to this locomotive's equipment family.",
              booking_index: index,
              equipment_node_id: booking.equipment_node_id,
            },
          },
          { status: 422 },
        )
      }
      if (!NODES.some((n) => n.id === booking.equipment_node_id)) {
        return HttpResponse.json(
          {
            detail: {
              code: 'EQUIPMENT_NOT_FOUND',
              message: 'The selected equipment could not be found.',
              booking_index: index,
              equipment_node_id: booking.equipment_node_id,
            },
          },
          { status: 422 },
        )
      }
      if (!DEFECT_TYPES.some((d) => d.id === booking.defect_type_id)) {
        return HttpResponse.json(
          {
            detail: {
              code: 'UNKNOWN_DEFECT_TYPE',
              message: 'Unknown or inactive defect type.',
              booking_index: index,
              defect_type_id: booking.defect_type_id,
            },
          },
          { status: 422 },
        )
      }
    }

    const stagesCreated = body.schedule_family === 'MINOR' ? 4 : 0
    const visit = addShedVisit({
      loco_number: body.loco_number,
      schedule_family: body.schedule_family,
      schedule_variant: body.schedule_variant,
      arrival_condition: body.arrival_condition,
      arrival_at: body.arrival_at,
      status: 'IN_SHED',
      // Shed In leaves the visit Spare: no schedule work has begun yet.
      schedule_started_at: null,
      ready_at: null,
      departed_at: null,
      booking_total: body.log_book_bookings.length,
      pending_booking_count: body.log_book_bookings.length,
    })

    for (const booking of body.log_book_bookings) {
      const node = NODES.find((n) => n.id === booking.equipment_node_id)
      const defectType = DEFECT_TYPES.find((d) => d.id === booking.defect_type_id) ?? null
      addBooking({
        status: 'OPEN',
        description: booking.remarks,
        booking_source: 'LOG_BOOK',
        workflow_stage_type: null,
        stage_id: null,
        equipment_node_id: booking.equipment_node_id,
        equipment_node_name: node?.name ?? null,
        defect_type: defectType,
        shed_visit_id: visit.id,
        loco_number: visit.loco_number,
        schedule_family: visit.schedule_family,
        schedule_variant: visit.schedule_variant,
        created_at: new Date().toISOString(),
        started_by_name: null,
        started_by_section_code: null,
        started_at: null,
        attended_by_name: null,
        attended_by_section_code: null,
        attended_at: null,
        attendance_remarks: null,
      })
    }

    return HttpResponse.json({
      id: visit.id,
      loco_number: visit.loco_number,
      status: visit.status,
      arrival_at: visit.arrival_at,
      schedule_family: visit.schedule_family,
      schedule_variant: visit.schedule_variant,
      arrival_condition: visit.arrival_condition,
      bookings_created: body.log_book_bookings.length,
      section_assignments_created: 0,
      stages_created: stagesCreated,
    })
  }),

  http.get('/api/sections/:code/assignments', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const section = SECTIONS.find((s) => s.code === params.code)
    if (!section) return HttpResponse.json({ detail: 'Section not found' }, { status: 404 })
    if (!canOperateSection(user, section.id)) {
      return HttpResponse.json(
        { detail: 'You may only operate your own section\'s assignments.' },
        { status: 403 },
      )
    }
    return HttpResponse.json(ASSIGNMENTS.filter((a) => a.section_code === params.code))
  }),

  // Booking Pool Hardening: these three legacy assignment mutation routes are FROZEN - always
  // 410, never write - mirroring the backend's section_dashboard_service._deprecated_mutation().
  // The list route above (GET /api/sections/:code/assignments) stays live/read-only.
  http.post('/api/section-assignments/:id/start', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const assignment = ASSIGNMENTS.find((a) => a.id === Number(params.id))
    if (!assignment) return HttpResponse.json({ detail: 'Assignment not found' }, { status: 404 })
    if (!canOperateSection(user, assignment.section_id)) {
      return HttpResponse.json(
        { detail: "You may only operate your own section's assignments." },
        { status: 403 },
      )
    }
    if (assignment.status !== 'OPEN' && assignment.status !== 'REOPENED') {
      return HttpResponse.json(
        { detail: `Cannot start an assignment in status ${assignment.status}.` },
        { status: 409 },
      )
    }
    assignment.status = 'IN_PROGRESS'
    assignment.started_at = new Date().toISOString()
    assignment.started_by_name = user.me.name
    recomputeBookingStatus(assignment.booking_id)
    addBookingEvent({
      booking_id: assignment.booking_id,
      event_type: 'STARTED',
      created_at: assignment.started_at,
      actor_name: user.me.name,
      remarks: null,
      from_section_code: null,
      to_section_code: assignment.section_code,
      event_data: null,
    })
    return HttpResponse.json(assignment)
  }),

  http.post('/api/section-assignments/:id/attend', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const assignment = ASSIGNMENTS.find((a) => a.id === Number(params.id))
    if (!assignment) return HttpResponse.json({ detail: 'Assignment not found' }, { status: 404 })
    if (!canOperateSection(user, assignment.section_id)) {
      return HttpResponse.json(
        { detail: "You may only operate your own section's assignments." },
        { status: 403 },
      )
    }
    if (assignment.status !== 'IN_PROGRESS') {
      return HttpResponse.json(
        { detail: `Cannot attend an assignment in status ${assignment.status}.` },
        { status: 409 },
      )
    }
    const body = (await request.json()) as { remarks: string }
    if (!body.remarks || !body.remarks.trim()) {
      return HttpResponse.json({ detail: 'remarks must not be blank' }, { status: 422 })
    }
    assignment.status = 'ATTENDED'
    assignment.attended_at = new Date().toISOString()
    assignment.attended_by_name = user.me.name
    assignment.attendance_remarks = body.remarks.trim()
    recomputeBookingStatus(assignment.booking_id)
    addBookingEvent({
      booking_id: assignment.booking_id,
      event_type: 'ATTENDED',
      created_at: assignment.attended_at,
      actor_name: user.me.name,
      remarks: assignment.attendance_remarks,
      from_section_code: null,
      to_section_code: assignment.section_code,
      event_data: null,
    })
    return HttpResponse.json(assignment)
  }),

  http.post('/api/section-assignments/:id/reopen', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Only Admin may reopen an attended assignment.' }, { status: 403 })
    }
    const assignment = ASSIGNMENTS.find((a) => a.id === Number(params.id))
    if (!assignment) return HttpResponse.json({ detail: 'Assignment not found' }, { status: 404 })
    if (assignment.status !== 'ATTENDED') {
      return HttpResponse.json(
        { detail: `Cannot reopen an assignment in status ${assignment.status}.` },
        { status: 409 },
      )
    }
    const body = (await request.json()) as { reason?: string }
    if (!body.reason || !body.reason.trim()) {
      return HttpResponse.json({ detail: 'reason must not be blank' }, { status: 422 })
    }
    assignment.status = 'REOPENED'
    recomputeBookingStatus(assignment.booking_id)
    addBookingEvent({
      booking_id: assignment.booking_id,
      event_type: 'REOPENED',
      created_at: new Date().toISOString(),
      actor_name: user.me.name,
      remarks: body.reason.trim(),
      from_section_code: null,
      to_section_code: assignment.section_code,
      event_data: null,
    })
    return HttpResponse.json(assignment)
  }),

  // Common Booking Pool + Equipment-Bifurcated Dashboard Reform: the global booking list and its
  // booking-level start/attend/reopen lifecycle. Visible/operable identically for Admin and
  // Supervisor - never filtered by section.
  http.get('/api/bookings', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const url = new URL(request.url)
    const shedVisitId = url.searchParams.get('shed_visit_id')
    const statusFilter = url.searchParams.get('status')
    const sourceFilter = url.searchParams.get('source')
    const equipmentNodeId = url.searchParams.get('equipment_node_id')

    let items = [...BOOKINGS]
    if (shedVisitId) items = items.filter((b) => b.shed_visit_id === Number(shedVisitId))
    if (statusFilter) items = items.filter((b) => b.status === statusFilter)
    if (sourceFilter) items = items.filter((b) => b.booking_source === sourceFilter)
    if (equipmentNodeId) items = items.filter((b) => b.equipment_node_id === Number(equipmentNodeId))

    return HttpResponse.json(items.map(bookingPoolItem))
  }),

  // Business Rule Alignment: booking-level start/attend/reopen are retired (410 Gone) - the
  // assignment-level routes above (POST /api/section-assignments/:id/start|attend|reopen) are
  // the sole authoritative lifecycle mutations again.
  http.post('/api/bookings/:bookingId/start', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(
      { detail: 'Booking-level workflow operations are retired. Use assignment-level operations.' },
      { status: 410 },
    )
  }),

  http.post('/api/bookings/:bookingId/attend', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(
      { detail: 'Booking-level workflow operations are retired. Use assignment-level operations.' },
      { status: 410 },
    )
  }),

  http.post('/api/bookings/:bookingId/reopen', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    return HttpResponse.json(
      { detail: 'Booking-level workflow operations are retired. Use assignment-level operations.' },
      { status: 410 },
    )
  }),

  // Booking Pool Hardening: FROZEN - a forward/add-section-style mutation must never become an
  // alternate booking workflow. The permission check is still enforced at this layer (matching
  // the backend's require_add_booking_sections_permission dependency, checked before the
  // deprecated service call), but nothing is ever written.
  http.post('/api/bookings/:bookingId/sections', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE && !user.me.permissions.can_add_booking_sections) {
      return HttpResponse.json(
        { detail: 'Permission to add booking sections is required.' },
        { status: 403 },
      )
    }
    return HttpResponse.json({ detail: LEGACY_MUTATION_DEPRECATION_MESSAGE }, { status: 410 })
  }),

  http.get('/api/bookings/summary', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    let sectionId: number | undefined
    if (user.me.role === ADMIN_ROLE) {
      const param = new URL(request.url).searchParams.get('section_id')
      if (!param) return HttpResponse.json({ detail: 'section_id is required for Admin.' }, { status: 422 })
      sectionId = Number(param)
    } else {
      if (!user.me.section) {
        return HttpResponse.json({ detail: 'No section is assigned to this account.' }, { status: 403 })
      }
      sectionId = user.me.section.id
    }
    const relevant = ASSIGNMENTS.filter((a) => a.section_id === sectionId)
    const today = new Date().toISOString().slice(0, 10)
    return HttpResponse.json({
      open: relevant.filter((a) => a.status === 'OPEN').length,
      in_progress: relevant.filter((a) => a.status === 'IN_PROGRESS').length,
      attended_today: relevant.filter(
        (a) => a.status === 'ATTENDED' && a.attended_at && a.attended_at.slice(0, 10) === today,
      ).length,
      reopened: relevant.filter((a) => a.status === 'REOPENED').length,
    })
  }),

  // Common Booking Pool reform: history/detail are visible to any authenticated Dashboard user -
  // no more section-scoped 403, which would otherwise make every new-flow booking (no
  // booking_section_assignments rows at all) unreadable for a Supervisor.
  http.get('/api/bookings/:bookingId/history', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const bookingId = Number(params.bookingId)
    const existsInPool = BOOKINGS.some((b) => b.id === bookingId)
    const existsLegacy = ASSIGNMENTS.some((a) => a.booking_id === bookingId)
    if (!existsInPool && !existsLegacy) {
      return HttpResponse.json({ detail: 'Booking not found' }, { status: 404 })
    }
    return HttpResponse.json(BOOKING_EVENTS.filter((e) => e.booking_id === bookingId))
  }),

  http.get('/api/bookings/:bookingId', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const bookingId = Number(params.bookingId)

    const pooled = BOOKINGS.find((b) => b.id === bookingId)
    if (pooled) {
      return HttpResponse.json({
        id: pooled.id,
        status: pooled.status,
        description: pooled.description,
        booking_source: pooled.booking_source,
        equipment_node_id: pooled.equipment_node_id,
        equipment_node_name: pooled.equipment_node_name,
        equipment_path: pooled.equipment_node_id ? nodePath(pooled.equipment_node_id) : [],
        defect_type: pooled.defect_type,
        shed_visit: {
          id: pooled.shed_visit_id,
          loco_number: pooled.loco_number,
          schedule_family: pooled.schedule_family,
          schedule_variant: pooled.schedule_variant,
          arrival_condition: null,
        },
        assignments: [] as unknown[],
      })
    }

    const related = ASSIGNMENTS.filter((a) => a.booking_id === bookingId)
    if (related.length === 0) return HttpResponse.json({ detail: 'Booking not found' }, { status: 404 })
    const first = related[0]
    return HttpResponse.json({
      id: bookingId,
      status: first.booking.status,
      description: first.booking.description,
      booking_source: first.booking.booking_source,
      equipment_node_id: first.booking.equipment_node_id,
      equipment_node_name: first.booking.equipment_node_name,
      equipment_path: first.booking.equipment_node_id ? nodePath(first.booking.equipment_node_id) : [],
      defect_type: first.booking.defect_type,
      shed_visit: first.booking.shed_visit,
      assignments: related.map((a) => ({
        id: a.id,
        section_id: a.section_id,
        section_code: a.section_code,
        assignment_source: a.assignment_source,
        status: a.status,
        assigned_at: a.assigned_at,
        started_at: a.started_at,
        started_by_name: a.started_by_name,
        attended_at: a.attended_at,
        attended_by_name: a.attended_by_name,
        attendance_remarks: a.attendance_remarks,
      })),
    })
  }),

  http.get('/api/shed-visits/:visitId/workflow', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    if (visit.schedule_family !== 'MINOR') {
      return HttpResponse.json(
        { detail: { code: 'WORKFLOW_NOT_APPLICABLE', message: 'This shed visit is not on a Minor Schedule.' } },
        { status: 409 },
      )
    }
    return HttpResponse.json(visit)
  }),

  http.post('/api/shed-visits/:visitId/stages/test-before/start', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_BEFORE')
    if (!stage) return HttpResponse.json({ detail: 'TEST_BEFORE stage not found' }, { status: 404 })
    if (stage.status !== 'PENDING') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_PENDING', message: `TEST_BEFORE is ${stage.status}.` } },
        { status: 409 },
      )
    }
    stage.status = 'IN_PROGRESS'
    stage.started_at = new Date().toISOString()
    stage.started_by = user.me.id
    return HttpResponse.json(stage)
  }),

  http.post('/api/shed-visits/:visitId/stages/test-before/complete', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_BEFORE')
    if (!stage) return HttpResponse.json({ detail: 'TEST_BEFORE stage not found' }, { status: 404 })
    if (stage.status !== 'IN_PROGRESS') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_IN_PROGRESS', message: `TEST_BEFORE is ${stage.status}.` } },
        { status: 409 },
      )
    }

    const tbAssignments = ASSIGNMENTS.filter((a) => a.booking.stage_id === stage.id)
    const pendingByBooking = new Map<number, string[]>()
    for (const a of tbAssignments) {
      if (a.status !== 'ATTENDED') {
        const list = pendingByBooking.get(a.booking_id) ?? []
        list.push(a.section_code)
        pendingByBooking.set(a.booking_id, list)
      }
    }
    if (pendingByBooking.size > 0) {
      return HttpResponse.json(
        {
          detail: {
            code: 'STAGE_BLOCKED_BY_BOOKINGS',
            stage: 'TEST_BEFORE',
            message: 'TEST_BEFORE cannot be completed while section assignments remain unattended.',
            open_bookings: Array.from(pendingByBooking.entries()).map(([booking_id, pending_sections]) => ({
              booking_id,
              pending_sections: pending_sections.sort(),
            })),
          },
        },
        { status: 409 },
      )
    }

    stage.status = 'COMPLETED'
    stage.completed_at = new Date().toISOString()
    stage.completed_by = user.me.id
    return HttpResponse.json(stage)
  }),

  http.post('/api/shed-visits/:visitId/stages/schedule-inspection/start', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const testBefore = visit.stages.find((s) => s.stage_type === 'TEST_BEFORE')
    if (testBefore && testBefore.status !== 'COMPLETED') {
      return HttpResponse.json(
        {
          detail: {
            code: 'PREVIOUS_STAGE_NOT_COMPLETED',
            message: `TEST_BEFORE is ${testBefore.status}; it must be COMPLETED before SCHEDULE_INSPECTION can start.`,
          },
        },
        { status: 409 },
      )
    }
    const stage = visit.stages.find((s) => s.stage_type === 'SCHEDULE_INSPECTION')
    if (!stage) return HttpResponse.json({ detail: 'SCHEDULE_INSPECTION stage not found' }, { status: 404 })
    if (stage.status !== 'PENDING') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_PENDING', message: `SCHEDULE_INSPECTION is ${stage.status}.` } },
        { status: 409 },
      )
    }
    stage.status = 'IN_PROGRESS'
    stage.started_at = new Date().toISOString()
    stage.started_by = user.me.id
    return HttpResponse.json(stage)
  }),

  http.post('/api/shed-visits/:visitId/stages/schedule-inspection/complete', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'SCHEDULE_INSPECTION')
    if (!stage) return HttpResponse.json({ detail: 'SCHEDULE_INSPECTION stage not found' }, { status: 404 })
    if (stage.status !== 'IN_PROGRESS') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_IN_PROGRESS', message: `SCHEDULE_INSPECTION is ${stage.status}.` } },
        { status: 409 },
      )
    }

    const siAssignments = ASSIGNMENTS.filter((a) => a.booking.stage_id === stage.id)
    const pendingByBooking = new Map<number, string[]>()
    for (const a of siAssignments) {
      if (a.status !== 'ATTENDED') {
        const list = pendingByBooking.get(a.booking_id) ?? []
        list.push(a.section_code)
        pendingByBooking.set(a.booking_id, list)
      }
    }
    if (pendingByBooking.size > 0) {
      return HttpResponse.json(
        {
          detail: {
            code: 'STAGE_BLOCKED_BY_BOOKINGS',
            stage: 'SCHEDULE_INSPECTION',
            message: 'SCHEDULE_INSPECTION cannot be completed while section assignments remain unattended.',
            open_bookings: Array.from(pendingByBooking.entries()).map(([booking_id, pending_sections]) => ({
              booking_id,
              pending_sections: pending_sections.sort(),
            })),
          },
        },
        { status: 409 },
      )
    }

    stage.status = 'COMPLETED'
    stage.completed_at = new Date().toISOString()
    stage.completed_by = user.me.id
    return HttpResponse.json(stage)
  }),

  // POST /api/shed-visits/:visitId/schedule-inspection/bookings was removed: Test Before / Test After findings originate on the Android
  // checksheet (via BL-DCMS) and Minor Inspection checksheets raise none. The backend answers 409
  // BOOKING_ORIGIN_NOT_ALLOWED; the Dashboard no longer calls it.
  http.get('/api/shed-visits/:visitId/schedule-inspection/bookings', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'SCHEDULE_INSPECTION')
    if (!stage) return HttpResponse.json({ detail: 'SCHEDULE_INSPECTION stage not found' }, { status: 404 })

    return HttpResponse.json(stageBookingListItems(stage.id))
  }),

  http.post('/api/shed-visits/:visitId/stages/test-after/start', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const testBefore = visit.stages.find((s) => s.stage_type === 'TEST_BEFORE')
    if (testBefore && testBefore.status !== 'COMPLETED') {
      return HttpResponse.json(
        {
          detail: {
            code: 'PREVIOUS_STAGE_NOT_COMPLETED',
            message: `TEST_BEFORE is ${testBefore.status}; it must be COMPLETED before TEST_AFTER can start.`,
          },
        },
        { status: 409 },
      )
    }
    const scheduleInspection = visit.stages.find((s) => s.stage_type === 'SCHEDULE_INSPECTION')
    if (scheduleInspection && scheduleInspection.status !== 'COMPLETED') {
      return HttpResponse.json(
        {
          detail: {
            code: 'PREVIOUS_STAGE_NOT_COMPLETED',
            message: `SCHEDULE_INSPECTION is ${scheduleInspection.status}; it must be COMPLETED before TEST_AFTER can start.`,
          },
        },
        { status: 409 },
      )
    }
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_AFTER')
    if (!stage) return HttpResponse.json({ detail: 'TEST_AFTER stage not found' }, { status: 404 })
    if (stage.status !== 'PENDING') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_PENDING', message: `TEST_AFTER is ${stage.status}.` } },
        { status: 409 },
      )
    }
    stage.status = 'IN_PROGRESS'
    stage.started_at = new Date().toISOString()
    stage.started_by = user.me.id
    return HttpResponse.json(stage)
  }),

  http.post('/api/shed-visits/:visitId/stages/test-after/complete', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_AFTER')
    if (!stage) return HttpResponse.json({ detail: 'TEST_AFTER stage not found' }, { status: 404 })
    if (stage.status !== 'IN_PROGRESS') {
      return HttpResponse.json(
        { detail: { code: 'STAGE_NOT_IN_PROGRESS', message: `TEST_AFTER is ${stage.status}.` } },
        { status: 409 },
      )
    }

    // Deliberately scoped to this TEST_AFTER stage's own bookings only — an
    // unattended TEST_BEFORE reference booking must never block this (see
    // the Phase 3C brief's "NO AUTOMATIC REOPEN" section).
    const taAssignments = ASSIGNMENTS.filter((a) => a.booking.stage_id === stage.id)
    const pendingByBooking = new Map<number, string[]>()
    for (const a of taAssignments) {
      if (a.status !== 'ATTENDED') {
        const list = pendingByBooking.get(a.booking_id) ?? []
        list.push(a.section_code)
        pendingByBooking.set(a.booking_id, list)
      }
    }
    if (pendingByBooking.size > 0) {
      return HttpResponse.json(
        {
          detail: {
            code: 'STAGE_BLOCKED_BY_BOOKINGS',
            stage: 'TEST_AFTER',
            message: 'TEST_AFTER cannot be completed while section assignments remain unattended.',
            open_bookings: Array.from(pendingByBooking.entries()).map(([booking_id, pending_sections]) => ({
              booking_id,
              pending_sections: pending_sections.sort(),
            })),
          },
        },
        { status: 409 },
      )
    }

    stage.status = 'COMPLETED'
    stage.completed_at = new Date().toISOString()
    stage.completed_by = user.me.id
    return HttpResponse.json(stage)
  }),

  // Operations Dashboard Phase 5B.3: Authoritative Checksheet Stage Reconciliation. Default
  // mock keeps every stage exactly as-is (a neutral "nothing changed" result) - individual
  // tests override this with server.use() to exercise a specific evidence outcome.
  http.post('/api/shed-visits/:visitId/reconcile-checksheet-stages', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    return HttpResponse.json({
      shed_visit_id: visit.shed_visit_id,
      changed: false,
      stages: visit.stages
        .filter((s) => s.stage_type !== 'SPECIAL_CHECKING')
        .map((s) => ({
          workflow_stage_type: s.stage_type,
          previous_status: s.status,
          current_status: s.status,
          checksheets_ready: null,
          bookings_ready: null,
          reason: s.status === 'COMPLETED' ? 'ALREADY_COMPLETED' : 'WORK_PACKAGE_MISSING',
        })),
    })
  }),

  // POST /api/shed-visits/:visitId/test-after/bookings was removed: Test Before / Test After findings originate on the Android
  // checksheet (via BL-DCMS) and Minor Inspection checksheets raise none. The backend answers 409
  // BOOKING_ORIGIN_NOT_ALLOWED; the Dashboard no longer calls it.
  http.get('/api/shed-visits/:visitId/test-after/bookings', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_AFTER')
    if (!stage) return HttpResponse.json({ detail: 'TEST_AFTER stage not found' }, { status: 404 })

    return HttpResponse.json(stageBookingListItems(stage.id))
  }),

  http.get('/api/shed-visits/:visitId/test-after/reference-bookings', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    // Read-only: the same shed visit's TEST_BEFORE bookings only.
    const tbAssignments = ASSIGNMENTS.filter(
      (a) => a.booking.booking_source === 'TEST_BEFORE' && a.booking.shed_visit.id === visit.shed_visit_id,
    )
    const bookingIds = Array.from(new Set(tbAssignments.map((a) => a.booking_id)))
    const items = bookingIds.map((bookingId) => {
      const assignmentsForBooking = tbAssignments.filter((a) => a.booking_id === bookingId)
      const first = assignmentsForBooking[0]
      return {
        id: bookingId,
        status: first.booking.status,
        description: first.booking.description,
        equipment_node_id: first.booking.equipment_node_id,
        equipment_node_name: first.booking.equipment_node_name,
        equipment_path: first.booking.equipment_node_id ? nodePath(first.booking.equipment_node_id) : [],
        defect_type: first.booking.defect_type,
        assignments: assignmentsForBooking.map((a) => ({
          id: a.id,
          section_id: a.section_id,
          section_code: a.section_code,
          assignment_source: a.assignment_source,
          status: a.status,
          assigned_at: a.assigned_at,
          started_at: a.started_at,
          started_by_name: a.started_by_name,
          attended_at: a.attended_at,
          attended_by_name: a.attended_by_name,
          attendance_remarks: a.attendance_remarks,
        })),
      }
    })

    return HttpResponse.json(items)
  }),

  // POST /api/shed-visits/:visitId/test-before/bookings was removed: Test Before / Test After findings originate on the Android
  // checksheet (via BL-DCMS) and Minor Inspection checksheets raise none. The backend answers 409
  // BOOKING_ORIGIN_NOT_ALLOWED; the Dashboard no longer calls it.
  http.get('/api/shed-visits/:visitId/test-before/bookings', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    const stage = visit.stages.find((s) => s.stage_type === 'TEST_BEFORE')
    if (!stage) return HttpResponse.json({ detail: 'TEST_BEFORE stage not found' }, { status: 404 })

    return HttpResponse.json(stageBookingListItems(stage.id))
  }),

  http.get('/api/shed-visits/:visitId/shed-out-eligibility', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    const requiredStages = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER']
    const stageBlockers = requiredStages.flatMap((stageType) => {
      const stage = visit.stages.find((s) => s.stage_type === stageType)
      if (!stage) return [{ stage_type: stageType, status: 'MISSING' }]
      if (stage.status !== 'COMPLETED') return [{ stage_type: stageType, status: stage.status }]
      return []
    })

    const visitAssignments = ASSIGNMENTS.filter((a) => a.booking.shed_visit.id === visit.shed_visit_id)
    const bookingIds = Array.from(new Set(visitAssignments.map((a) => a.booking_id)))
    const bookingBlockers = bookingIds.flatMap((bookingId) => {
      const forBooking = visitAssignments.filter((a) => a.booking_id === bookingId)
      const pending = forBooking.filter((a) => a.status !== 'ATTENDED')
      if (pending.length === 0) return []
      // Exactly backend BookingBlockerOut: the booking's own derived status, no per-section list.
      return [{ booking_id: bookingId, booking_source: forBooking[0].booking.booking_source, status: pending[0].status }]
    })

    return HttpResponse.json({
      shed_visit_id: visit.shed_visit_id,
      eligible: stageBlockers.length === 0 && bookingBlockers.length === 0,
      stage_blockers: stageBlockers,
      booking_blockers: bookingBlockers,
    })
  }),

  http.post('/api/shed-visits/:visitId/out', async ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json({ detail: 'Admin access required.' }, { status: 403 })
    }
    // Tests set SHED_OUT_BLOCKER to stand in for a server-side gate refusal (e.g. a MAJOR visit
    // with outstanding checksheets), exactly as READY_BLOCKER does for Ready.
    if (SHED_OUT_BLOCKER) {
      return HttpResponse.json({ detail: SHED_OUT_BLOCKER.detail }, { status: SHED_OUT_BLOCKER.status })
    }
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === Number(params.visitId))
    if (!visit) {
      // The Shed Movement register's visits (SHED_VISITS) - including MAJOR ones, which have no
      // workflow page - depart through this same endpoint.
      const registerVisit = SHED_VISITS.find((v) => v.id === Number(params.visitId))
      if (!registerVisit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
      const body = (await request.json()) as { departed_at: string }
      registerVisit.status = 'CLOSED'
      registerVisit.departed_at = body.departed_at
      Object.assign(registerVisit, derivePhaseFixture(registerVisit))
      return HttpResponse.json({
        id: registerVisit.id,
        loco_number: registerVisit.loco_number,
        status: 'CLOSED',
        departed_at: body.departed_at,
        departure_source: 'DASHBOARD',
      })
    }
    if (visit.status === 'CLOSED') {
      return HttpResponse.json(
        { detail: { code: 'VISIT_ALREADY_CLOSED', message: 'This shed visit has already been Shed Out.' } },
        { status: 409 },
      )
    }

    const body = (await request.json()) as { departed_at: string; remarks?: string | null }
    if (!body.departed_at) {
      return HttpResponse.json(
        { detail: { code: 'VALIDATION_ERROR', message: 'departed_at is required.' } },
        { status: 422 },
      )
    }
    if (new Date(body.departed_at) < new Date(visit.arrival_at)) {
      return HttpResponse.json(
        { detail: { code: 'DEPARTURE_BEFORE_ARRIVAL', message: 'departed_at cannot be before arrival_at.' } },
        { status: 422 },
      )
    }

    const requiredStages = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER']
    const stageBlockers = requiredStages.flatMap((stageType) => {
      const stage = visit.stages.find((s) => s.stage_type === stageType)
      if (!stage) return [{ stage_type: stageType, status: 'MISSING' }]
      if (stage.status !== 'COMPLETED') return [{ stage_type: stageType, status: stage.status }]
      return []
    })
    const visitAssignments = ASSIGNMENTS.filter((a) => a.booking.shed_visit.id === visit.shed_visit_id)
    const bookingIds = Array.from(new Set(visitAssignments.map((a) => a.booking_id)))
    const bookingBlockers = bookingIds.flatMap((bookingId) => {
      const forBooking = visitAssignments.filter((a) => a.booking_id === bookingId)
      const pending = forBooking.filter((a) => a.status !== 'ATTENDED')
      if (pending.length === 0) return []
      return [{ booking_id: bookingId, booking_source: forBooking[0].booking.booking_source, status: pending[0].status }]
    })
    if (stageBlockers.length > 0 || bookingBlockers.length > 0) {
      const blockedBy = [
        ...(stageBlockers.length > 0 ? ['STAGES'] : []),
        ...(bookingBlockers.length > 0 ? ['BOOKINGS'] : []),
      ]
      return HttpResponse.json(
        {
          detail: {
            code: 'SHED_OUT_BLOCKED',
            message: stageBlockers.length > 0
              ? 'Shed Out blocked: workflow stages not complete.'
              : `Shed Out blocked: ${bookingBlockers.length} booking(s) still have work outstanding.`,
            blocked_by: blockedBy,
            stage_blockers: stageBlockers,
            booking_blockers: bookingBlockers,
            checksheet_blockers: [],
            checksheets: null,
          },
        },
        { status: 409 },
      )
    }

    visit.status = 'CLOSED'
    return HttpResponse.json({
      id: visit.shed_visit_id,
      loco_number: visit.loco_number,
      status: 'CLOSED',
      departed_at: body.departed_at,
      departure_source: 'DASHBOARD',
    })
  }),

  // Operations Dashboard Integration Phase 4: read-only BL-DCMS checksheet status/progress.
  // A visit with no CHECKSHEET_INTEGRATION entry defaults to available/zero-checksheets, so
  // every existing test (which never touches this fixture) sees a clean, non-error panel.
  http.get('/api/shed-visits/:visitId/checksheets', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visitId = Number(params.visitId)
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === visitId)
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    const url = new URL(request.url)
    const stageFilter = url.searchParams.get('workflow_stage_type')
    const fixture = CHECKSHEET_INTEGRATION[visitId] ?? { available: true, items: [] }
    const items = stageFilter
      ? fixture.items.filter((i) => i.workflow_stage_type === stageFilter)
      : fixture.items

    return HttpResponse.json({
      shed_visit_id: visitId,
      source: 'BLDCMS',
      available: fixture.available,
      items: fixture.available ? items : [],
    })
  }),

  http.get('/api/shed-visits/:visitId/checksheet-summary', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visitId = Number(params.visitId)
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === visitId)
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    const fixture = CHECKSHEET_INTEGRATION[visitId] ?? { available: true, items: [] }
    if (!fixture.available) {
      return HttpResponse.json({ shed_visit_id: visitId, source: 'BLDCMS', available: false, stages: [] })
    }

    const byStage = new Map<string, typeof fixture.items>()
    for (const item of fixture.items) {
      const list = byStage.get(item.workflow_stage_type) ?? []
      list.push(item)
      byStage.set(item.workflow_stage_type, list)
    }
    const stages = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER']
      .filter((stageType) => (byStage.get(stageType) ?? []).length > 0)
      .map((stageType) => {
        const stageItems = byStage.get(stageType)!
        const approved = stageItems.filter((i) => i.approved).length
        return {
          workflow_stage_type: stageType,
          total_checksheets: stageItems.length,
          approved_checksheets: approved,
          pending_checksheets: stageItems.length - approved,
        }
      })

    return HttpResponse.json({ shed_visit_id: visitId, source: 'BLDCMS', available: true, stages })
  }),

  // Operations Dashboard Integration Phase 5B.1: materialized checksheet work packages.
  // A visit with no CHECKSHEET_WORK_PACKAGES entry defaults to "not generated yet", matching
  // production behaviour before an Admin has run generation for this visit.
  http.get('/api/shed-visits/:visitId/checksheet-work-package', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visitId = Number(params.visitId)
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === visitId)
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    const fixture = CHECKSHEET_WORK_PACKAGES[visitId]
    if (!fixture) {
      return HttpResponse.json({
        shed_visit_id: visitId,
        generated: false,
        generated_by: null,
        generated_at: null,
        stages: [],
      })
    }

    return HttpResponse.json({
      shed_visit_id: visitId,
      generated: fixture.generated,
      generated_by: fixture.generated_by,
      generated_at: fixture.generated_at,
      stages: fixture.stages,
    })
  }),

  http.post('/api/shed-visits/:visitId/checksheet-work-package', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (user.me.role !== ADMIN_ROLE) {
      return HttpResponse.json(
        { detail: 'Only Admin may generate a checksheet work package.' },
        { status: 403 },
      )
    }
    const visitId = Number(params.visitId)
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === visitId)
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    const existing = CHECKSHEET_WORK_PACKAGES[visitId]
    if (existing) {
      return HttpResponse.json({
        shed_visit_id: visitId,
        generated: existing.generated,
        generated_by: existing.generated_by,
        generated_at: existing.generated_at,
        stages: existing.stages,
      })
    }

    return HttpResponse.json(
      { detail: 'No checksheet applicability configured for this visit schedule/technology.' },
      { status: 409 },
    )
  }),

  // Operations Dashboard Phase 5B.2: Required Checksheet vs BL-DCMS APPROVED correlation.
  // Default: no work package generated yet. Individual tests override via server.use() for
  // satisfied/in-progress/not-started/unavailable scenarios, matching the established pattern
  // for the other checksheet-integration endpoints above.
  http.get('/api/shed-visits/:visitId/checksheet-requirement-progress', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const visitId = Number(params.visitId)
    const visit = WORKFLOW_VISITS.find((v) => v.shed_visit_id === visitId)
    if (!visit) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })

    return HttpResponse.json({
      shed_visit_id: visitId,
      work_package_generated: false,
      bldcms_available: false,
      stages: [],
    })
  }),

  // Shed Visit History. Filtering/paging is the server's job; this mock applies just enough of it
  // (loco number, family, status, page) for the page's own behaviour to be observable, and records
  // every query so tests can assert what was asked for.
  http.get('/api/shed-visit-history', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const params = new URL(request.url).searchParams
    HISTORY.listRequests.push(params)
    if (HISTORY.listError) {
      return HttpResponse.json({ detail: HISTORY.listError.detail }, { status: HISTORY.listError.status })
    }
    const loco = params.get('loco_number')
    const family = params.get('schedule_family')
    const status = params.get('status')
    const rows = HISTORY.rows.filter(
      (r) =>
        (!loco || r.loco_number.includes(loco)) &&
        (!family || r.schedule_family === family) &&
        (!status || r.status === status),
    )
    const page = Number(params.get('page') ?? '1')
    const pageSize = Number(params.get('page_size') ?? '25')
    return HttpResponse.json({
      items: rows.slice((page - 1) * pageSize, page * pageSize),
      total: rows.length,
      page,
      page_size: pageSize,
      locomotive_details_available: HISTORY.locomotiveDetailsAvailable,
      checksheet_counts_available: HISTORY.checksheetCountsAvailable,
    })
  }),

  http.get('/api/shed-visit-history/loco-models', ({ request }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    if (HISTORY.models === null) {
      return HttpResponse.json(
        { detail: { code: 'BLDCMS_UNAVAILABLE', message: 'Locomotive models could not be read from BL-DCMS right now.' } },
        { status: 502 },
      )
    }
    return HttpResponse.json(HISTORY.models)
  }),

  http.get('/api/shed-visit-history/:visitId/checksheets/:checksheetId/signed-document', ({ request }) => {
    const url = new URL(request.url)
    HISTORY.documentRequests.push({ url: url.pathname + url.search, authorization: request.headers.get('Authorization') })
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const doc = HISTORY.documents[url.pathname]
    if (doc === undefined) {
      return HttpResponse.json(
        { detail: { code: 'SIGNED_DOCUMENT_NOT_AVAILABLE', message: 'This checksheet has no digitally signed document.' } },
        { status: 404 },
      )
    }
    if (doc !== 'pdf') return HttpResponse.json({ detail: doc.detail }, { status: doc.status })
    return new HttpResponse(SIGNED_PDF_BYTES, {
      headers: { 'Content-Type': 'application/pdf', 'Cache-Control': 'private, no-store' },
    })
  }),

  http.get('/api/shed-visit-history/:visitId', ({ request, params }) => {
    const user = userForToken(request)
    if (!user) return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })
    const detail = HISTORY.details[Number(params.visitId)]
    if (!detail) return HttpResponse.json({ detail: 'Shed visit not found' }, { status: 404 })
    return HttpResponse.json(detail)
  }),
]
