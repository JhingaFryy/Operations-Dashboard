import { EquipmentSelector, type EquipmentSelection } from './EquipmentSelector'
import { RoutingPreview } from './RoutingPreview'

/** The equipment field of one Log Book booking: search and select. Nothing else.
 *
 * TECHNOLOGY IS NOT A QUESTION FOR THE USER. Given `locoNumber`, the family is derived on
 * the server, so a booking can only ever name equipment of the locomotive's own technology
 * and the server independently refuses anything else.
 *
 * THIS FORM CANNOT CREATE EQUIPMENT. It used to offer "+ Add Equipment" on a dead-end
 * search, to Admin and SHIFT. That is withdrawn: creating equipment is master-data
 * administration, it belongs to the Equipment Responsibility Mapping page, and it is
 * Admin-only. The change is not cosmetic - POST /api/equipment/nodes was DELETED from the
 * API, so there is no endpoint left for this form to call even if a control reappeared
 * here. A dead-end search now simply says "No equipment found".
 */
export function BookingEquipmentField({
  idPrefix,
  locoNumber,
  selection,
  onChange,
}: {
  idPrefix: string
  locoNumber: string
  selection: EquipmentSelection | null
  onChange: (selection: EquipmentSelection | null) => void
}) {
  return (
    <div className="booking-equipment-field">
      <EquipmentSelector
        id={`${idPrefix}-equipment`}
        scope={{ locoNumber }}
        selection={selection}
        onChange={onChange}
      />

      {selection && <RoutingPreview nodeId={selection.node.id} />}
    </div>
  )
}
