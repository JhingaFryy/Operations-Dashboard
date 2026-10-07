import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from '../mocks/server'
import { resetFixtures } from '../mocks/fixtures'
import { setReadyBlocker, setShedOutBlocker } from '../mocks/handlers'
import { resetHistoryMock } from '../mocks/visitHistory'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  resetFixtures()
  setReadyBlocker(null)
  setShedOutBlocker(null)
  resetHistoryMock()
  localStorage.clear()
})
afterAll(() => server.close())
