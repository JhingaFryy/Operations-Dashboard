import type { ShedOutEligibility, StageType } from '../types'
import { stageLabel } from './format'

/** Turns the backend's structured Shed Out eligibility response into the
 * lines the readiness panel renders.
 *
 * This is presentation shaping ONLY. It never decides eligibility — that is
 * `eligibility.eligible`, computed by app/services/shed_out_service.py and
 * re-checked on the Shed Out request itself. Nothing here reads a raw
 * exception string; every line is built from the typed
 * stage_blockers / booking_blockers arrays.
 */

/** The stages the readiness panel always accounts for, in workflow order.
 * A stage absent from `stage_blockers` is, by the backend's own contract,
 * satisfied. */
export const READINESS_STAGES: StageType[] = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER']

export interface ReadinessStageLine {
  stageType: StageType
  /** Backend vocabulary, spaced — e.g. "TEST AFTER". Operators quote these. */
  rawLabel: string
  /** Human label — e.g. "Test After". */
  label: string
  blocked: boolean
  /** Actual stage status when blocked, "Completed" when satisfied. */
  statusText: string
}

export interface ReadinessBookingLine {
  bookingId: number
  sourceLabel: string
  /** The booking's own status in words ("Open", "In Progress", "Reopened"), or an empty string
   * when the blocker is a missing assignment rather than pending work. */
  pendingText: string
  noSectionAssignment: boolean
}

export interface ShedOutReadiness {
  eligible: boolean
  stageLines: ReadinessStageLine[]
  bookingLines: ReadinessBookingLine[]
  /** Short, plain-language reasons — the "why is this blocked" summary. */
  reasons: string[]
}

const NO_ASSIGNMENTS = 'NO_ASSIGNMENTS'

const STATUS_SENTENCE: Record<string, string> = {
  OPEN: 'still Open',
  IN_PROGRESS: 'still In Progress',
  REOPENED: 'Reopened and awaiting work',
}

const STATUS_WORD: Record<string, string> = {
  OPEN: 'Open',
  IN_PROGRESS: 'In Progress',
  REOPENED: 'Reopened',
}

/** Never throws on an unexpected shape: a contract drift here once crashed the whole page. */
function bookingStatusWord(status: string | undefined | null): string {
  if (!status) return 'Not yet attended'
  return STATUS_WORD[status] ?? status.replace(/_/g, ' ')
}

function pluralBookings(n: number): string {
  return n === 1 ? '1 booking' : `${n} bookings`
}

export function buildShedOutReadiness(eligibility: ShedOutEligibility): ShedOutReadiness {
  const stageLines: ReadinessStageLine[] = READINESS_STAGES.map((stageType) => {
    const blocker = eligibility.stage_blockers.find((b) => b.stage_type === stageType)
    return {
      stageType,
      rawLabel: stageType.replace(/_/g, ' '),
      label: stageLabel(stageType),
      blocked: Boolean(blocker),
      statusText: blocker ? blocker.status : 'Completed',
    }
  })

  const bookingLines: ReadinessBookingLine[] = eligibility.booking_blockers.map((b) => ({
    bookingId: b.booking_id,
    sourceLabel: b.booking_source,
    pendingText: b.status === NO_ASSIGNMENTS ? '' : bookingStatusWord(b.status),
    noSectionAssignment: b.status === NO_ASSIGNMENTS,
  }))

  const reasons: string[] = []

  for (const line of stageLines) {
    if (line.blocked) {
      reasons.push(`${line.label} is not complete yet.`)
    }
  }

  const unassigned = eligibility.booking_blockers.filter((b) => b.status === NO_ASSIGNMENTS).length
  if (unassigned > 0) {
    reasons.push(
      unassigned === 1
        ? '1 booking has no responsible section assigned yet.'
        : `${unassigned} bookings have no responsible section assigned yet.`,
    )
  }

  const pendingByStatus = new Map<string, number>()
  for (const b of eligibility.booking_blockers) {
    if (b.status === NO_ASSIGNMENTS) continue
    const key = b.status || 'OPEN'
    pendingByStatus.set(key, (pendingByStatus.get(key) ?? 0) + 1)
  }
  for (const status of ['OPEN', 'IN_PROGRESS', 'REOPENED']) {
    const n = pendingByStatus.get(status) ?? 0
    if (n > 0) {
      reasons.push(`${pluralBookings(n)} ${STATUS_SENTENCE[status]}.`)
    }
  }
  for (const [status, n] of pendingByStatus) {
    if (!(status in STATUS_SENTENCE)) reasons.push(`${pluralBookings(n)} ${status.replace(/_/g, ' ')}.`)
  }

  // The checksheet gate holds Shed Out too, for MINOR and MAJOR alike.
  const outstandingChecksheets = (eligibility.checksheet_blockers ?? []).filter(
    (b) => b.kind === 'REQUIREMENT_PENDING',
  ).length
  if (outstandingChecksheets > 0) {
    reasons.push(
      outstandingChecksheets === 1
        ? '1 required checksheet is still outstanding.'
        : `${outstandingChecksheets} required checksheets are still outstanding.`,
    )
  }
  if ((eligibility.checksheet_blockers ?? []).some((b) => b.kind === 'WORK_PACKAGE_NOT_GENERATED')) {
    reasons.push('No checksheet work package has been generated, so the required checksheets are unknown.')
  }
  if ((eligibility.checksheet_blockers ?? []).some((b) => b.kind === 'BLDCMS_UNAVAILABLE')) {
    reasons.push('Checksheets could not be read from BL-DCMS, so they cannot be confirmed yet.')
  }

  return { eligible: eligibility.eligible, stageLines, bookingLines, reasons }
}
