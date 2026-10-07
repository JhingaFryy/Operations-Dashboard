/** Shared display formatting helpers. Presentation only — nothing here
 * derives, filters, or gates anything operational. */

/** The shed runs on Asia/Kolkata (IST, UTC+05:30) and every displayed time says so
 * explicitly. The browser's own timezone is never used: a laptop or tablet left on
 * another timezone must still show shed time. This app's timestamps are
 * `timestamp with time zone` columns serialized with an offset, so they are
 * authoritative and almost always what arrives here. An offset-less string (BL-DCMS's
 * naive columns) is read as UTC, which is that backend's canonical storage - it pins
 * its database connection to UTC for exactly this reason. Either way the offset is
 * explicit in the data or in this policy, never taken from the browser. */
export const IST_TIME_ZONE = 'Asia/Kolkata'

const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function parseTimestamp(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const trimmed = String(iso).trim()
  if (!trimmed) return null
  const normalized = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')
  const anchored = HAS_OFFSET.test(normalized) ? normalized : `${normalized}Z`
  const d = new Date(anchored)
  return Number.isNaN(d.getTime()) ? null : d
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Builds the string from explicit Intl parts rather than a locale pattern: ICU renders the short
 * month differently per build ("Sep" vs "Sept") and some builds use a narrow no-break space before
 * AM/PM. Assembling the parts here keeps one exact output in every browser. */
function istString(d: Date, opts: { withYear?: boolean; withSeconds?: boolean } = {}): string {
  const { withYear = true, withSeconds = false } = opts
  const fields = new Intl.DateTimeFormat('en-US', {
    day: '2-digit',
    month: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' as const } : {}),
    hour12: true,
    timeZone: IST_TIME_ZONE,
  }).formatToParts(d)
  const get = (type: Intl.DateTimeFormatPartTypes): string =>
    fields.find((part) => part.type === type)?.value ?? ''
  const month = MONTHS[Number(get('month')) - 1] ?? get('month')
  const time = withSeconds
    ? `${get('hour')}:${get('minute')}:${get('second')}`
    : `${get('hour')}:${get('minute')}`
  const date = withYear ? `${get('day')} ${month} ${get('year')}` : `${get('day')} ${month}`
  return `${date}, ${time} ${get('dayPeriod').toUpperCase()} IST`
}

/** "23 Sep 2026, 12:57 PM IST" */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parseTimestamp(iso)
  if (!d) return iso ? String(iso) : '—'
  return istString(d)
}

/** "23 Sep 2026, 12:57:56 PM IST" - where the second matters (logs, event trails). */
export function formatDateTimeWithSeconds(iso: string | null | undefined): string {
  const d = parseTimestamp(iso)
  if (!d) return iso ? String(iso) : '—'
  return istString(d, { withSeconds: true })
}

/** Short, dense form for card/table metadata where the full string is too wide:
 * "23 Sep, 12:57 PM IST". Still explicitly IST, never the browser's timezone. */
export function formatDateTimeShort(iso: string | null | undefined): string {
  const d = parseTimestamp(iso)
  if (!d) return iso ? String(iso) : '—'
  return istString(d, { withYear: false })
}

/** The one place that renders a schedule for a human: just the variant
 * (e.g. "IA", "TOH") — never the internal family prefix ("MINOR"/"MAJOR").
 * schedule_family/schedule_variant stay separate fields everywhere else
 * (storage, API payloads, internal reads) — this function is only where
 * they collapse into a single display string. Every surface that shows a
 * schedule to a user must go through this — not format the pair inline. */
export function formatSchedule(
  family: string | null | undefined,
  variant: string | null | undefined,
): string {
  if (!family && !variant) return '—'
  return variant ?? '—'
}

/** Every value bookings.booking_source is allowed to hold, per its CHECK constraint.
 *
 * All EIGHT are listed deliberately. SPECIAL_CHECKING, TRIP_INSPECTION and GENERAL_CHECKING were
 * missing, so those bookings rendered their raw enum text to the user. There is no TB and no
 * SYSTEM - those are not values this system produces, and inventing entries for them would only
 * make a typo elsewhere look like a supported source.
 */
export const BOOKING_SOURCE_LABELS: Record<string, string> = {
  LOG_BOOK: 'Log Book',
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Schedule Inspection',
  TEST_AFTER: 'Test After',
  SPECIAL_CHECKING: 'Special Checking',
  MANUAL: 'Manual',
  TRIP_INSPECTION: 'Trip Inspection',
  GENERAL_CHECKING: 'General Checking',
}

export function bookingSourceLabel(source: string | null | undefined): string {
  if (!source) return '—'
  return BOOKING_SOURCE_LABELS[source] ?? source
}

export const STAGE_LABELS: Record<string, string> = {
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Schedule Inspection',
  TEST_AFTER: 'Test After',
  SPECIAL_CHECKING: 'Special Checking',
}

export function stageLabel(stageType: string | null | undefined): string {
  if (!stageType) return '—'
  return STAGE_LABELS[stageType] ?? stageType
}

export function equipmentPathLabel(path: { name: string }[]): string {
  return path.map((p) => p.name).join(' → ')
}
