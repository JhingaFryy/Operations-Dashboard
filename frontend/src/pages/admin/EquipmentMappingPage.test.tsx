import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../../test/testUtils'
import { server } from '../../mocks/server'
import { EquipmentMappingPage } from './EquipmentMappingPage'

async function selectFamily(user: ReturnType<typeof userEvent.setup>) {
  const familySelect = await screen.findByLabelText(/locomotive family/i)
  await user.selectOptions(familySelect, '3PHASE')
}

describe('EquipmentMappingPage', () => {
  it('Booking Pool Hardening: terminology no longer claims booking routing', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<EquipmentMappingPage />)

    await screen.findByRole('heading', { name: 'Equipment Responsibility Mapping' })
    expect(screen.getByText(/does not control booking visibility/i)).toBeInTheDocument()
    expect(screen.queryByText(/routing/i)).not.toBeInTheDocument()
  })

  it('loads families and lets the user pick one', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)

    const familySelect = await screen.findByLabelText(/locomotive family/i)
    expect(within(familySelect).getByText(/3-Phase Locomotives/)).toBeInTheDocument()
    expect(within(familySelect).getByText(/Conventional Locomotives/)).toBeInTheDocument()

    await user.selectOptions(familySelect, '3PHASE')
    await screen.findByLabelText('Equipment')
  })

  it('loads root nodes for the selected family', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    expect(within(rootSelect).getByText('Auxiliary Converter')).toBeInTheDocument()
    expect(within(rootSelect).getByText('Contactor')).toBeInTheDocument()
    expect(within(rootSelect).getByText('Standalone Part')).toBeInTheDocument()
  })

  it('selecting a node with children reveals the next level', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Auxiliary Converter')

    const level2 = await screen.findByLabelText('Level 2')
    expect(within(level2).getByText('ABB')).toBeInTheDocument()

    // A node with no children (Contactor) never grows a further dropdown.
    await user.selectOptions(rootSelect, 'Contactor')
    await waitFor(() => expect(screen.queryByLabelText('Level 2')).not.toBeInTheDocument())
  })

  it('changing a higher level clears lower-level selections', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Auxiliary Converter')
    const level2 = await screen.findByLabelText('Level 2')
    await user.selectOptions(level2, 'ABB')
    await screen.findByLabelText('Level 3')

    // Switching the root level away clears everything beneath it.
    await user.selectOptions(rootSelect, 'Standalone Part')
    await waitFor(() => expect(screen.queryByLabelText('Level 2')).not.toBeInTheDocument())
  })

  it('shows selected node details: name, type, path, id', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Auxiliary Converter')

    expect(await screen.findByRole('heading', { name: 'Auxiliary Converter' })).toBeInTheDocument()
    expect(screen.getByText('EQUIPMENT')).toBeInTheDocument()
    expect(screen.getByText('Node ID: 100')).toBeInTheDocument()
  })

  it('shows the direct exact mapping when one exists', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Contactor')

    await screen.findByText('Exact mapping on this node.')
    const directBlock = screen.getByText('Direct Mapping').closest('.mapping-block')! as HTMLElement
    expect(within(directBlock).getByText('M1-HR')).toBeInTheDocument()
    expect(within(directBlock).getByText('M2-HR')).toBeInTheDocument()
  })

  it('shows inherited/resolved mapping distinctly from direct when there is no direct mapping', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Auxiliary Converter')
    const level2 = await screen.findByLabelText('Level 2')
    await user.selectOptions(level2, 'ABB')

    const directBlock = await screen.findByText('Direct Mapping')
    expect(directBlock.closest('.mapping-block')).toHaveTextContent('None')

    const resolvedBlock = screen.getByText('Resolved Responsibility').closest('.mapping-block')! as HTMLElement
    expect(within(resolvedBlock).getByText('M35-Aux')).toBeInTheDocument()
    expect(resolvedBlock).toHaveTextContent('Inherited from')
    expect(resolvedBlock).toHaveTextContent('Auxiliary Converter')
  })

  it('shows NONE when there is no direct or inherited mapping anywhere', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Standalone Part')

    await screen.findByText('No responsible section configured (direct or inherited).')
  })

  it('search finds nodes and shows disambiguating paths for duplicate names', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    await user.type(await screen.findByLabelText(/equipment search/i), 'igbt')

    const results = await screen.findByLabelText('Search results')
    await waitFor(() => expect(within(results).getAllByText('IGBT')).toHaveLength(2))

    expect(
      within(results).getByText('Auxiliary Converter → ABB → Converter Module → IGBT'),
    ).toBeInTheDocument()
    expect(
      within(results).getByText('Traction Converter → Siemens → Converter Module → IGBT'),
    ).toBeInTheDocument()
  })

  it('selecting a search result loads its path and mapping', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    await user.type(await screen.findByLabelText(/equipment search/i), 'igbt')
    const results = await screen.findByLabelText('Search results')
    await waitFor(() => expect(within(results).getAllByText('IGBT')).toHaveLength(2))

    await user.click(
      within(results).getByText('Auxiliary Converter → ABB → Converter Module → IGBT'),
    )

    const panel = (await screen.findByRole('heading', { name: 'IGBT' })).closest(
      '.selected-node-panel',
    ) as HTMLElement
    expect(within(panel).getByText('Auxiliary Converter → ABB → Converter Module → IGBT')).toBeInTheDocument()
    expect(within(panel).getByText(/inherited from/i)).toBeInTheDocument()
  })

  it('saves a mapping with multiple sections selected, then clearing it exposes ancestor routing', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Auxiliary Converter')
    const level2 = await screen.findByLabelText('Level 2')
    await user.selectOptions(level2, 'ABB')

    await screen.findByText('Mapped Sections')
    await user.click(screen.getByLabelText(/M9-HR/))
    await user.click(screen.getByLabelText(/M1-HR/))
    await user.click(screen.getByRole('button', { name: /save mapping/i }))

    await screen.findByText('Mapping saved.')
    const directBlock = screen.getByText('Direct Mapping').closest('.mapping-block')! as HTMLElement
    expect(within(directBlock).getByText('M9-HR')).toBeInTheDocument()
    expect(within(directBlock).getByText('M1-HR')).toBeInTheDocument()
    await screen.findByText('Exact mapping on this node.')

    // Now clear it — ancestor (Auxiliary Converter -> M35-Aux) should take over.
    await user.click(screen.getByRole('button', { name: /clear direct mapping/i }))
    await user.click(await screen.findByRole('button', { name: /confirm clear/i }))

    await screen.findByText('Direct mapping cleared.')
    await waitFor(() =>
      expect(screen.getByText('Direct Mapping').closest('.mapping-block')).toHaveTextContent('None'),
    )
    const resolvedBlock = screen.getByText('Resolved Responsibility').closest('.mapping-block')! as HTMLElement
    expect(within(resolvedBlock).getByText('M35-Aux')).toBeInTheDocument()
    expect(resolvedBlock).toHaveTextContent('Auxiliary Converter')
  })

  it('shows a friendly message when Loco Master is unavailable for families', async () => {
    server.use(
      http.get('/api/equipment/families', () =>
        HttpResponse.json({ detail: 'Loco Master is unavailable.' }, { status: 502 }),
      ),
    )
    loginAsToken('token-admin')
    renderWithProviders(<EquipmentMappingPage />)

    await screen.findByText(/equipment service is currently unavailable/i)
  })

  it('shows a friendly message when Loco Master is unavailable while loading the hierarchy', async () => {
    server.use(
      http.get('/api/equipment/nodes', () =>
        HttpResponse.json({ detail: 'Loco Master is unavailable.' }, { status: 502 }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)

    await screen.findByText(/equipment service is currently unavailable/i)
  })

  it('shows a friendly message when mapping fails to load (e.g. 404)', async () => {
    server.use(
      http.get('/api/equipment/nodes/:id/mapping', () =>
        HttpResponse.json({ detail: 'Equipment node not found' }, { status: 404 }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Contactor')

    await screen.findByText(/could not be found/i)
  })

  it('shows a validation error message when saving fails with 422', async () => {
    server.use(
      http.put('/api/equipment/nodes/:id/mapping', () =>
        HttpResponse.json({ detail: 'Unknown section code(s): BOGUS' }, { status: 422 }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Contactor')

    await screen.findByText('Mapped Sections')
    await user.click(screen.getByRole('button', { name: /save mapping/i }))

    await screen.findByText('Unknown section code(s): BOGUS')
  })

  it('shows a permission-denied message when the backend rejects a save with 403', async () => {
    server.use(
      http.put('/api/equipment/nodes/:id/mapping', () =>
        HttpResponse.json({ detail: 'forbidden' }, { status: 403 }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    const rootSelect = await screen.findByLabelText('Equipment')
    await user.selectOptions(rootSelect, 'Contactor')

    await screen.findByText('Mapped Sections')
    await user.click(screen.getByRole('button', { name: /save mapping/i }))

    await screen.findByText(/don't have permission/i)
  })
})

/**
 * The permanent "+ Add Equipment" action.
 *
 * TWO DIFFERENT CAPABILITIES, on purpose. A SHIFT Supervisor may create equipment during
 * Shed In, but only from a dead-end search, always as a root of the locomotive's own
 * family. This page's button places equipment anywhere in the hierarchy - that is
 * administration of the master data, so it is Admin only, and a Supervisor who may edit
 * mappings here still does not get it.
 */
describe('EquipmentMappingPage: + Add Equipment', () => {
  async function openDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: /\+ add equipment/i }))
    return screen.findByRole('dialog', { name: /add equipment/i })
  }

  it('is permanently visible to an Admin, before anything is chosen', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<EquipmentMappingPage />)

    expect(await screen.findByRole('button', { name: /\+ add equipment/i })).toBeInTheDocument()
    // No family picked, no search typed, nothing selected.
    expect(screen.queryByLabelText('Equipment')).toBeNull()
  })

  it('stays visible once a family is chosen and equipment is listed', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await screen.findByLabelText('Equipment')

    expect(screen.getByRole('button', { name: /\+ add equipment/i })).toBeInTheDocument()
  })

  it('stays visible with equipment selected', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await user.selectOptions(await screen.findByLabelText('Equipment'), 'Auxiliary Converter')
    await screen.findByRole('heading', { name: 'Auxiliary Converter' })

    expect(screen.getByRole('button', { name: /\+ add equipment/i })).toBeInTheDocument()
  })

  it('is withheld from a Supervisor who may edit mappings but is not an Admin', async () => {
    loginAsToken('token-sup-permitted')
    renderWithProviders(<EquipmentMappingPage />)

    await screen.findByRole('heading', { name: 'Equipment Responsibility Mapping' })
    expect(screen.queryByRole('button', { name: /\+ add equipment/i })).toBeNull()
  })

  it('refuses a non-Admin server-side even if the button is bypassed', async () => {
    // The button is usability; this is the boundary. The mock mirrors require_admin.
    loginAsToken('token-sup-permitted')
    const { adminCreateNode } = await import('../../api/equipment')

    await expect(
      adminCreateNode({ familyCode: '3PHASE', parentId: null, name: 'X', sectionCodes: ['M1-HR'] }),
    ).rejects.toMatchObject({ status: 403 })
  })

  it('creates equipment at the top level, mapped to several sections', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await screen.findByLabelText('Equipment')

    const dialog = await openDialog(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Oil Cooler Fan')
    await user.click(within(dialog).getByRole('checkbox', { name: 'M1-HR' }))
    await user.click(within(dialog).getByRole('checkbox', { name: 'M2-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const notice = await screen.findByText(/"Oil Cooler Fan" was added/)
    expect(notice.textContent).toContain('M1-HR')
    expect(notice.textContent).toContain('M2-HR')
  })

  it('selects the new equipment and shows its mapping without a second click', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await screen.findByLabelText('Equipment')

    const dialog = await openDialog(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Oil Cooler Fan')
    await user.click(within(dialog).getByRole('checkbox', { name: 'M1-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    expect(await screen.findByRole('heading', { name: 'Oil Cooler Fan' })).toBeInTheDocument()
    // The page's own mapping editor now shows the sections it was created with.
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'M1-HR (M1-HR)' })).toBeChecked(),
    )
  })

  it('refreshes the hierarchy so the new equipment appears in it', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    const roots = (await screen.findByLabelText('Equipment')) as HTMLSelectElement
    expect(Array.from(roots.options).map((o) => o.textContent)).not.toContain('Oil Cooler Fan')

    const dialog = await openDialog(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Oil Cooler Fan')
    await user.click(within(dialog).getByRole('checkbox', { name: 'M1-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    await waitFor(() => {
      const after = screen.getByLabelText('Equipment') as HTMLSelectElement
      expect(Array.from(after.options).map((o) => o.textContent)).toContain('Oil Cooler Fan')
    })
  })

  it('creates equipment under the item currently selected', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await user.selectOptions(await screen.findByLabelText('Equipment'), 'Auxiliary Converter')
    await screen.findByRole('heading', { name: 'Auxiliary Converter' })

    const dialog = await openDialog(user)
    await user.click(within(dialog).getByRole('radio', { name: /under auxiliary converter/i }))
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Oil Cooler Fan')
    // No section needed: a child inherits its ancestors' responsible sections.
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByText(/inheriting its parent/i)).toBeInTheDocument()
  })

  it('a top-level item must be mapped, or it could never be booked', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await screen.findByLabelText('Equipment')

    const dialog = await openDialog(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Oil Cooler Fan')
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(/needs at least one section/i)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('EquipmentMappingPage: existing equipment is reused, not duplicated', () => {
  async function openDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: /\+ add equipment/i }))
    return screen.findByRole('dialog', { name: /add equipment/i })
  }

  async function setUp(user: ReturnType<typeof userEvent.setup>) {
    loginAsToken('token-admin')
    renderWithProviders(<EquipmentMappingPage />)
    await selectFamily(user)
    await screen.findByLabelText('Equipment')
    return openDialog(user)
  }

  it('says so when the name already exists, and shows where and who maintains it', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)

    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Auxiliary Converter')

    expect(await within(dialog).findByText('Equipment already exists.')).toBeInTheDocument()
    const existing = within(dialog).getByRole('group', { name: /existing equipment/i })
    expect(within(existing).getByText('Auxiliary Converter')).toBeInTheDocument()
    expect(within(existing).getByText(/Sections:/)).toBeInTheDocument()
  })

  it('matches regardless of case and repeated whitespace', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)

    await user.type(within(dialog).getByLabelText(/equipment name/i), '  auxiliary   CONVERTER ')

    expect(await within(dialog).findByText('Equipment already exists.')).toBeInTheDocument()
  })

  it('will not submit a creation while an existing match is unresolved', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)

    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Auxiliary Converter')
    await within(dialog).findByText('Equipment already exists.')

    // The user must say what they mean: reuse that one, or explicitly create another.
    expect(within(dialog).getByRole('button', { name: /^add equipment$/i })).toBeDisabled()
  })

  it('adds sections to the existing node instead of creating a second one', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Auxiliary Converter')
    await within(dialog).findByText('Equipment already exists.')

    await user.click(within(dialog).getByRole('button', { name: /auxiliary converter/i }))
    await user.click(await within(dialog).findByRole('checkbox', { name: 'M9-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /add section mapping/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const notice = await screen.findByText(/already existed/i)
    expect(notice.textContent).toContain('M9-HR')
    // Same node, selected on the page - not a new one.
    expect(screen.getByRole('heading', { name: 'Auxiliary Converter' })).toBeInTheDocument()
    const roots = screen.getByLabelText('Equipment') as HTMLSelectElement
    const named = Array.from(roots.options).filter((o) => o.textContent === 'Auxiliary Converter')
    expect(named).toHaveLength(1)
  })

  it('keeps the sections the equipment already had', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Auxiliary Converter')
    await within(dialog).findByText('Equipment already exists.')
    await user.click(within(dialog).getByRole('button', { name: /auxiliary converter/i }))

    // Existing mappings are shown ticked and locked: this dialog adds, it never removes.
    // Its accessible name carries the state too ("M35-Aux already mapped"), so match loosely.
    const mapped = await within(dialog).findByRole('checkbox', { name: /M35-Aux/ })
    expect(mapped).toBeChecked()
    expect(mapped).toBeDisabled()

    await user.click(within(dialog).getByRole('checkbox', { name: 'M9-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /add section mapping/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const notice = await screen.findByText(/already existed/i)
    expect(notice.textContent).toContain('M35-Aux')
    expect(notice.textContent).toContain('M9-HR')
  })

  it('re-adding a section it already has changes nothing', async () => {
    loginAsToken('token-admin')
    const { addNodeSections, getMapping } = await import('../../api/equipment')

    const before = await getMapping(100)
    const first = await addNodeSections(100, before.section_codes)
    const second = await addNodeSections(100, before.section_codes)

    expect(first.newly_added).toEqual([])
    expect(first.already_present).toEqual(before.section_codes)
    expect(second.section_codes).toEqual(first.section_codes)
    expect((await getMapping(100)).section_codes).toEqual(before.section_codes)
  })

  it('offers an explicit escape hatch to create a genuinely new item anyway', async () => {
    const user = userEvent.setup()
    const dialog = await setUp(user)
    await user.type(within(dialog).getByLabelText(/equipment name/i), 'Auxiliary Converter')
    await within(dialog).findByText('Equipment already exists.')

    await user.click(within(dialog).getByRole('button', { name: /create new equipment instead/i }))

    // Now it is a plain creation again - and the server's sibling rule still applies,
    // so an identical root is still refused.
    await user.click(within(dialog).getByRole('checkbox', { name: 'M1-HR' }))
    await user.click(within(dialog).getByRole('button', { name: /^add equipment$/i }))

    const refusal = await within(dialog).findByRole('alert')
    expect(refusal).toHaveTextContent(/already exists/i)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

/**
 * Editing EXISTING equipment. ADMIN ONLY.
 *
 * The authorization boundary is the server (require_admin on PATCH
 * /api/equipment/admin/nodes/{id}); these tests cover what the dialog itself must get right,
 * and the most important of those is that it never saves a section set it did not fully load.
 */
describe('EquipmentMappingPage: Edit Equipment', () => {
  async function selectNode(user: ReturnType<typeof userEvent.setup>) {
    await selectFamily(user)
    await user.selectOptions(await screen.findByLabelText('Equipment'), 'Auxiliary Converter')
    await screen.findByRole('heading', { name: 'Auxiliary Converter' })
  }

  async function openEditor(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: /^edit equipment$/i }))
    return screen.findByRole('dialog', { name: /edit equipment/i })
  }

  it('offers Edit Equipment to an Admin once equipment is selected', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)

    expect(screen.getByRole('button', { name: /^edit equipment$/i })).toBeInTheDocument()
  })

  it('does not offer it before anything is selected', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<EquipmentMappingPage />)

    await screen.findByRole('heading', { name: 'Equipment Responsibility Mapping' })
    expect(screen.queryByRole('button', { name: /^edit equipment$/i })).toBeNull()
  })

  it('is withheld from a Supervisor who may edit mappings but is not an Admin', async () => {
    loginAsToken('token-sup-permitted')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)

    expect(screen.queryByRole('button', { name: /^edit equipment$/i })).toBeNull()
  })

  it('shows family and location read-only, with no way to move the node', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)
    const dialog = await openEditor(user)

    expect(within(dialog).getByText('Family')).toBeInTheDocument()
    expect(within(dialog).getByText('Location')).toBeInTheDocument()
    // No parent or family control exists at all - moving equipment is out of scope here.
    expect(within(dialog).queryByLabelText(/parent/i)).toBeNull()
    expect(within(dialog).queryByLabelText(/family/i)).toBeNull()
  })

  it('renames equipment and keeps it selected afterwards', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)
    const dialog = await openEditor(user)

    const nameField = within(dialog).getByLabelText('Name')
    await user.clear(nameField)
    await user.type(nameField, 'Aux Converter IGBT')
    await user.click(within(dialog).getByRole('button', { name: /save changes/i }))

    // The renamed node stays selected - a rename must not throw away the administrator's place.
    expect(await screen.findByRole('heading', { name: 'Aux Converter IGBT' })).toBeInTheDocument()
  })

  it('refuses a rename that collides with a sibling, and says which one', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)
    const dialog = await openEditor(user)

    const nameField = within(dialog).getByLabelText('Name')
    await user.clear(nameField)
    await user.type(nameField, 'Traction Converter')
    await user.click(within(dialog).getByRole('button', { name: /save changes/i }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/already exists/i)
  })

  it('will not save with every section unticked', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)
    const dialog = await openEditor(user)

    const checked = within(dialog)
      .getAllByRole('checkbox')
      .filter((box) => (box as HTMLInputElement).checked && (box as HTMLInputElement).name !== '')
    for (const box of checked) await user.click(box)

    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled(),
    )
  })

  it('disables Save when nothing has been changed', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)
    const dialog = await openEditor(user)

    // An untouched dialog has nothing to write - saving would be a no-op round trip.
    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled(),
    )
  })

  it('refuses to edit sections at all when the current mapping could not be loaded', async () => {
    // THE dangerous case: the mapping PUT is replace-set, so saving a set the dialog never
    // managed to load would silently unmap sections the administrator never saw.
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<EquipmentMappingPage />)
    await selectNode(user)

    server.use(
      http.get('/api/equipment/nodes/:id/mapping', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    const dialog = await openEditor(user)

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      /could not load this equipment’s current sections/i,
    )
    expect(within(dialog).getByRole('button', { name: /save changes/i })).toBeDisabled()
  })
})
