import { describe, expect, it } from 'vitest'

import { formatDateTime, formatDateTimeShort, formatDateTimeWithSeconds, parseTimestamp } from './format'

describe('IST timestamp formatting', () => {
  it('renders a UTC instant in Asia/Kolkata', () => {
    expect(formatDateTimeWithSeconds('2026-09-22T10:35:57.510919+00:00')).toBe('22 Sep 2026, 04:05:57 PM IST')
    expect(formatDateTime('2026-09-22T10:35:57+00:00')).toBe('22 Sep 2026, 04:05 PM IST')
    expect(formatDateTime('2026-09-22T10:35:57Z')).toBe('22 Sep 2026, 04:05 PM IST')
  })

  it('gets midnight, noon and afternoon right', () => {
    expect(formatDateTime('2026-09-23T00:05:00+05:30')).toBe('23 Sep 2026, 12:05 AM IST')
    expect(formatDateTime('2026-09-23T12:05:00+05:30')).toBe('23 Sep 2026, 12:05 PM IST')
    expect(formatDateTime('2026-09-23T13:05:00+05:30')).toBe('23 Sep 2026, 01:05 PM IST')
  })

  it('honours an explicit offset and never the browser timezone', () => {
    // 18:57 in Dubai (+04:00) is 20:27 IST.
    expect(formatDateTime('2026-09-22T18:57:00+04:00')).toBe('22 Sep 2026, 08:27 PM IST')
    expect(formatDateTime('2026-09-22T19:27:56+05:30')).toBe('22 Sep 2026, 07:27 PM IST')
  })

  it('reads an offset-less timestamp as UTC, the backends\' canonical storage', () => {
    expect(formatDateTime('2026-09-22T13:57:56')).toBe('22 Sep 2026, 07:27 PM IST')
    expect(formatDateTime('2026-09-22 13:57:56')).toBe('22 Sep 2026, 07:27 PM IST')
  })

  it('keeps the short form dense but still explicit', () => {
    expect(formatDateTimeShort('2026-09-22T10:35:57+00:00')).toBe('22 Sep, 04:05 PM IST')
  })

  it('returns the original text for unparseable input and a dash for nothing', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDateTime('')).toBe('—')
    expect(formatDateTime('not-a-timestamp')).toBe('not-a-timestamp')
    expect(parseTimestamp('not-a-timestamp')).toBeNull()
  })

  it('parses both spellings to the same instant', () => {
    expect(parseTimestamp('2026-09-22T10:35:57')!.getTime()).toBe(
      parseTimestamp('2026-09-22T10:35:57+00:00')!.getTime(),
    )
  })
})
