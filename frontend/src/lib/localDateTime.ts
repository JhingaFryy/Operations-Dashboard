/**
 * Shared local date/time handling for every shed lifecycle action
 * (Shed In, Start Schedule, Complete Schedule, Shed Out).
 *
 * All four follow the same rule: the field is PRE-FILLED with the current local time so the
 * common case is one click, but stays editable because shed staff routinely record an action
 * after the fact. `<input type="datetime-local">` speaks local wall-clock time with no zone, so
 * these two helpers are the single place that converts between it and the ISO instants the API
 * exchanges - doing that conversion ad hoc per form is how timezone bugs get in.
 *
 * Deployment timezone is Asia/Kolkata; nothing here hardcodes it - the browser's own zone is used
 * and `new Date(...).toISOString()` carries the correct absolute instant to the backend.
 */

/** Current local time formatted for a `datetime-local` input: "YYYY-MM-DDTHH:mm". */
export function nowLocalDateTimeValue(now: Date = new Date()): string {
  return toLocalDateTimeValue(now)
}

export function toLocalDateTimeValue(value: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}` +
    `T${pad(value.getHours())}:${pad(value.getMinutes())}`
  )
}

/**
 * Converts a `datetime-local` value to an ISO instant for the API.
 * Returns null for an empty or unparseable value, so callers validate rather than sending
 * "Invalid Date".
 */
export function localDateTimeToIso(value: string): string | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : null
}

/** "2h 14m" / "38m" / "3d 4h". Used for every elapsed/duration figure on the shed cards. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '—'
  const totalMinutes = Math.floor(seconds / 60)
  if (totalMinutes < 1) return '<1m'
  const days = Math.floor(totalMinutes / 1440)
  const hours = Math.floor((totalMinutes % 1440) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}
