/** Shed Visit History mock state. Pure data - the handlers live in handlers.ts next to the other
 * routes, and tests seed or override this state per case. Shapes follow the backend's
 * app/schemas/visit_history.py exactly. */
import type {
  VisitChecksheetHistory,
  VisitHistoryDetail,
  VisitHistoryRow,
} from '../types'

export const SIGNED_PDF_BYTES = '%PDF-1.7\n% signed\n%%EOF\n'

export interface HistoryMockState {
  rows: VisitHistoryRow[]
  details: Record<number, VisitHistoryDetail>
  models: string[] | null
  listError: { status: number; detail: unknown } | null
  locomotiveDetailsAvailable: boolean
  checksheetCountsAvailable: boolean
  /** Signed document route -> 'pdf' (served) or an error response. */
  documents: Record<string, 'pdf' | { status: number; detail: unknown }>
  listRequests: URLSearchParams[]
  documentRequests: { url: string; authorization: string | null }[]
}

export function historyRow(overrides: Partial<VisitHistoryRow> = {}): VisitHistoryRow {
  return {
    shed_visit_id: 101,
    loco_number: '39015',
    loco_model: 'WAP-7',
    technology: '3_PHASE',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    visit_type: 'SCHEDULED',
    status: 'IN_SHED',
    operational_phase: 'SCHEDULE_IN_PROGRESS',
    display_label: 'IA In Progress',
    arrival_at: '2026-09-10T08:00:00Z',
    arrival_condition: 'WORKING',
    schedule_started_at: '2026-09-10T12:00:00Z',
    ready_at: null,
    departed_at: null,
    departure_source: null,
    closure: null,
    booking_count: 3,
    checksheet_count: 6,
    ...overrides,
  }
}

export function historyChecksheet(overrides: Partial<VisitChecksheetHistory> = {}): VisitChecksheetHistory {
  return {
    checksheet_id: 501,
    template_id: 1,
    template_name: 'Pantograph Performa',
    schedule_family: 'MINOR',
    workflow_stage_type: 'SCHEDULE_INSPECTION',
    workflow_group: 'SCHEDULE_INSPECTION',
    section_id: 9,
    section_name: 'M1-HR',
    equipment_label: 'Pantograph PT-1',
    maintenance_type: null,
    status: 'SUBMITTED',
    created_at: '2026-09-10T13:00:00Z',
    created_by_name: 'Tech Ravi',
    submitted_at: '2026-09-10T14:00:00Z',
    submitted_by_name: 'Tech Ravi',
    approved_at: null,
    approved_by_name: null,
    rejected_at: null,
    rejected_by_name: null,
    rejection_reason: null,
    signed: false,
    signed_at: null,
    signed_by_name: null,
    signature_verification: null,
    signed_document_available: false,
    signed_document_url: null,
    requirement: { is_required: true, is_active: true },
    ...overrides,
  }
}

export function historyDetail(overrides: Partial<VisitHistoryDetail> = {}): VisitHistoryDetail {
  const visit = overrides.visit ?? historyRow()
  return {
    visit,
    timings: {
      milestones: [
        { key: 'arrival', label: 'Shed In (arrival)', at: visit.arrival_at, actor_name: null, note: null },
        { key: 'test_before_started', label: 'Test Before started', at: '2026-09-10T09:00:00Z', actor_name: 'Anil M1', note: null },
        { key: 'test_before_completed', label: 'Test Before completed', at: '2026-09-10T10:00:00Z', actor_name: 'Anil M1', note: null },
        { key: 'schedule_started', label: 'Schedule started', at: visit.schedule_started_at, actor_name: null, note: null },
        { key: 'inspection_completed', label: 'Minor Inspection completed', at: null, actor_name: null, note: null },
        { key: 'test_after_started', label: 'Test After started', at: null, actor_name: null, note: null },
        { key: 'test_after_completed', label: 'Test After completed', at: null, actor_name: null, note: null },
        { key: 'ready', label: 'Ready', at: null, actor_name: null, note: null },
        { key: 'departed', label: 'Shed Out', at: null, actor_name: null, note: null },
      ],
      spans: [
        { key: 'test_before', label: 'Test Before duration', seconds: 3600, running: false },
        { key: 'test_after', label: 'Test After duration', seconds: null, running: false },
        { key: 'total_dwell', label: 'Total shed dwell', seconds: 7200, running: true },
      ],
    },
    bookings: [],
    booking_scope: 'ALL_SECTIONS',
    checksheets: { available: true, message: null, items: [] },
    events: [
      {
        at: visit.arrival_at,
        event_type: 'SHED_IN',
        sentence: 'Shed In recorded by Anil M1',
        actor_name: 'Anil M1',
        section_name: null,
        remarks: null,
        raw: null,
      },
    ],
    event_log_filter: { shed_visit_id: visit.shed_visit_id },
    ...overrides,
  }
}

function initialState(): HistoryMockState {
  return {
    rows: [],
    details: {},
    models: ['WAG9HC', 'WAP-4', 'WAP-7'],
    listError: null,
    locomotiveDetailsAvailable: true,
    checksheetCountsAvailable: true,
    documents: {},
    listRequests: [],
    documentRequests: [],
  }
}

export const HISTORY: HistoryMockState = initialState()

export function resetHistoryMock(): void {
  Object.assign(HISTORY, initialState())
}
