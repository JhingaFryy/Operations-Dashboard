import { Link } from 'react-router-dom'
import { equipmentPathLabel } from '../../lib/format'
import { BookingStatusBadge, Tag } from '../ui/StatusBadge'
import { EmptyState } from '../ui/States'
import type { TestBeforeBooking } from '../../types'

export function TestBeforeBookingList({
  bookings,
  emptyMessage = 'No Test Before findings recorded yet.',
}: {
  bookings: TestBeforeBooking[]
  emptyMessage?: string
}) {
  if (bookings.length === 0) {
    return <EmptyState title={emptyMessage} />
  }

  return (
    <div className="test-before-booking-list">
      {bookings.map((booking) => (
        <div className="panel test-before-booking-card" key={booking.id}>
          <div className="test-before-booking-header">
            <strong>{booking.equipment_node_name ?? 'Unknown equipment'}</strong>
            {booking.defect_type && <Tag tone="info">{booking.defect_type.name}</Tag>}
          </div>

          {booking.equipment_path.length > 0 && (
            <p className="equipment-picker-path">{equipmentPathLabel(booking.equipment_path)}</p>
          )}

          <p className="assignment-card-description">{booking.description}</p>

          <div className="assignment-card-status-row">
            <span className="assignment-card-status-label">Booking Status:</span>
            <BookingStatusBadge status={booking.status} size="small" />
          </div>

          <h4>Responsible Sections</h4>
          <ul className="test-before-section-list">
            {booking.assignments.map((a) => (
              <li key={a.id}>
                <span>{a.section_code}</span>
                <BookingStatusBadge status={a.status} size="small" />
                <Link to={`/section-dashboard?section=${encodeURIComponent(a.section_code)}`}>
                  View in Section Dashboard
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
