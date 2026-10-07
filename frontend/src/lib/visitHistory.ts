/** Presentation helpers for Shed Visit History. Grouping only - nothing here decides what a
 * user may see; the server has already scoped every list it returns. */
import type { VisitChecksheetHistory, VisitHistoryRow } from '../types'

export const NOT_RECORDED = 'Not recorded'

const GROUP_ORDER = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER', 'MAJOR']

const GROUP_LABELS: Record<string, string> = {
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Minor Inspection',
  TEST_AFTER: 'Test After',
  MAJOR: 'Major schedule',
  UNCLASSIFIED: 'Other checksheets',
}

/** A group the page does not know yet (a future TI or GC workflow) is still shown, under its
 * own name - never dropped. */
export function checksheetGroupLabel(group: string): string {
  return GROUP_LABELS[group] ?? group.replace(/_/g, ' ')
}

export interface ChecksheetSectionBucket {
  sectionName: string
  items: VisitChecksheetHistory[]
}

export interface ChecksheetGroup {
  key: string
  label: string
  count: number
  sections: ChecksheetSectionBucket[]
}

function byEquipmentThenId(a: VisitChecksheetHistory, b: VisitChecksheetHistory): number {
  const ea = a.equipment_label ?? a.template_name ?? ''
  const eb = b.equipment_label ?? b.template_name ?? ''
  return ea.localeCompare(eb) || a.checksheet_id - b.checksheet_id
}

/** Stage groups in workflow order (Minor: Test Before, Minor Inspection, Test After; then Major;
 * then anything else alphabetically), each split by section and sorted by equipment. */
export function groupChecksheets(items: VisitChecksheetHistory[]): ChecksheetGroup[] {
  const groups = new Map<string, VisitChecksheetHistory[]>()
  for (const item of items) {
    const list = groups.get(item.workflow_group) ?? []
    list.push(item)
    groups.set(item.workflow_group, list)
  }
  const rank = (key: string) => {
    const i = GROUP_ORDER.indexOf(key)
    return i === -1 ? GROUP_ORDER.length : i
  }
  return Array.from(groups.entries())
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([key, list]) => {
      const sections = new Map<string, VisitChecksheetHistory[]>()
      for (const item of list) {
        const name = item.section_name ?? 'Section not recorded'
        const bucket = sections.get(name) ?? []
        bucket.push(item)
        sections.set(name, bucket)
      }
      return {
        key,
        label: checksheetGroupLabel(key),
        count: list.length,
        sections: Array.from(sections.entries())
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([sectionName, bucket]) => ({ sectionName, items: [...bucket].sort(byEquipmentThenId) })),
      }
    })
}

/** The visit's family as people say it; a family this page does not know is shown as-is. */
export function familyLabel(family: string | null | undefined): string {
  if (family === 'MINOR') return 'Minor'
  if (family === 'MAJOR') return 'Major'
  return family ?? NOT_RECORDED
}

export function isAdminReset(row: VisitHistoryRow): boolean {
  return row.closure?.kind === 'ADMIN_RESET'
}

export function visitStatusLabel(row: VisitHistoryRow): string {
  if (row.status === 'CLOSED') return row.closure?.label ?? 'Closed'
  if (row.status === 'READY') return 'Ready'
  return 'In shed'
}
