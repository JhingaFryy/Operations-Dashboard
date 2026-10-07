import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { setToken } from '../api/client'
import { ThemeModeProvider } from '../theme/ThemeModeProvider'

export function renderWithProviders(ui: ReactElement, { route = '/' }: { route?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ThemeModeProvider>
        <AuthProvider>{ui}</AuthProvider>
      </ThemeModeProvider>
    </MemoryRouter>,
  )
}

/** Seeds a token directly so a test can start already-authenticated
 * without exercising the login form. */
export function loginAsToken(token: string) {
  setToken(token)
}
