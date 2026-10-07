import { describe, expect, it } from 'vitest'
import { historyChecksheet, historyRow } from '../mocks/visitHistory'
import { checksheetGroupLabel, familyLabel, groupChecksheets, isAdminReset, visitStatusLabel } from './visitHistory'

describe('groupChecksheets', () => {
  it('orders Minor stages as the workflow runs, then Major, then anything else', () => {
    const groups = groupChecksheets([
      historyChecksheet({ checksheet_id: 1, workflow_group: 'GC' }),
      historyChecksheet({ checksheet_id: 2, workflow_group: 'TEST_AFTER' }),
      historyChecksheet({ checksheet_id: 3, workflow_group: 'MAJOR' }),
      historyChecksheet({ checksheet_id: 4, workflow_group: 'TEST_BEFORE' }),
      historyChecksheet({ checksheet_id: 5, workflow_group: 'SCHEDULE_INSPECTION' }),
      historyChecksheet({ checksheet_id: 6, workflow_group: 'TI' }),
    ])
    expect(groups.map((g) => g.label)).toEqual([
      'Test Before', 'Minor Inspection', 'Test After', 'Major schedule', 'GC', 'TI',
    ])
  })

  it('splits a group by section and sorts by equipment', () => {
    const [group] = groupChecksheets([
      historyChecksheet({ checksheet_id: 1, section_name: 'M2-HR', equipment_label: 'Compressor' }),
      historyChecksheet({ checksheet_id: 2, section_name: 'M1-HR', equipment_label: 'Pantograph' }),
      historyChecksheet({ checksheet_id: 3, section_name: 'M1-HR', equipment_label: 'Axle box' }),
      historyChecksheet({ checksheet_id: 4, section_name: null, equipment_label: 'Unknown' }),
    ])
    expect(group.count).toBe(4)
    expect(group.sections.map((s) => [s.sectionName, s.items.map((i) => i.checksheet_id)])).toEqual([
      ['M1-HR', [3, 2]],
      ['M2-HR', [1]],
      ['Section not recorded', [4]],
    ])
  })

  it('drops nothing', () => {
    expect(groupChecksheets([])).toEqual([])
    const items = Array.from({ length: 7 }, (_, i) =>
      historyChecksheet({ checksheet_id: i, workflow_group: i % 2 ? 'UNCLASSIFIED' : 'MAJOR' }))
    const total = groupChecksheets(items).reduce((n, g) => n + g.sections.reduce((m, s) => m + s.items.length, 0), 0)
    expect(total).toBe(7)
    expect(checksheetGroupLabel('UNCLASSIFIED')).toBe('Other checksheets')
    expect(checksheetGroupLabel('GENERAL_CHECKING')).toBe('GENERAL CHECKING')
  })
})

describe('visit labels', () => {
  it('names families, statuses and resets plainly', () => {
    expect(familyLabel('MINOR')).toBe('Minor')
    expect(familyLabel('MAJOR')).toBe('Major')
    expect(familyLabel('TI')).toBe('TI')
    expect(familyLabel(null)).toBe('Not recorded')
    expect(visitStatusLabel(historyRow())).toBe('In shed')
    expect(visitStatusLabel(historyRow({ status: 'READY' }))).toBe('Ready')
    const reset = historyRow({
      status: 'CLOSED',
      closure: { kind: 'ADMIN_RESET', label: 'Closed administratively (system reset)', at: null, source: 'SYSTEM', actor_name: 'System', reason: null },
    })
    expect(visitStatusLabel(reset)).toBe('Closed administratively (system reset)')
    expect(isAdminReset(reset)).toBe(true)
    expect(isAdminReset(historyRow())).toBe(false)
  })
})
