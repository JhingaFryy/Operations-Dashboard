import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeModeProvider } from './ThemeModeProvider'
import { THEME_STORAGE_KEY } from './themeMode'
import { useThemeMode } from './useThemeMode'
import { ThemeToggle } from '../components/common/ThemeToggle'
import css from '../index.css?raw'

function systemPrefers(dark: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: dark && query.includes('dark'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }))
}

function Harness() {
  const { mode, followsSystem } = useThemeMode()
  return (
    <>
      <span data-testid="mode">{mode}</span>
      <span data-testid="follows">{String(followsSystem)}</span>
      <ThemeToggle />
    </>
  )
}

function renderThemed() {
  return render(
    <ThemeModeProvider>
      <Harness />
    </ThemeModeProvider>,
  )
}

describe('theme mode', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    systemPrefers(false)
  })

  it('follows the operating system on a first visit, like the BL-DCMS Dashboard', () => {
    renderThemed()
    expect(screen.getByTestId('mode')).toHaveTextContent('light')
    expect(screen.getByTestId('follows')).toHaveTextContent('true')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it('opens dark when the operating system is dark and nothing is stored', () => {
    systemPrefers(true)
    renderThemed()
    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('switches to dark and persists the choice', () => {
    renderThemed()
    fireEvent.click(screen.getByRole('button', { name: /switch to dark theme/i }))

    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(screen.getByTestId('follows')).toHaveTextContent('false')
  })

  it('switches back to light and persists that too', () => {
    renderThemed()
    fireEvent.click(screen.getByRole('button', { name: /switch to dark theme/i }))
    fireEvent.click(screen.getByRole('button', { name: /switch to light theme/i }))

    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
  })

  it('applies a saved dark preference at startup, over a light system setting', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    systemPrefers(false)
    renderThemed()

    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByTestId('follows')).toHaveTextContent('false')
  })

  it('ignores a corrupt stored value rather than breaking the app', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'neon')
    renderThemed()
    expect(screen.getByTestId('mode')).toHaveTextContent('light')
  })

  it('states what the button will do, and its current state', () => {
    renderThemed()
    const toggle = screen.getByRole('button', { name: /switch to dark theme/i })
    expect(toggle).toHaveAttribute('type', 'button')
    expect(toggle).toHaveAttribute('title', 'Switch to dark theme')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /switch to light theme/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('never asks the backend for the theme', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    renderThemed()
    fireEvent.click(screen.getByRole('button', { name: /switch to dark theme/i }))
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('the two themes are complete', () => {
  it('light mode redefines the accents and every status colour, not just surfaces', () => {
    const light = css.slice(css.indexOf(":root[data-theme='light']"))
    const block = light.slice(0, light.indexOf('}'))
    for (const token of [
      '--color-bg', '--color-surface', '--color-text', '--color-text-muted', '--color-border',
      '--color-primary', '--color-cyan', '--color-focus',
      '--color-danger', '--color-success', '--color-warn', '--color-active', '--color-info',
      '--color-neutral',
    ]) {
      expect(block, `light theme is missing ${token}`).toContain(`${token}:`)
    }
  })

  it('status keeps a background and a border in light mode, so badges keep their shape', () => {
    const light = css.slice(css.indexOf(":root[data-theme='light']"))
    const block = light.slice(0, light.indexOf('}'))
    for (const status of ['danger', 'success', 'warn', 'active', 'info', 'neutral']) {
      expect(block).toContain(`--color-${status}-bg:`)
      expect(block).toContain(`--color-${status}-border:`)
    }
  })

  it('never paints theme-flipping text or borders onto the always-navy sidebar', () => {
    // .sidebar and everything inside it keep a deep navy background in BOTH
    // themes, so --color-text / --color-border / --color-cyan (which flip to
    // dark-on-light values) would be unreadable there. Guard the whole block.
    const start = css.indexOf('.sidebar {')
    const end = css.indexOf('.app-main {')
    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    const sidebar = css.slice(start, end)

    for (const token of ['--color-text)', '--color-border)', '--color-cyan)']) {
      expect(sidebar, `sidebar uses ${token}, which flips with the theme`).not.toContain(token)
    }
    expect(sidebar).toContain('--color-sidebar-text-strong')
    expect(sidebar).toContain('--color-sidebar-border')
    expect(sidebar).toContain('--color-sidebar-accent')
  })

  it('declares every sidebar token once, so light and dark share one navy rail', () => {
    // Parity with BL-DCMS, whose drawer is theme-independent
    // (Dashboard/src/components/AppLayout.tsx paints drawerContent with a fixed
    // gradients.navy background and #E7EDFB text, with no palette.mode branch).
    // A --color-sidebar-* override inside the light block would silently give
    // Operations Dashboard a rail that neither product has.
    const lightStart = css.indexOf(":root[data-theme='light']")
    const lightBlock = css.slice(lightStart, css.indexOf('}', lightStart))
    expect(lightBlock).not.toMatch(/--color-sidebar-[a-z-]*\s*:/)

    const base = css.slice(0, lightStart)
    for (const token of [
      '--color-sidebar-bg',
      '--color-sidebar-text',
      '--color-sidebar-muted',
      '--color-sidebar-active-bg',
      '--color-sidebar-text-strong',
      '--color-sidebar-border',
      '--color-sidebar-accent',
    ]) {
      const declared = css.split(`${token}:`).length - 1
      expect(declared, `${token} should be declared exactly once`).toBe(1)
      expect(base, `${token} should be declared in the base block`).toContain(`${token}:`)
    }
  })

  it('declares color-scheme for both themes so native controls follow', () => {
    expect(css).toContain('color-scheme: dark')
    expect(css).toContain('color-scheme: light')
  })
})
