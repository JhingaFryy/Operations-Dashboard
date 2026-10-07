import { screen, within } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'

type User = ReturnType<typeof userEvent.setup>

/** Choose equipment on a Log Book booking row the only way the UI now allows: type,
 * then click a record the server returned. There is no equipment dropdown any more -
 * see components/equipment/EquipmentSelector.tsx for why. */
export async function selectEquipment(user: User, row: HTMLElement, name: string) {
  await user.type(within(row).getByLabelText(/^equipment$/i), name)
  const results = await within(row).findByRole('listbox', { name: /equipment results/i }, {
    timeout: 3000,
  })
  await user.click(within(results).getByRole('option', { name: new RegExp(name, 'i') }))
}

/** The name currently shown in a row's equipment field, or null when nothing is selected. */
export function selectedEquipmentName(row: HTMLElement): string | null {
  return row.querySelector('.equipment-selected-name')?.textContent ?? null
}

export function bookingRow(index: number): HTMLElement {
  return screen
    .getByRole('heading', { name: `Booking #${index + 1}` })
    .closest('.log-book-booking-row') as HTMLElement
}
