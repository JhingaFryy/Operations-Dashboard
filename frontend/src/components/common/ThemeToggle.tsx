import { useThemeMode } from '../../theme/useThemeMode'

/**
 * The one control that switches the whole product between light and dark.
 *
 * A real <button>: reachable by keyboard, carrying an aria-label that states what pressing it will
 * do (not merely what the current theme is) and aria-pressed for the current state, so it is not a
 * colour-only affordance. The icon is inline SVG - this frontend deliberately ships no icon font.
 */
export function ThemeToggle() {
  const { mode, toggle } = useThemeMode()
  const goingDark = mode === 'light'
  const label = goingDark ? 'Switch to dark theme' : 'Switch to light theme'

  return (
    <button
      type="button"
      className="btn btn-secondary btn-small theme-toggle"
      onClick={toggle}
      aria-label={label}
      aria-pressed={mode === 'dark'}
      title={label}
    >
      <span className="theme-toggle-icon" aria-hidden="true">
        {goingDark ? (
          // Moon: pressing this takes you to dark.
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        ) : (
          // Sun: pressing this takes you to light.
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
        )}
      </span>
      <span className="theme-toggle-text">{goingDark ? 'Dark' : 'Light'}</span>
    </button>
  )
}
