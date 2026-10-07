import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../test/testUtils'
import { LoginPage } from './LoginPage'
import { ProtectedRoute } from '../auth/ProtectedRoute'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <div>Protected Home</div>
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

describe('LoginPage', () => {
  it('redirects an unauthenticated visitor from a protected route to /login', async () => {
    renderWithProviders(<App />, { route: '/' })

    await waitFor(() => expect(screen.getByLabelText(/employee id/i)).toBeInTheDocument())
  })

  it('logs in successfully and reaches the originally requested page', async () => {
    const user = userEvent.setup()
    renderWithProviders(<App />, { route: '/' })

    await screen.findByLabelText(/employee id/i)
    await user.type(screen.getByLabelText(/employee id/i), 'ADMIN1')
    await user.type(screen.getByLabelText(/^password$/i), 'pass')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await screen.findByText('Protected Home')
  })

  it('shows an error message on invalid credentials', async () => {
    const user = userEvent.setup()
    renderWithProviders(<App />, { route: '/login' })

    await screen.findByLabelText(/employee id/i)
    await user.type(screen.getByLabelText(/employee id/i), 'ADMIN1')
    await user.type(screen.getByLabelText(/^password$/i), 'wrong-password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid employee id or password/i)
  })
})
