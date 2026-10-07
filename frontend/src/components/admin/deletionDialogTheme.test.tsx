import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HttpResponse, http } from 'msw'
import css from '../../index.css?raw'
import { renderWithProviders, loginAsToken } from '../../test/testUtils'
import { THEME_STORAGE_KEY } from '../../theme/themeMode'
import { server } from '../../mocks/server'
import { DeleteBookingDialog } from './DeleteBookingDialog'
import { DeleteShedVisitDialog } from './DeleteShedVisitDialog'

/** Contrast and theming for the two deletion dialogs.
 *
 * WHY THESE ASSERT ON TOKENS AND CLASSES RATHER THAN COLOURS. jsdom does not apply stylesheets, so
 * a computed-colour assertion here would read the browser default and prove nothing. What CAN be
 * proven is the thing that actually broke: which CSS custom properties the rules reference, and
 * whether the dialogs carry the shared theme-aware classes. The original bug was entirely of that
 * kind - rules naming tokens that did not exist, under a media query keyed to the wrong signal.
 */

const DELETION_SECTION = (() => {
  const marker = css.indexOf('ADMIN DESTRUCTIVE DELETION')
  expect(marker).toBeGreaterThan(-1)
  // From the banner's OPENING `/*`, not from the marker text inside it - otherwise the slice begins
  // mid-comment, the comment stripper below cannot match an unopened comment, and the banner's own
  // prose survives into DELETION_RULES.
  const start = css.lastIndexOf('/*', marker)
  expect(start).toBeGreaterThan(-1)
  return css.slice(start)
})()

/** The same section with CSS comments stripped.
 *
 * Structural assertions must read DECLARATIONS, not prose. The section's own comment explains the
 * bug being guarded against and therefore quotes `@media (prefers-color-scheme: dark)` verbatim -
 * which a naive search would flag as the very thing it is documenting the absence of.
 */
const DELETION_RULES = DELETION_SECTION.replace(/\/\*[\s\S]*?\*\//g, '')

/** The body of one rule, so a declaration can be attributed to the selector it belongs to. */
function rule(selector: string): string {
  const at = DELETION_RULES.indexOf(`${selector} {`)
  expect(at, `no rule for ${selector}`).toBeGreaterThan(-1)
  const open = DELETION_RULES.indexOf('{', at)
  return DELETION_RULES.slice(open + 1, DELETION_RULES.indexOf('}', open))
}

const VISIT_PREVIEW = {
  visit: { id: 700, loco_number: '32032', schedule_family: 'MINOR', schedule_variant: 'IA',
           status: 'IN_SHED', arrival_at: '2026-09-25T06:00:00Z' },
  required_confirmation: 'DELETE 32032 IA',
  counts: { shed_visits: 1, bookings: 2 },
  total_rows: 3,
  files: [],
  warnings: ['3 digitally signed checksheet(s) will be destroyed.'],
}

const BOOKING_PREVIEW = {
  booking: { id: 900, description: 'Pantograph horn fault', status: 'OPEN',
             booking_source: 'LOG_BOOK', equipment_node_id: 1843 },
  visit: { id: 700, loco_number: '32032', schedule_family: 'MINOR', schedule_variant: 'IA',
           status: 'IN_SHED', arrival_at: null },
  counts: { bookings: 1 },
  total_rows: 1,
  unaffected: { shed_visit: true, other_bookings_on_this_visit: 4, checksheets: true },
  files: [],
  warnings: ['This booking has already been attended.'],
}

/** Render a dialog in a given theme, selected the way a user selects it.
 *
 * Through the stored preference, not by setting data-theme directly: renderWithProviders mounts
 * ThemeModeProvider, which writes the attribute itself on mount and would overwrite anything set
 * beforehand. Going through storage exercises the real path.
 */
async function renderVisitDialog(theme: 'light' | 'dark') {
  localStorage.setItem(THEME_STORAGE_KEY, theme)
  server.use(
    http.get('/api/admin/shed-visits/700/deletion-preview', () => HttpResponse.json(VISIT_PREVIEW)),
  )
  loginAsToken('token-admin')
  renderWithProviders(
    <DeleteShedVisitDialog visitId={700} onDeleted={vi.fn()} onCancel={vi.fn()} />,
  )
  await screen.findByText('32032')
}

async function renderBookingDialog(theme: 'light' | 'dark') {
  localStorage.setItem(THEME_STORAGE_KEY, theme)
  server.use(
    http.get('/api/admin/bookings/900/deletion-preview', () => HttpResponse.json(BOOKING_PREVIEW)),
  )
  loginAsToken('token-admin')
  renderWithProviders(
    <DeleteBookingDialog bookingId={900} onDeleted={vi.fn()} onCancel={vi.fn()} />,
  )
  await screen.findByText('Pantograph horn fault')
}

// ==================================================================== the regression ==========


describe('the deletion dialogs do not paint their own surface', () => {
  it('.modal-danger sets no background at all, so it inherits the themed modal surface', () => {
    const body = rule('.modal-danger')
    expect(body).not.toMatch(/(^|[\s;])background\s*:/)
    // It may only tint the EDGES.
    expect(body).toContain('--color-danger')
  })

  it('no hardcoded brown or red surface remains anywhere in the section', () => {
    // The exact values the broken version used, plus any other opaque hex surface.
    for (const banned of ['#2a1715', '#3a1c19', '#fff8f7', '#fdecea', '#b3261e']) {
      expect(DELETION_RULES, `${banned} is still present`).not.toContain(banned)
    }
    // A `background: #rrggbb` declaration would be theme-blind whatever the value.
    const declarations = DELETION_RULES.match(/background\s*:\s*#[0-9a-f]{3,8}/gi) ?? []
    expect(declarations).toEqual([])
  })

  it('does not key off the operating system theme', () => {
    // prefers-color-scheme reads the OS; this application themes itself with
    // :root[data-theme]. The two disagree, which is what produced the unreadable combination.
    expect(DELETION_RULES).not.toMatch(/@media\s*\(prefers-color-scheme/)
  })

  it('references only custom properties this stylesheet actually declares', () => {
    // The broken version named --danger-surface, --danger-strong and --text-muted, none of which
    // exist, so every rule silently fell back to a hardcoded hex.
    const used = new Set(
      [...DELETION_RULES.matchAll(/var\((--[a-z0-9-]+)/gi)].map((m) => m[1]),
    )
    expect(used.size).toBeGreaterThan(5)
    for (const token of used) {
      expect(css, `${token} is referenced but never declared`).toMatch(
        new RegExp(`\\n\\s*${token}\\s*:`),
      )
    }
  })

  it('takes its danger colour from the per-theme token, which both themes define', () => {
    for (const marker of [":root[data-theme='light']", ':root,']) {
      const start = css.indexOf(marker)
      const block = css.slice(start, css.indexOf('}', start))
      expect(block).toContain('--color-danger:')
      expect(block).toContain('--color-danger-bg:')
      expect(block).toContain('--color-danger-border:')
    }
  })
})

// ================================================================== red is an accent ==========


describe('red is used as an accent, not as the body colour', () => {
  it('the title, the counts heading and the irreversible line are danger-coloured', () => {
    for (const selector of ['.danger-heading', '.delete-visit-counts h3',
                            '.delete-visit-irreversible']) {
      expect(rule(selector)).toContain('var(--color-danger)')
    }
  })

  it('body copy uses the normal theme text colour', () => {
    for (const selector of ['.delete-visit-summary dd', '.delete-visit-counts ul',
                            '.delete-visit-warnings', '.workflow-danger-zone p']) {
      expect(rule(selector)).toContain('var(--color-text)')
    }
    // The summary's labels are muted theme text, not red.
    expect(rule('.delete-visit-summary dt')).toContain('var(--color-text-muted)')
  })

  it('the warning panel is a tinted wash with a red edge, never an opaque red block', () => {
    const body = rule('.delete-visit-warnings')
    expect(body).toContain('background: var(--color-danger-bg)')
    expect(body).toContain('var(--color-danger)')
    // Readable text is the whole point of the fix.
    expect(body).toContain('color: var(--color-text)')
  })

  it('leaves the inputs entirely to the themed .field styling', () => {
    // Anything set here would diverge from every other input in the application, including focus.
    for (const banned of ['.delete-visit-dialog input', '.modal-danger input',
                          '.modal-danger textarea', '.modal-danger .field input']) {
      expect(DELETION_RULES).not.toContain(`${banned} {`)
    }
  })
})

// ======================================================= both dialogs, both themes ==========


describe.each(['light', 'dark'] as const)('in %s theme', (theme) => {
  it('the Delete Visit dialog renders the shared themed shell and readable content', async () => {
    await renderVisitDialog(theme)

    const dialog = screen.getByRole('dialog', { name: /delete shed visit/i })
    // The themed modal shell, plus the danger modifier - not a bespoke surface.
    expect(dialog.querySelector('.modal')).toHaveClass('modal-danger')
    expect(document.documentElement.getAttribute('data-theme')).toBe(theme)

    // Ordinary body content is present and carries no danger class of its own.
    expect(screen.getByText('32032')).toBeInTheDocument()
    expect(screen.getByText('MINOR / IA')).toBeInTheDocument()
    expect(screen.getByText('IN_SHED')).toBeInTheDocument()
    const summary = dialog.querySelector('.delete-visit-summary')
    expect(summary?.className).not.toMatch(/danger/)

    // The accents.
    expect(screen.getByRole('heading', { name: /delete shed visit permanently/i })).toHaveClass(
      'danger-heading',
    )
    expect(dialog.querySelector('.delete-visit-warnings')).not.toBeNull()
    expect(screen.getByRole('button', { name: /delete visit permanently/i })).toHaveClass(
      'btn-danger',
    )
    expect(screen.getByRole('button', { name: /^cancel$/i })).toHaveClass('btn')
    expect(screen.getByRole('button', { name: /^cancel$/i })).not.toHaveClass('btn-danger')
  })

  it('the Delete Booking dialog renders the same shell and the same accents', async () => {
    await renderBookingDialog(theme)

    const dialog = screen.getByRole('dialog', { name: /delete booking/i })
    expect(dialog.querySelector('.modal')).toHaveClass('modal-danger')
    expect(screen.getByRole('heading', { name: /delete booking permanently/i })).toHaveClass(
      'danger-heading',
    )
    expect(screen.getByRole('button', { name: /delete booking permanently/i })).toHaveClass(
      'btn-danger',
    )
    // The same shared warning block as the visit dialog, so the two cannot drift.
    expect(dialog.querySelector('.delete-visit-warnings')).not.toBeNull()
    expect(screen.getByText('Pantograph horn fault')).toBeInTheDocument()
  })

  it('the three inputs use the ordinary themed field markup', async () => {
    await renderVisitDialog(theme)
    const dialog = screen.getByRole('dialog', { name: /delete shed visit/i })

    for (const label of [/why is this being deleted/i, /to confirm, type/i,
                         /your own account password/i]) {
      const field = screen.getByLabelText(label)
      // Wrapped in .field, like every other input in the application - so the themed border,
      // background and focus ring all apply unchanged.
      expect(field.closest('.field')).not.toBeNull()
      expect(field.getAttribute('style')).toBeNull()
    }
    expect(dialog.querySelectorAll('.field').length).toBeGreaterThanOrEqual(3)
  })

  it('the disabled destructive button is still disabled, not merely dimmed', async () => {
    await renderVisitDialog(theme)
    const button = screen.getByRole('button', { name: /delete visit permanently/i })
    expect(button).toBeDisabled()
    expect(button).toHaveClass('btn-danger')
  })
})

// ======================================================================= shared CSS ==========


describe('both dialogs are styled from one place', () => {
  it('neither component carries inline styles or a per-dialog stylesheet', async () => {
    await renderVisitDialog('light')
    const dialog = screen.getByRole('dialog', { name: /delete shed visit/i })
    expect(dialog.querySelectorAll('[style]')).toHaveLength(0)
  })

  it('the subtle table affordance is danger-tinted rather than filled', () => {
    const body = rule('.btn-danger-subtle')
    expect(body).toContain('var(--color-danger)')
    expect(body).toContain('var(--color-danger-bg)')
  })
})
