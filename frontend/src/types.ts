export interface SectionBrief {
  id: number
  code: string
  name: string
}

export interface Permissions {
  can_add_booking_sections: boolean
  /** Migration 015. May replace a booking's maintenance-section assignments - the only
   *  Operations Dashboard write a planning section (PPIO) holds. Optional so a response from a
   *  build predating it still parses; absent means false. */
  can_route_bookings?: boolean
  can_manage_equipment_mapping: boolean
}

export interface Capabilities {
  can_access_operations_dashboard: boolean
  /** The RESOLVED section kind, from the server. The frontend reads this rather than inferring
   *  "planner" from the absence of another capability. Optional so an older response parses. */
  is_planning_section?: boolean
  can_manage_loco_movement: boolean
  can_manage_own_section_bookings: boolean
  /** Cross-section operational visibility - Admin only, never a Supervisor. */
  can_access_all_sections: boolean
  can_admin: boolean
}

export interface CurrentUser {
  id: number
  employee_id: string
  name: string
  role: string
  section: SectionBrief | null
  dashboard_access: boolean
  permissions: Permissions
  /** Server-decided (app/core/authz.py). PRESENTATION ONLY - every one is re-enforced
   *  independently on the endpoint, so hiding a control is usability, never the security
   *  boundary. */
  capabilities: Capabilities
}

export interface Section {
  id: number
  code: string
  name: string
}

export interface EquipmentFamily {
  id: number
  code: string
  name: string
  description?: string | null
}

export interface EquipmentNode {
  id: number
  family_id: number
  parent_id: number | null
  name: string
  node_type: string
  description?: string | null
  /** Optional: the hierarchy/search listings omit it (they only return active nodes anyway);
   * the create and update responses carry it. Treat an absent value as active. */
  is_active?: boolean
  has_children: boolean
}

export interface PathItem {
  id: number
  name: string
}

export interface EquipmentNodeSearchResult extends EquipmentNode {
  path: PathItem[]
}

/** A node just created through POST /api/equipment/nodes, returned with the sections it was
 * mapped to so the caller can select it without searching for it again. */
export interface EquipmentNodeCreated extends EquipmentNode {
  path: PathItem[]
  section_codes: string[]
}

/** An existing node whose name matches what the user typed, with the identity needed to
 * tell it apart from its namesakes - production reuses 1,129 names across 7,423 nodes. */
export interface EquipmentNodeMatch extends EquipmentNode {
  path: PathItem[]
  section_codes: string[]
}

export interface EquipmentSectionAddResult {
  equipment_node_id: number
  section_codes: string[]
  already_present: string[]
  newly_added: string[]
}

export interface EquipmentMapping {
  equipment_node_id: number
  section_codes: string[]
}

export type Resolution = 'EXACT' | 'ANCESTOR' | 'NONE'

export interface ResolvedSections {
  equipment_node_id: number
  resolved_from_node_id: number | null
  resolution: Resolution
  section_codes: string[]
}

export interface Locomotive {
  loco_number: string
  loco_type: string
}

export interface DefectType {
  id: number
  code: string
  name: string
}

// MINOR (IA/IA0/IB/IC/IC0) and MAJOR (IOH/TOH). IA0/IC0 are distinct variants, not IA/IC aliases. Keep in sync with the backend's
// single source of truth, app/domain/schedule.py's SCHEDULE_MATRIX — see
// src/lib/schedule.ts, which mirrors that matrix for the frontend and is
// the only place that should reason about which family a variant belongs
// to (nothing else should hardcode this pairing).
export type ScheduleFamily = 'MINOR' | 'MAJOR'
export type MinorScheduleVariant = 'IA' | 'IA0' | 'IB' | 'IC' | 'IC0'
export type MajorScheduleVariant = 'IOH' | 'TOH'
export type ScheduleVariant = MinorScheduleVariant | MajorScheduleVariant
export type ArrivalCondition = 'WORKING' | 'DEAD'

export interface LogBookBookingIn {
  equipment_node_id: number
  defect_type_id: number
  remarks: string
}

export interface ShedInRequest {
  loco_number: string
  schedule_family: ScheduleFamily
  schedule_variant: ScheduleVariant
  arrival_condition: ArrivalCondition
  arrival_at: string
  log_book_bookings: LogBookBookingIn[]
}

export interface ShedInResponse {
  id: number
  loco_number: string
  status: string
  arrival_at: string
  schedule_family: string
  schedule_variant: string
  arrival_condition: string
  bookings_created: number
  section_assignments_created: number
  stages_created: number
}

export interface ShedInErrorDetail {
  code: string
  message: string
  booking_index?: number
  equipment_node_id?: number
  defect_type_id?: number
}

/** Derived server-side by app/services/shed_visit_phase.py - the single definition. Never
 *  re-derived in the frontend, and never persisted as a status enum value. */
export type OperationalPhase =
  | 'SPARE'
  | 'SCHEDULE_IN_PROGRESS'
  /** MINOR only: the actual inspection is complete (Complete Schedule); Test After comes next. */
  | 'INSPECTION_COMPLETED'
  | 'READY'
  | 'SHED_OUT'

export type ShedVisitAction = 'START_SCHEDULE' | 'COMPLETE_SCHEDULE' | 'MARK_READY' | 'SHED_OUT'

/** Durations in whole seconds, computed from timestamps. A *_running flag marks a span whose
 *  closing timestamp has not happened yet ("2h 14m so far" vs "took 2h 14m"). */
export interface VisitTimings {
  waiting_seconds: number | null
  waiting_running: boolean
  /** Schedule start -> inspection complete (MINOR) / schedule complete (MAJOR). */
  schedule_seconds: number | null
  schedule_running: boolean
  ready_delay_seconds: number | null
  ready_delay_running: boolean
  total_seconds: number | null
  total_running: boolean
}

export interface CurrentShedVisit {
  id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
  arrival_condition: string | null
  arrival_at: string
  status: string
  schedule_started_at: string | null
  /** MINOR: the actual Minor Inspection ended (Complete Schedule). Not Ready. */
  inspection_completed_at?: string | null
  ready_at: string | null
  departed_at: string | null
  operational_phase: OperationalPhase
  /** "Spare IA" / "IA In Progress" / "Ready" - composed server-side. */
  display_label: string
  available_actions: ShedVisitAction[]
  timings: VisitTimings
  booking_total: number
  pending_booking_count: number
}

export interface ScheduleTransitionResult {
  id: number
  loco_number: string
  status: string
  schedule_family: string | null
  schedule_variant: string | null
  arrival_at: string
  schedule_started_at: string | null
  inspection_completed_at?: string | null
  ready_at: string | null
  departed_at: string | null
  operational_phase: OperationalPhase
  display_label: string
  available_actions: ShedVisitAction[]
  timings: VisitTimings
}

export type AssignmentStatus = 'OPEN' | 'IN_PROGRESS' | 'ATTENDED' | 'REOPENED'

export interface ShedVisitBrief {
  id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
  arrival_condition: string | null
}

export interface BookingBrief {
  id: number
  status: AssignmentStatus
  description: string
  booking_source: string
  equipment_node_id: number | null
  equipment_node_name: string | null
  defect_type: DefectType | null
  shed_visit: ShedVisitBrief
}

export interface SectionAssignment {
  id: number
  booking_id: number
  section_id: number
  section_code: string
  status: AssignmentStatus
  assigned_at: string
  started_at: string | null
  started_by_name: string | null
  attended_at: string | null
  attended_by_name: string | null
  attendance_remarks: string | null
  booking: BookingBrief
}

export interface SectionSummary {
  open: number
  in_progress: number
  attended_today: number
  reopened: number
}

export interface BookingHistoryEvent {
  id: number
  event_type: string
  created_at: string
  actor_name: string | null
  remarks: string | null
  from_section_code: string | null
  to_section_code: string | null
  event_data: Record<string, unknown> | null
}

export interface BookingAssignmentDetail {
  id: number
  section_id: number
  section_code: string
  assignment_source: string
  status: AssignmentStatus
  assigned_at: string
  started_at: string | null
  started_by_name: string | null
  attended_at: string | null
  attended_by_name: string | null
  attendance_remarks: string | null
}

export interface BookingDetail {
  id: number
  status: AssignmentStatus
  description: string
  booking_source: string
  equipment_node_id: number | null
  equipment_node_name: string | null
  equipment_path: PathItem[]
  defect_type: DefectType | null
  shed_visit: ShedVisitBrief
  assignments: BookingAssignmentDetail[]
}

export type StageType = 'TEST_BEFORE' | 'SCHEDULE_INSPECTION' | 'TEST_AFTER' | 'SPECIAL_CHECKING'
/** SKIPPED: Test Before only, an audited Admin skip - never a completed Test Before. */
export type StageStatus = 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'SKIPPED'

export interface WorkflowStage {
  id: number
  stage_type: StageType
  stage_order: number
  status: StageStatus
  started_at: string | null
  started_by: number | null
  completed_at: string | null
  completed_by: number | null
  skipped_at?: string | null
  skipped_by?: number | null
  skip_reason?: string | null
}

export interface Workflow {
  shed_visit_id: number
  loco_number: string
  arrival_at: string
  schedule_family: string | null
  schedule_variant: string | null
  status: string
  stages: WorkflowStage[]
  /** Schedule started under the previous workflow without a completed/skipped Test Before:
   *  grandfathered - Test Before does not hold back later stages, and nothing is fabricated for it. */
  test_before_legacy_waived?: boolean
}

export interface TestBeforeBookingIn {
  equipment_node_id: number
  defect_type_id: number
  remarks: string
}

export interface TestBeforeBookingsResult {
  bookings_created: number
  section_assignments_created: number
}

export interface StageBlockedBooking {
  booking_id: number
  pending_sections: string[]
}

export interface StageBlockedDetail {
  code: string
  message?: string
  stage?: string
  open_bookings?: StageBlockedBooking[]
  // Phase 5B.3: STAGE_NOT_READY_FOR_COMPLETION carries the same machine-readable reason the
  // reconciliation engine itself returns (see VisitReconciliationResult) - the manual complete
  // endpoints are now a thin wrapper around that single authoritative gate.
  reason?: string
  checksheets_ready?: boolean | null
  bookings_ready?: boolean | null
}

export interface TestBeforeBooking {
  id: number
  status: AssignmentStatus
  description: string
  equipment_node_id: number | null
  equipment_node_name: string | null
  equipment_path: PathItem[]
  defect_type: DefectType | null
  assignments: BookingAssignmentDetail[]
}

export interface StageBlocker {
  stage_type: StageType
  status: string // actual stage status, or "MISSING"
}

/** Exactly the backend's BookingBlockerOut (app/schemas/shed_visits.py). The booking blocks Shed
 * Out because an assignment is not yet ATTENDED - `status` is the booking's derived status
 * (OPEN / IN_PROGRESS / REOPENED) - or because it has no section assignment at all
 * (`status === 'NO_ASSIGNMENTS'`). The backend sends no per-section breakdown here; an earlier
 * frontend shape (`pending_sections` / `reason`) no longer matched it and crashed on real data. */
export interface BookingBlocker {
  booking_id: number
  booking_source: string
  status: string
}

/** One checksheet requirement holding Shed Out - backend ChecksheetRequirementBlockerOut. */
export interface ChecksheetRequirementBlocker {
  kind: 'WORK_PACKAGE_NOT_GENERATED' | 'BLDCMS_UNAVAILABLE' | 'REQUIREMENT_PENDING' | string
  requirement_id?: number | null
  template_id?: number | null
  template_name?: string | null
  section_name?: string | null
  equipment_name?: string | null
  maintenance_type?: string | null
  workflow_stage_type?: string | null
  checksheet_status?: string | null
}

export interface ChecksheetRequirementSummary {
  work_package_generated: boolean
  total: number
  blocking: number
  optional: number
  deactivated: number
  satisfied: number
}

export interface ShedOutEligibility {
  shed_visit_id: number
  eligible: boolean
  stage_blockers: StageBlocker[]
  booking_blockers: BookingBlocker[]
  // Additive on the backend; optional here so an older response still renders.
  checksheet_blockers?: ChecksheetRequirementBlocker[]
  checksheet_summary?: ChecksheetRequirementSummary
}

export interface ShedOutRequestPayload {
  departed_at: string
  remarks?: string | null
}

export interface ShedOutResult {
  id: number
  loco_number: string
  status: string
  departed_at: string
  departure_source: string
}

// Operations Dashboard Integration Phase 4: read-only BL-DCMS checksheet status/progress.
// `available` distinguishes "BL-DCMS reachable, zero checksheets" from "BL-DCMS unavailable" -
// an empty items/stages array alone is never enough to tell the two apart, see
// ChecksheetProgress.tsx.
export interface ChecksheetIntegrationItem {
  id: number
  template_id: number
  template_name: string | null
  section: string | null
  equipment: string | null
  loco: string | null
  schedule_family: string | null
  schedule_variant: string | null
  workflow_stage_type: string | null
  status: string
  approved: boolean
  signed: boolean
  signing_timestamp: string | null
  verification_status: string | null
}

export interface ChecksheetIntegrationListResult {
  shed_visit_id: number
  source: string
  available: boolean
  items: ChecksheetIntegrationItem[]
}

export interface ChecksheetStageSummary {
  workflow_stage_type: string
  total_checksheets: number
  approved_checksheets: number
  pending_checksheets: number
}

export interface ChecksheetSummaryResult {
  shed_visit_id: number
  source: string
  available: boolean
  stages: ChecksheetStageSummary[]
}

// Operations Dashboard Integration Phase 5B.1: materialized checksheet work packages.
// A frozen, visit-specific snapshot of BL-DCMS applicability at generation time - never
// re-read from BL-DCMS afterward. See ChecksheetWorkPackage.tsx.
export interface ChecksheetRequirement {
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
}

export interface ChecksheetRequirementStage {
  workflow_stage_type: string
  requirements: ChecksheetRequirement[]
}

export interface ChecksheetWorkPackage {
  shed_visit_id: number
  generated: boolean
  generated_by: number | null
  generated_at: string | null
  stages: ChecksheetRequirementStage[]
}

// Common Booking Pool + Equipment-Bifurcated Dashboard Reform. Bookings are global to their shed
// visit and visible to every authorized section - equipment-section mapping no longer routes
// them. See BookingPool.tsx.
export type BookingPoolStatus = 'OPEN' | 'IN_PROGRESS' | 'ATTENDED' | 'REOPENED'
export type BookingPoolSource = 'LOG_BOOK' | 'TEST_BEFORE' | 'SCHEDULE_INSPECTION' | 'TEST_AFTER' | 'MANUAL'

export interface BookingPoolShedVisitBrief {
  id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
}

/** A section this booking was ROUTED to, from booking_section_assignments.
 *
 * Historical, not derived: it records where the work was actually sent, and stays correct after
 * the equipment's section mapping is later changed. A booking may carry several - multi-section
 * routing is supported.
 */
export interface RoutedSection {
  section_id: number
  section_code: string
  section_name: string
  /** AUTO_MAPPING or MANUAL. Available for diagnostics; not normally shown. */
  assignment_source: string
  /** This section's own execution status; the booking status is an aggregate of them. */
  status: AssignmentStatus
}

export interface BookingPoolItem {
  id: number
  status: BookingPoolStatus
  description: string
  booking_source: string
  workflow_stage_type: string | null
  equipment_node_id: number | null
  equipment_node_name: string | null
  equipment_path: PathItem[]
  routed_sections: RoutedSection[]
  /** Provenance, derived server-side from bookings.created_by - NOT a booking source. Drives the
   *  "Added by PPIO" badge. Optional so a response from an older build still parses. */
  created_by_name?: string | null
  created_by_section_code?: string | null
  created_by_planning_section?: boolean
  defect_type: DefectType | null
  shed_visit: BookingPoolShedVisitBrief
  created_at: string
  started_by_name: string | null
  started_by_section_code: string | null
  started_at: string | null
  attended_by_name: string | null
  attended_by_section_code: string | null
  attended_at: string | null
  attendance_remarks: string | null
}

// Operations Dashboard Phase 5B.2: Required Checksheet vs BL-DCMS APPROVED correlation.
// Every value is either the frozen work-package snapshot (Phase 5B.1) or a live BL-DCMS read -
// never persisted. See ChecksheetRequirementProgress.tsx.
export type ChecksheetProgressState = 'NOT_STARTED' | 'IN_PROGRESS' | 'SATISFIED' | 'REJECTED'

export interface ChecksheetEvidence {
  checksheet_id: number
  status: string
  signed: boolean
  signing_timestamp: string | null
  verification_status: string | null
}

export interface ChecksheetRequirementProgressItem {
  requirement_id: number
  applicability_id: number
  template_id: number
  template_name_snapshot: string
  section: string | null
  equipment: string | null
  workflow_stage_type: string
  is_required: boolean
  progress_state: ChecksheetProgressState
  satisfied: boolean
  matching_checksheets: ChecksheetEvidence[]
}

export interface StageChecksheetProgress {
  workflow_stage_type: string
  required_total: number
  required_satisfied: number
  required_remaining: number
  optional_total: number
  optional_satisfied: number
  ready_for_completion: boolean
  requirements: ChecksheetRequirementProgressItem[]
}

export interface VisitChecksheetProgress {
  shed_visit_id: number
  work_package_generated: boolean
  bldcms_available: boolean
  stages: StageChecksheetProgress[]
}

// Operations Dashboard Phase 5B.3: Authoritative Checksheet Stage Reconciliation. This is the
// sole mutation path that may transition a shed_visit_stage to COMPLETED - see
// api/workflow.ts:reconcileChecksheetStages and ShedVisitWorkflowPage.tsx's "Reconcile Workflow"
// action, which asks the system to evaluate evidence rather than manually overriding it.
export interface StageReconciliationResult {
  workflow_stage_type: string
  previous_status: string
  current_status: string
  checksheets_ready: boolean | null
  bookings_ready: boolean | null
  reason: string
}

export interface VisitReconciliationResult {
  shed_visit_id: number
  changed: boolean
  stages: StageReconciliationResult[]
}

// Shed Visit History (archive) - mirrors backend app/schemas/visit_history.py. Every nullable
// field means "not recorded"; the UI shows it as such and never as zero.
export interface VisitClosure {
  kind: 'SHED_OUT' | 'ADMIN_RESET' | 'OTHER'
  label: string
  at: string | null
  source: string | null
  actor_name: string | null
  reason: string | null
}

export interface VisitHistoryRow {
  shed_visit_id: number
  loco_number: string
  loco_model: string | null
  technology: string | null
  schedule_family: string | null
  schedule_variant: string | null
  visit_type: string | null
  status: string
  operational_phase: string
  display_label: string
  arrival_at: string
  arrival_condition: string | null
  schedule_started_at: string | null
  ready_at: string | null
  departed_at: string | null
  departure_source: string | null
  closure: VisitClosure | null
  booking_count: number
  /** null = BL-DCMS could not be asked; never a stand-in zero. */
  checksheet_count: number | null
}

export interface VisitHistoryPage {
  items: VisitHistoryRow[]
  total: number
  page: number
  page_size: number
  locomotive_details_available: boolean
  checksheet_counts_available: boolean
}

export interface VisitTimingMilestone {
  key: string
  label: string
  at: string | null
  actor_name: string | null
  note: string | null
}

export interface VisitTimingSpan {
  key: string
  label: string
  seconds: number | null
  running: boolean
}

export interface VisitHistoryEvent {
  at: string
  event_type: string
  sentence: string
  actor_name: string
  section_name: string | null
  remarks: string | null
  /** Admin only - null for everyone else. */
  raw: Record<string, unknown> | null
}

export interface VisitReopenRecord {
  at: string
  by_name: string
  reason: string | null
}

export interface VisitAssignmentHistory {
  assignment_id: number
  section_id: number
  section_code: string | null
  section_name: string | null
  status: string
  assignment_source: string | null
  assigned_at: string | null
  assigned_by_name: string | null
  started_at: string | null
  started_by_name: string | null
  attended_at: string | null
  attended_by_name: string | null
  attendance_remarks: string | null
  reopens: VisitReopenRecord[]
}

export interface VisitBookingHistory {
  booking_id: number
  booking_source: string
  booking_source_label: string
  stage_type: string | null
  equipment: { node_id: number | null; name: string | null; path: string[] }
  defect_type: string | null
  remarks: string
  status: string
  created_at: string
  created_by_name: string
  origin_checksheet_id: number | null
  responsible_sections: string[]
  assignments: VisitAssignmentHistory[]
  history: VisitHistoryEvent[]
}

export interface VisitBookingSectionGroup {
  section_id: number | null
  section_code: string | null
  section_name: string
  booking_count: number
  bookings: VisitBookingHistory[]
}

export interface VisitChecksheetHistory {
  checksheet_id: number
  template_id: number | null
  template_name: string | null
  schedule_family: string | null
  workflow_stage_type: string | null
  workflow_group: string
  section_id: number | null
  section_name: string | null
  equipment_label: string | null
  maintenance_type: string | null
  status: string
  created_at: string | null
  created_by_name: string | null
  submitted_at: string | null
  submitted_by_name: string | null
  approved_at: string | null
  approved_by_name: string | null
  rejected_at: string | null
  rejected_by_name: string | null
  rejection_reason: string | null
  signed: boolean
  signed_at: string | null
  signed_by_name: string | null
  signature_verification: string | null
  signed_document_available: boolean
  /** An authenticated API route, fetched with the session's Authorization header. */
  signed_document_url: string | null
  requirement: { is_required: boolean; is_active: boolean } | null
}

export interface VisitHistoryDetail {
  visit: VisitHistoryRow
  timings: { milestones: VisitTimingMilestone[]; spans: VisitTimingSpan[] }
  bookings: VisitBookingSectionGroup[]
  booking_scope: 'ALL_SECTIONS' | 'OWN_SECTION'
  checksheets: { available: boolean; message: string | null; items: VisitChecksheetHistory[] }
  events: VisitHistoryEvent[]
  event_log_filter: Record<string, unknown>
}

// ---------------------------------------------------------------------------------------------
// Admin destructive deletion.
//
// Mirrors app/schemas/admin_deletion.py. Note what is ABSENT from every request type: no employee
// id, no user id, no role. The acting Admin is derived from the session token server-side, and the
// request schema forbids unknown keys, so there is no field through which a client could nominate
// someone else.
// ---------------------------------------------------------------------------------------------

export interface DeletionVisitBrief {
  id: number
  loco_number: string | null
  schedule_family: string | null
  schedule_variant: string | null
  status: string | null
  arrival_at: string | null
}

export interface PlannedFile {
  entity_type: string
  origin_id: number | null
  path: string
  exists: boolean
  size_bytes: number | null
  sha256: string | null
  /** True when a row that SURVIVES this deletion still names the same file. Never destroyed. */
  shared: boolean
}

export interface VisitDeletionPreview {
  visit: DeletionVisitBrief
  /** The exact phrase the Admin must type. Derived server-side; a human check, not an identifier. */
  required_confirmation: string
  counts: Record<string, number>
  total_rows: number
  files: PlannedFile[]
  warnings: string[]
}

export interface BookingDeletionPreview {
  booking: {
    id: number
    description: string | null
    status: string | null
    booking_source: string | null
    equipment_node_id: number | null
  }
  visit: DeletionVisitBrief
  counts: Record<string, number>
  total_rows: number
  unaffected: {
    shed_visit: boolean
    other_bookings_on_this_visit: number
    checksheets: boolean
  }
  files: PlannedFile[]
  warnings: string[]
}

export interface DeletionEventSummary {
  id: number
  deletion_type: 'BOOKING' | 'SHED_VISIT'
  target_id: number
  shed_visit_id: number
  loco_number: string
  schedule_family: string | null
  schedule_variant: string | null
  visit_status: string | null
  actor_employee_id: string
  actor_name: string
  reason: string
  requested_at: string
  completed_at: string | null
  status: 'IN_PROGRESS' | 'COMPLETED' | 'FAILED'
  failure_reason: string | null
  record_counts: Record<string, number>
  total_rows: number
  manifest_hash: string | null
  files_planned: number
  /** null until the filesystem step has run - it happens only after the database commits. */
  files_destroyed: number | null
  files_completed_at: string | null
}

export interface DeletionEventDetail extends DeletionEventSummary {
  manifest: { version?: number; entities?: { order: number; entity_type: string; row_count: number }[] }
  file_plan: PlannedFile[]
  file_result: { path: string; result: string; sha256: string | null }[] | null
  confirmation_text: string | null
  item_count: number
}

export interface DeletionItem {
  id: number
  entity_type: string
  original_id: number | null
  original_key: Record<string, unknown> | null
  snapshot: Record<string, unknown>
  content_hash: string
}

export interface DeletionItemPage {
  items: DeletionItem[]
  total: number
  offset: number
  limit: number
}
