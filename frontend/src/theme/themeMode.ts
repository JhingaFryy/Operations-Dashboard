import { createContext } from 'react'

/**
 * Light / dark mode for the Operations Dashboard.
 *
 * The colours themselves live in index.css as one set of custom properties per theme; this only
 * decides which set is active, by putting `data-theme` on <html>. No component branches on the
 * theme - that is what keeps a second theme from turning into a second design.
 *
 * FIRST VISIT follows the operating system, which is what the BL-DCMS Dashboard does
 * (src/contexts/ThemeModeContext.tsx there defaults to 'system'), so the two products open the
 * same way on the same machine. The moment the viewer picks a side, that choice is stored and the
 * system is no longer consulted - a shed screen must not change theme by itself mid-shift.
 *
 * The preference is per browser: localStorage, no API call, no account setting, no reload.
 */

export type ThemeMode = 'light' | 'dark'

const STORAGE_KEY = 'operations-dashboard-theme'

export interface ThemeModeContextValue {
  mode: ThemeMode
  /** True while the theme is still following the operating system (nothing stored yet). */
  followsSystem: boolean
  toggle: () => void
  setMode: (mode: ThemeMode) => void
}

export const ThemeModeContext = createContext<ThemeModeContextValue | undefined>(undefined)

export function readStored(): ThemeMode | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : null
  } catch {
    // Private windows and blocked site data throw on access; the theme still works, it just
    // cannot be remembered.
    return null
  }
}

export function systemMode(): ThemeMode {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.dataset.theme = mode
}

export const THEME_STORAGE_KEY = STORAGE_KEY
