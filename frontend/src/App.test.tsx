import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderWithProviders, loginAsToken } from './test/testUtils'
import App from './App'

describe('App routing/permissions', () => {
  it('Admin can open Equipment Mapping directly', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<App />, { route: '/admin/equipment-mapping' })

    expect(await screen.findByRole('heading', { name: /equipment responsibility mapping/i })).toBeInTheDocument()
  })

  it('a Supervisor without permission is denied even navigating directly', async () => {
    loginAsToken('token-sup-denied')
    renderWithProviders(<App />, { route: '/admin/equipment-mapping' })

    expect(await screen.findByText(/permission denied/i)).toBeInTheDocument()
  })
})
