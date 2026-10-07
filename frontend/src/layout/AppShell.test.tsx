import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { AppShell } from './AppShell'
import css from '../index.css?raw'

describe('AppShell navigation', () => {
  it('shows Equipment Mapping for Admin', async () => {
    loginAsToken('token-admin')
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /equipment responsibility mapping/i })).toBeInTheDocument()
  })

  it('shows Equipment Mapping for a permitted Supervisor', async () => {
    loginAsToken('token-sup-permitted')
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Sam Supervisor')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /equipment responsibility mapping/i })).toBeInTheDocument()
  })

  it('hides Equipment Mapping for a Supervisor without the permission', async () => {
    loginAsToken('token-sup-denied')
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Sue Supervisor')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /equipment responsibility mapping/i })).not.toBeInTheDocument()
  })

  it('Menu toggle controls the sidebar drawer', async () => {
    loginAsToken('token-admin')
    const { container } = renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Menu' })
    expect(toggle).toHaveAttribute('aria-controls', 'app-sidebar')
    expect(container.querySelector('#app-sidebar')).not.toBeNull()
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(container.querySelector('.app-shell')).toHaveClass('app-shell-nav-open')

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell-nav-open')
  })


  it('puts the theme toggle in the top-right header, after the page context', async () => {
    loginAsToken('token-admin')
    const { container } = renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: /switch to (dark|light) theme/i })
    expect(container.querySelector('.app-header')?.contains(toggle)).toBe(true)
    // The spacer before it is what pushes it (and the user summary) to the right.
    const spacer = container.querySelector('.app-header-spacer')
    expect(spacer).not.toBeNull()

    const relation = spacer!.compareDocumentPosition(toggle)
    expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps every navigation destination and the page body after toggling the theme', async () => {
    loginAsToken('token-admin')
    renderWithProviders(
      <AppShell>
        <h1>Page body</h1>
      </AppShell>,
    )

    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    const before = screen
      .getAllByRole('link')
      .map((a) => `${a.textContent}\u0000${a.getAttribute('href')}`)

    fireEvent.click(screen.getByRole('button', { name: /switch to dark theme/i }))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(
      screen.getAllByRole('link').map((a) => `${a.textContent}\u0000${a.getAttribute('href')}`),
    ).toEqual(before)
    expect(screen.getByRole('heading', { name: 'Page body' })).toBeInTheDocument()
    expect(screen.getByText('Alice Admin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
  })

  // jsdom does not apply media queries, so guard the stylesheet directly: the Menu toggle is
  // hidden by default (desktop, sidebar always visible) with a selector that outranks `.btn`,
  // and only shown inside the max-width: 1000px block where the sidebar becomes a drawer.
  it('hides the Menu toggle on desktop and shows it only at the drawer breakpoint', () => {
    const drawerBlockStart = css.indexOf('@media (max-width: 1000px)')
    expect(drawerBlockStart).toBeGreaterThan(-1)

    const hide = css.search(/\.app-header \.nav-toggle \{\s*display: none;/)
    expect(hide).toBeGreaterThan(-1)
    expect(hide).toBeLessThan(drawerBlockStart)

    const show = css.slice(drawerBlockStart).search(/\.app-header \.nav-toggle \{\s*display: inline-flex;/)
    expect(show).toBeGreaterThan(-1)
    // A bare `.nav-toggle {` rule would tie with `.btn` and lose the cascade again.
    expect(css).not.toMatch(/^\s*\.nav-toggle \{/m)
  })
})

/**
 * The drawer's exit paths.
 *
 * THE BUG: below 1000px the sidebar is `position: fixed; inset: 0 auto 0 0; z-index: 20`, so an
 * open drawer covers the top-left of the viewport - which is exactly where the header's Menu
 * button sits at `z-index: 5`. The only control that could close the drawer was underneath it.
 * jsdom applies no media queries and computes no stacking, so the layering itself is asserted
 * against the stylesheet; the three exits are asserted against behaviour.
 */
describe('AppShell mobile drawer', () => {
  async function openDrawer() {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    const view = renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )
    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Menu' }))
    return { user, ...view }
  }

  it('28. the menu button opens the drawer', async () => {
    const { container } = await openDrawer()
    expect(container.querySelector('.app-shell')).toHaveClass('app-shell-nav-open')
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('29. the drawer carries its own close button', async () => {
    const { user, container } = await openDrawer()

    await user.click(screen.getByRole('button', { name: /close navigation/i }))

    expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell-nav-open')
  })

  it('30. tapping the scrim outside the drawer closes it', async () => {
    const { user, container } = await openDrawer()
    const scrim = container.querySelector('.sidebar-scrim') as HTMLElement
    expect(scrim).not.toBeNull()

    await user.click(scrim)

    expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell-nav-open')
    expect(container.querySelector('.sidebar-scrim')).toBeNull()
  })

  it('30b. Escape closes the drawer', async () => {
    const { user, container } = await openDrawer()

    await user.keyboard('{Escape}')

    expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell-nav-open')
  })

  it('30c. focus moves into the drawer on open and back to Menu on close', async () => {
    const { user } = await openDrawer()
    const close = screen.getByRole('button', { name: /close navigation/i })
    expect(close).toHaveFocus()

    await user.click(close)

    expect(screen.getByRole('button', { name: 'Menu' })).toHaveFocus()
  })

  it('30d. the scrim exists only while the drawer is open', async () => {
    loginAsToken('token-admin')
    const { container } = renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )
    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    expect(container.querySelector('.sidebar-scrim')).toBeNull()
  })

  it('31. desktop is unaffected: the close button and scrim are display:none above the breakpoint', () => {
    const drawerBlock = css.indexOf('@media (max-width: 1000px)')
    expect(drawerBlock).toBeGreaterThan(-1)

    // Both are declared display:none in the base (desktop) cascade...
    const baseClose = css.search(/\.sidebar-close \{[^}]*display: none;/)
    const baseScrim = css.search(/\.sidebar-scrim \{\s*display: none;/)
    expect(baseClose).toBeGreaterThan(-1)
    expect(baseScrim).toBeGreaterThan(-1)
    expect(baseClose).toBeLessThan(drawerBlock)
    expect(baseScrim).toBeLessThan(drawerBlock)

    // ...and only turned on inside the drawer breakpoint.
    const inDrawer = css.slice(drawerBlock)
    expect(inDrawer).toMatch(/\.sidebar-close \{\s*display: inline-grid;/)
    expect(inDrawer).toMatch(/\.sidebar-scrim \{\s*display: block;/)
  })

  it('31b. the scrim sits above the header and below the drawer, so it catches the tap', () => {
    // The layering that made the Menu button unreachable: header 5 < scrim 15 < sidebar 20.
    const drawerBlock = css.slice(css.indexOf('@media (max-width: 1000px)'))
    expect(css).toMatch(/\.app-header \{[\s\S]*?z-index: 5;/)
    expect(drawerBlock).toMatch(/\.sidebar \{[\s\S]*?z-index: 20;/)
    expect(drawerBlock).toMatch(/\.sidebar-scrim \{[\s\S]*?z-index: 15;/)
  })

  it('navigating closes the drawer rather than leaving it over the new page', async () => {
    const { user, container } = await openDrawer()

    await user.click(screen.getByRole('link', { name: 'Shed Visits' }))

    expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell-nav-open')
  })
})

describe('AppShell naming', () => {
  it('32. the archive is called "Shed Visits", while its route is unchanged', async () => {
    loginAsToken('token-admin')
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )

    expect(await screen.findByText('Alice Admin')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Shed Visits' })
    expect(link).toHaveAttribute('href', '/shed-visit-history')
    expect(screen.queryByRole('link', { name: /Shed Visit History/ })).toBeNull()
  })
})
