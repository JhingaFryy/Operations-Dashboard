import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  THEME_STORAGE_KEY,
  ThemeModeContext,
  applyTheme,
  readStored,
  systemMode,
  type ThemeMode,
} from './themeMode'

export function ThemeModeProvider({ children }: { children: React.ReactNode }) {
  const [stored, setStored] = useState<ThemeMode | null>(readStored)
  const [system, setSystem] = useState<ThemeMode>(systemMode)

  useEffect(() => {
    if (stored !== null) return
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!query) return
    const listener = (event: MediaQueryListEvent) => setSystem(event.matches ? 'dark' : 'light')
    query.addEventListener('change', listener)
    return () => query.removeEventListener('change', listener)
  }, [stored])

  const mode: ThemeMode = stored ?? system

  useEffect(() => {
    applyTheme(mode)
  }, [mode])

  const setMode = useCallback((next: ThemeMode) => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // Not remembering the choice is acceptable; ignoring the click is not.
    }
    setStored(next)
  }, [])

  const toggle = useCallback(() => setMode(mode === 'dark' ? 'light' : 'dark'), [mode, setMode])

  const value = useMemo(
    () => ({ mode, followsSystem: stored === null, toggle, setMode }),
    [mode, stored, toggle, setMode],
  )

  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>
}
