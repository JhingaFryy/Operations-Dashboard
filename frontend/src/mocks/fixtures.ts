export interface FixtureNode {
  id: number
  family_id: number
  parent_id: number | null
  name: string
  node_type: string
  /** Both optional, as on the real API: the hierarchy listings omit them and the
   * create/update responses carry them. */
  description?: string | null
  is_active?: boolean
  has_children: boolean
}

export const FAMILIES = [
  { id: 1, code: '3PHASE', name: '3-Phase Locomotives', description: null },
  { id: 2, code: 'CONVENTIONAL', name: 'Conventional Locomotives', description: null },
]

// Auxiliary Converter (mapped: M35-Aux)
//   -> ABB
//     -> Converter Module
//       -> IGBT                       (no direct mapping -> inherits M35-Aux)
// Traction Converter
//   -> Siemens
//     -> Converter Module
//       -> IGBT                       (duplicate name, different ancestry)
// Contactor                            (mapped directly: M1-HR, M2-HR — exact)
// Standalone Part                      (no mapping anywhere -> NONE)
const DEFAULT_NODES: FixtureNode[] = [
  { id: 100, family_id: 1, parent_id: null, name: 'Auxiliary Converter', node_type: 'EQUIPMENT', has_children: true },
  { id: 140, family_id: 1, parent_id: 100, name: 'ABB', node_type: 'TYPE_VERSION', has_children: true },
  { id: 1732, family_id: 1, parent_id: 140, name: 'Converter Module', node_type: 'SUB_EQUIPMENT', has_children: true },
  { id: 1843, family_id: 1, parent_id: 1732, name: 'IGBT', node_type: 'COMPONENT', has_children: false },

  { id: 300, family_id: 1, parent_id: null, name: 'Traction Converter', node_type: 'EQUIPMENT', has_children: true },
  { id: 340, family_id: 1, parent_id: 300, name: 'Siemens', node_type: 'TYPE_VERSION', has_children: true },
  { id: 2732, family_id: 1, parent_id: 340, name: 'Converter Module', node_type: 'SUB_EQUIPMENT', has_children: true },
  { id: 2843, family_id: 1, parent_id: 2732, name: 'IGBT', node_type: 'COMPONENT', has_children: false },

  { id: 200, family_id: 1, parent_id: null, name: 'Contactor', node_type: 'EQUIPMENT', has_children: false },
  { id: 400, family_id: 1, parent_id: null, name: 'Standalone Part', node_type: 'EQUIPMENT', has_children: false },

  // Conventional (family 2). "Auxiliary Converter (conv)" shares a search prefix with the 3-phase
  // root on purpose, so a family leak in search would be visible rather than subtle.
  { id: 900, family_id: 2, parent_id: null, name: 'Tap Changer', node_type: 'EQUIPMENT', has_children: true },
  { id: 940, family_id: 2, parent_id: 900, name: 'GR Contactor', node_type: 'SUB_EQUIPMENT', has_children: false },
  { id: 950, family_id: 2, parent_id: null, name: 'Auxiliary Converter (conv)', node_type: 'EQUIPMENT', has_children: false },
]

/** Mutable for the duration of one test: POST /api/equipment/nodes appends to it, exactly
 * as creating equipment appends a row in Loco Master. resetFixtures() puts it back. */
export let NODES: FixtureNode[] = [...DEFAULT_NODES]

export const SECTIONS = [
  { id: 5, code: 'M35-Aux', name: 'M35-Aux' },
  { id: 8, code: 'M2-HR', name: 'M2-HR' },
  { id: 9, code: 'M1-HR', name: 'M1-HR' },
  { id: 10, code: 'M4-HR', name: 'M4-HR' },
  { id: 14, code: 'M9-HR', name: 'M9-HR' },
  // Production's SHIFT section (id 18) - the one allowed to create equipment.
  { id: 18, code: 'SHIFT', name: 'SHIFT' },
]

export const DEFAULT_MAPPINGS: Record<number, string[]> = {
  100: ['M35-Aux'],
  200: ['M1-HR', 'M2-HR'],
}

export let MAPPINGS: Record<number, string[]> = { ...DEFAULT_MAPPINGS }

export const LOCOMOTIVES = [
  { loco_number: '39126', loco_type: 'WAG9HC' },
  { loco_number: '39127', loco_type: 'WAG9HC' },
  { loco_number: '39128', loco_type: 'WAG9HC' },
  { loco_number: '22345', loco_type: 'WAG7' },
]

/** What the SERVER knows about each locomotive's technology, and therefore which equipment family
 * it answers with. The browser never sees or sends this - it is here so the fake API can behave
 * like the real one. */
export const LOCO_FAMILY: Record<string, string> = {
  '39126': '3PHASE',
  '39127': '3PHASE',
  '39128': '3PHASE',
  '22345': 'CONVENTIONAL',
}

export const DEFECT_TYPES = [
  { id: 1, code: 'DEFECTIVE', name: 'Defective' },
  { id: 2, code: 'BROKEN', name: 'Broken' },
  { id: 3, code: 'ISOLATED', name: 'Isolated' },
]

export interface FixtureShedVisit {
  id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
  arrival_condition: string | null
  arrival_at: string
  status: string
  // New shed workflow. The real GET /api/shed-visits/current always returns these (derived
  // server-side by app/services/shed_visit_phase.py), so the fixture must too - otherwise the
  // mock describes a contract the backend does not have.
  schedule_started_at: string | null
  inspection_completed_at?: string | null
  ready_at: string | null
  departed_at: string | null
  operational_phase: 'SPARE' | 'SCHEDULE_IN_PROGRESS' | 'INSPECTION_COMPLETED' | 'READY' | 'SHED_OUT'
  display_label: string
  available_actions: ('START_SCHEDULE' | 'COMPLETE_SCHEDULE' | 'MARK_READY' | 'SHED_OUT')[]
  timings: {
    waiting_seconds: number | null
    waiting_running: boolean
    schedule_seconds: number | null
    schedule_running: boolean
    ready_delay_seconds: number | null
    ready_delay_running: boolean
    total_seconds: number | null
    total_running: boolean
  }
  booking_total: number
  pending_booking_count: number
}

/** Mirrors the server's derivation so fixtures stay self-consistent when a test flips a
 *  timestamp, rather than each test hand-writing a phase that could contradict its own data. */
export function derivePhaseFixture(
  visit: Pick<
    FixtureShedVisit,
    'schedule_started_at' | 'inspection_completed_at' | 'ready_at' | 'departed_at' | 'schedule_variant'
  >,
): Pick<FixtureShedVisit, 'operational_phase' | 'display_label' | 'available_actions'> {
  const variant = visit.schedule_variant ?? ''
  if (visit.departed_at) {
    return { operational_phase: 'SHED_OUT', display_label: 'Shed Out', available_actions: [] }
  }
  if (visit.ready_at) {
    return { operational_phase: 'READY', display_label: 'Ready', available_actions: ['SHED_OUT'] }
  }
  if (visit.inspection_completed_at) {
    return {
      operational_phase: 'INSPECTION_COMPLETED',
      display_label: variant ? `${variant} Inspection Complete` : 'Inspection Complete',
      available_actions: ['MARK_READY'],
    }
  }
  if (visit.schedule_started_at) {
    return {
      operational_phase: 'SCHEDULE_IN_PROGRESS',
      display_label: variant ? `${variant} In Progress` : 'Schedule In Progress',
      available_actions: ['COMPLETE_SCHEDULE'],
    }
  }
  return {
    operational_phase: 'SPARE',
    display_label: variant ? `Spare ${variant}` : 'Spare',
    available_actions: ['START_SCHEDULE'],
  }
}

export const EMPTY_TIMINGS: FixtureShedVisit['timings'] = {
  waiting_seconds: null,
  waiting_running: false,
  schedule_seconds: null,
  schedule_running: false,
  ready_delay_seconds: null,
  ready_delay_running: false,
  total_seconds: null,
  total_running: false,
}

export let SHED_VISITS: FixtureShedVisit[] = []
let nextShedVisitId = 1

export function resetFixtures() {
  NODES = DEFAULT_NODES.map((n) => ({ ...n }))
  MAPPINGS = { ...DEFAULT_MAPPINGS }
  SHED_VISITS = []
  nextShedVisitId = 1
  ASSIGNMENTS = DEFAULT_ASSIGNMENTS.map((a) => ({
    ...a,
    booking: { ...a.booking, shed_visit: { ...a.booking.shed_visit } },
  }))
  BOOKINGS = []
  BOOKING_EVENTS = []
  nextEventId = 1
  WORKFLOW_VISITS = defaultWorkflowVisits()
  CHECKSHEET_INTEGRATION = {}
  CHECKSHEET_WORK_PACKAGES = {}
  nextTbBookingId = 9000
  nextTbAssignmentId = 9000
}

type ShedVisitSeed = Omit<
  FixtureShedVisit,
  'id' | 'operational_phase' | 'display_label' | 'available_actions' | 'timings'
> &
  Partial<Pick<FixtureShedVisit, 'timings'>>

export function addShedVisit(visit: ShedVisitSeed): FixtureShedVisit {
  const created: FixtureShedVisit = {
    ...visit,
    timings: visit.timings ?? { ...EMPTY_TIMINGS },
    ...derivePhaseFixture(visit),
    id: nextShedVisitId++,
  }
  SHED_VISITS.push(created)
  return created
}

export type FixtureStatus = 'OPEN' | 'IN_PROGRESS' | 'ATTENDED' | 'REOPENED'

export interface FixtureAssignment {
  id: number
  booking_id: number
  section_id: number
  section_code: string
  status: FixtureStatus
  assignment_source: 'AUTO_MAPPING' | 'MANUAL'
  assigned_at: string
  started_at: string | null
  started_by_name: string | null
  attended_at: string | null
  attended_by_name: string | null
  attendance_remarks: string | null
  booking: {
    id: number
    status: FixtureStatus
    description: string
    booking_source: string
    stage_id: number | null
    equipment_node_id: number | null
    equipment_node_name: string | null
    defect_type: { id: number; code: string; name: string } | null
    shed_visit: {
      id: number
      loco_number: string
      schedule_family: string | null
      schedule_variant: string | null
      arrival_condition: string | null
    }
  }
}

// Common Booking Pool + Equipment-Bifurcated Dashboard Reform: bookings are global to the shed
// visit, no longer routed via equipment-section mapping. This is the booking-level fixture store
// (distinct from the legacy per-section ASSIGNMENTS store above) backing GET/POST /api/bookings
// and the per-stage booking-list endpoints (test-before/schedule-inspection/test-after
// /bookings), which now read from here instead of grouping ASSIGNMENTS by booking_id.
export interface FixtureBooking {
  id: number
  status: FixtureStatus
  description: string
  booking_source: string
  workflow_stage_type: string | null
  stage_id: number | null
  equipment_node_id: number | null
  equipment_node_name: string | null
  defect_type: { id: number; code: string; name: string } | null
  shed_visit_id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
  created_at: string
  started_by_name: string | null
  started_by_section_code: string | null
  started_at: string | null
  attended_by_name: string | null
  attended_by_section_code: string | null
  attended_at: string | null
  attendance_remarks: string | null
}

export let BOOKINGS: FixtureBooking[] = []

export function addBooking(booking: Omit<FixtureBooking, 'id'>): FixtureBooking {
  const created = { ...booking, id: nextBookingId() }
  BOOKINGS.push(created)
  return created
}

export interface FixtureBookingEvent {
  id: number
  booking_id: number
  event_type: string
  created_at: string
  actor_name: string | null
  remarks: string | null
  from_section_code: string | null
  to_section_code: string | null
  event_data: Record<string, unknown> | null
}

const DEFAULT_ASSIGNMENTS: FixtureAssignment[] = [
  {
    id: 1,
    booking_id: 900,
    section_id: 9,
    section_code: 'M1-HR',
    status: 'OPEN',
    assignment_source: 'AUTO_MAPPING',
    assigned_at: '2026-08-31T04:05:00Z',
    started_at: null,
    started_by_name: null,
    attended_at: null,
    attended_by_name: null,
    attendance_remarks: null,
    booking: {
      id: 900,
      status: 'OPEN',
      description: 'Aux converter isolated after recurring fault',
      booking_source: 'LOG_BOOK',
      stage_id: null,
      equipment_node_id: 1843,
      equipment_node_name: 'IGBT',
      defect_type: { id: 3, code: 'ISOLATED', name: 'Isolated' },
      shed_visit: {
        id: 501,
        loco_number: '39126',
        schedule_family: 'MINOR',
        schedule_variant: 'IA',
        arrival_condition: 'WORKING',
      },
    },
  },
  {
    id: 2,
    booking_id: 901,
    section_id: 9,
    section_code: 'M1-HR',
    status: 'ATTENDED',
    assignment_source: 'AUTO_MAPPING',
    assigned_at: '2026-08-30T10:00:00Z',
    started_at: '2026-08-30T10:15:00Z',
    started_by_name: 'Sam Supervisor',
    attended_at: '2026-08-30T11:00:00Z',
    attended_by_name: 'Sam Supervisor',
    attendance_remarks: 'Replaced faulty contactor.',
    booking: {
      id: 901,
      status: 'ATTENDED',
      description: 'Contactor not engaging',
      booking_source: 'LOG_BOOK',
      stage_id: null,
      equipment_node_id: 200,
      equipment_node_name: 'Contactor',
      defect_type: { id: 1, code: 'DEFECTIVE', name: 'Defective' },
      shed_visit: {
        id: 500,
        loco_number: '39127',
        schedule_family: 'MINOR',
        schedule_variant: 'IB',
        arrival_condition: 'WORKING',
      },
    },
  },
  {
    id: 3,
    booking_id: 902,
    section_id: 9,
    section_code: 'M1-HR',
    status: 'IN_PROGRESS',
    assignment_source: 'AUTO_MAPPING',
    assigned_at: '2026-08-31T02:00:00Z',
    started_at: '2026-08-31T02:10:00Z',
    started_by_name: 'Sam Supervisor',
    attended_at: null,
    attended_by_name: null,
    attendance_remarks: null,
    booking: {
      id: 902,
      status: 'IN_PROGRESS',
      description: 'Pantograph raising issue',
      booking_source: 'LOG_BOOK',
      stage_id: null,
      equipment_node_id: 1843,
      equipment_node_name: 'IGBT',
      defect_type: { id: 1, code: 'DEFECTIVE', name: 'Defective' },
      shed_visit: {
        id: 502,
        loco_number: '39128',
        schedule_family: 'MINOR',
        schedule_variant: 'IC',
        arrival_condition: 'WORKING',
      },
    },
  },
  {
    id: 4,
    booking_id: 903,
    section_id: 8,
    section_code: 'M2-HR',
    status: 'OPEN',
    assignment_source: 'AUTO_MAPPING',
    assigned_at: '2026-08-31T01:00:00Z',
    started_at: null,
    started_by_name: null,
    attended_at: null,
    attended_by_name: null,
    attendance_remarks: null,
    booking: {
      id: 903,
      status: 'OPEN',
      description: 'Contactor tip worn',
      booking_source: 'LOG_BOOK',
      stage_id: null,
      equipment_node_id: 200,
      equipment_node_name: 'Contactor',
      defect_type: { id: 2, code: 'BROKEN', name: 'Broken' },
      shed_visit: {
        id: 503,
        loco_number: '39127',
        schedule_family: 'MINOR',
        schedule_variant: 'IA',
        arrival_condition: 'DEAD',
      },
    },
  },
]

export let ASSIGNMENTS: FixtureAssignment[] = DEFAULT_ASSIGNMENTS.map((a) => ({
  ...a,
  booking: { ...a.booking, shed_visit: { ...a.booking.shed_visit } },
}))

export let BOOKING_EVENTS: FixtureBookingEvent[] = []
let nextEventId = 1

export function addBookingEvent(event: Omit<FixtureBookingEvent, 'id'>): FixtureBookingEvent {
  const created = { ...event, id: nextEventId++ }
  BOOKING_EVENTS.push(created)
  return created
}

/** Mirrors app/services/booking_service.py:recompute_booking_status —
 * every assignment row sharing a booking_id gets its embedded booking.status
 * kept in sync, since the mock's assignments are denormalized copies
 * rather than joined from a real relational booking table. */
export function recomputeBookingStatus(bookingId: number) {
  const related = ASSIGNMENTS.filter((a) => a.booking_id === bookingId)
  if (related.length === 0) return
  const statuses = related.map((a) => a.status)
  let newStatus: FixtureStatus
  if (statuses.includes('REOPENED')) newStatus = 'REOPENED'
  else if (statuses.every((s) => s === 'ATTENDED')) newStatus = 'ATTENDED'
  else if (statuses.includes('IN_PROGRESS')) newStatus = 'IN_PROGRESS'
  else newStatus = 'OPEN'
  related.forEach((a) => {
    a.booking.status = newStatus
  })
}

export interface FixtureStage {
  id: number
  stage_type: string
  stage_order: number
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED'
  started_at: string | null
  started_by: number | null
  completed_at: string | null
  completed_by: number | null
}

export interface FixtureWorkflowVisit {
  shed_visit_id: number
  loco_number: string
  arrival_at: string
  schedule_family: string | null
  schedule_variant: string | null
  status: string
  stages: FixtureStage[]
}

// Phase 3D scope correction: SPECIAL_CHECKING was removed from the active
// Minor workflow by business decision. New visits get exactly these 3
// stages, matching backend shed_visit_service.MINOR_STAGE_SEQUENCE.
function defaultStages(): FixtureStage[] {
  return [
    { id: 7001, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
    { id: 7002, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
    { id: 7003, stage_type: 'TEST_AFTER', stage_order: 3, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
  ]
}

function defaultWorkflowVisits(): FixtureWorkflowVisit[] {
  return [
    {
      shed_visit_id: 700,
      loco_number: '39126',
      arrival_at: '2026-08-31T04:00:00Z',
      schedule_family: 'MINOR',
      schedule_variant: 'IA',
      status: 'IN_SHED',
      stages: defaultStages(),
    },
    {
      shed_visit_id: 701,
      loco_number: '39127',
      arrival_at: '2026-08-31T03:00:00Z',
      schedule_family: null,
      schedule_variant: null,
      status: 'IN_SHED',
      stages: [],
    },
  ]
}

export let WORKFLOW_VISITS: FixtureWorkflowVisit[] = defaultWorkflowVisits()

// Operations Dashboard Integration Phase 4: read-only BL-DCMS checksheet status/progress.
// Keyed by shed_visit_id. A visit with no entry here defaults to "available, zero checksheets"
// (see handlers.ts) - tests that need a specific unavailable/populated state set one explicitly.
export interface FixtureChecksheetItem {
  id: number
  template_id: number
  template_name: string | null
  section: string | null
  equipment: string | null
  loco: string | null
  schedule_family: string | null
  schedule_variant: string | null
  workflow_stage_type: string
  status: string
  approved: boolean
  signed: boolean
  signing_timestamp: string | null
  verification_status: string | null
}

export interface FixtureChecksheetIntegration {
  available: boolean
  items: FixtureChecksheetItem[]
}

export let CHECKSHEET_INTEGRATION: Record<number, FixtureChecksheetIntegration> = {}

// Operations Dashboard Integration Phase 5B.1: materialized checksheet work packages. No entry
// for a visit means "not generated yet" (the GET handler's default 200 response) - most tests
// use server.use() overrides directly rather than this store, since generation itself calls out
// to Loco Master + BL-DCMS and isn't meaningfully simulated by a static fixture.
export interface FixtureChecksheetWorkPackage {
  generated: boolean
  generated_by: number | null
  generated_at: string | null
  stages: {
    workflow_stage_type: string
    requirements: {
      applicability_id: number
      template_id: number
      template_name: string
      technology: string
      section_id: number | null
      section_name: string | null
      equipment_id: number | null
      equipment_name: string | null
      maintenance_type: string | null
      is_required: boolean
    }[]
  }[]
}

export let CHECKSHEET_WORK_PACKAGES: Record<number, FixtureChecksheetWorkPackage> = {}

let nextTbBookingId = 9000
let nextTbAssignmentId = 9000

export function nextBookingId(): number {
  return nextTbBookingId++
}

export function nextAssignmentId(): number {
  return nextTbAssignmentId++
}

export function nodePath(nodeId: number): { id: number; name: string }[] {
  const chain: FixtureNode[] = []
  let current = NODES.find((n) => n.id === nodeId)
  while (current) {
    chain.push(current)
    current = current.parent_id != null ? NODES.find((n) => n.id === current!.parent_id) : undefined
  }
  chain.reverse()
  return chain.map((n) => ({ id: n.id, name: n.name }))
}

export function resolveSections(nodeId: number): {
  equipment_node_id: number
  resolved_from_node_id: number | null
  resolution: 'EXACT' | 'ANCESTOR' | 'NONE'
  section_codes: string[]
} {
  let current = NODES.find((n) => n.id === nodeId)
  let first = true
  while (current) {
    const codes = MAPPINGS[current.id]
    if (codes && codes.length > 0) {
      return {
        equipment_node_id: nodeId,
        resolved_from_node_id: current.id,
        resolution: first ? 'EXACT' : 'ANCESTOR',
        section_codes: codes,
      }
    }
    first = false
    current = current.parent_id != null ? NODES.find((n) => n.id === current!.parent_id) : undefined
  }
  return { equipment_node_id: nodeId, resolved_from_node_id: null, resolution: 'NONE', section_codes: [] }
}
