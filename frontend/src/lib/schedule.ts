/** Single source of truth (frontend side) for the shed-visit schedule
 * domain: which (schedule_family, schedule_variant) pairs are valid, and
 * how the Shed In "Schedule" dropdown maps a single user choice onto that
 * pair. Mirrors the backend's app/domain/schedule.py SCHEDULE_MATRIX —
 * the backend is still the authoritative validator (this just keeps the
 * dropdown and any frontend-side checks from drifting out of sync with
 * it, and keeps every family/variant comparison in one place instead of
 * scattered across components). */

import type { MajorScheduleVariant, MinorScheduleVariant, ScheduleFamily, ScheduleVariant } from '../types'

export const SCHEDULE_MATRIX: Record<ScheduleFamily, readonly ScheduleVariant[]> = {
  MINOR: ['IA', 'IA0', 'IB', 'IC', 'IC0'],
  MAJOR: ['IOH', 'TOH'],
}

const VARIANT_TO_FAMILY: Record<ScheduleVariant, ScheduleFamily> = {
  IA: 'MINOR',
  IA0: 'MINOR',
  IB: 'MINOR',
  IC: 'MINOR',
  IC0: 'MINOR',
  IOH: 'MAJOR',
  TOH: 'MAJOR',
}

/** Every user-selectable schedule, in display order, for the Shed In
 * "Schedule" dropdown — one flat list, no separate family picker. */
export const SCHEDULE_OPTIONS: readonly ScheduleVariant[] = ['IA', 'IA0', 'IB', 'IC', 'IC0', 'IOH', 'TOH']

/** Resolves the (family, variant) pair to send in a Shed In request from
 * the single dropdown value the user picked. */
export function familyForVariant(variant: ScheduleVariant): ScheduleFamily {
  return VARIANT_TO_FAMILY[variant]
}

export function isMinorVariant(variant: string | null | undefined): variant is MinorScheduleVariant {
  return variant != null && SCHEDULE_MATRIX.MINOR.includes(variant as ScheduleVariant)
}

export function isMajorVariant(variant: string | null | undefined): variant is MajorScheduleVariant {
  return variant != null && SCHEDULE_MATRIX.MAJOR.includes(variant as ScheduleVariant)
}
