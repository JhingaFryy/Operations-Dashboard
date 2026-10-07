import { describe, expect, it } from 'vitest'
import { allowedAssignmentActions, canPerform } from './transitions'

/** These assertions encode the backend's transition table
 * (app/services/section_dashboard_service.py). The UI must never offer a
 * transition the backend forbids. */
describe('allowedAssignmentActions', () => {
  it('OPEN offers only Start', () => {
    expect(allowedAssignmentActions('OPEN', { canReopen: true })).toEqual(['START'])
  })

  it('REOPENED offers only Start — never a direct Attend', () => {
    expect(allowedAssignmentActions('REOPENED', { canReopen: true })).toEqual(['START'])
    expect(canPerform('ATTEND', 'REOPENED', { canReopen: true })).toBe(false)
  })

  it('OPEN can never be attended directly', () => {
    expect(canPerform('ATTEND', 'OPEN', { canReopen: true })).toBe(false)
  })

  it('IN_PROGRESS offers only Attend', () => {
    expect(allowedAssignmentActions('IN_PROGRESS', { canReopen: true })).toEqual(['ATTEND'])
  })

  it('ATTENDED offers Reopen to an Admin only', () => {
    expect(allowedAssignmentActions('ATTENDED', { canReopen: true })).toEqual(['REOPEN'])
    expect(allowedAssignmentActions('ATTENDED', { canReopen: false })).toEqual([])
  })
})
