import { useState } from 'react'
import { fetchSignedChecksheet, historyErrorMessage } from '../../api/shedVisitHistory'
import { formatDateTime } from '../../lib/format'
import { formatDuration } from '../../lib/localDateTime'
import { NOT_RECORDED, groupChecksheets } from '../../lib/visitHistory'
import type {
  VisitBookingHistory,
  VisitBookingSectionGroup,
  VisitChecksheetHistory,
  VisitHistoryDetail,
  VisitHistoryEvent,
} from '../../types'
import { BookingStatusBadge, ChecksheetStatusBadge, Tag } from '../ui/StatusBadge'

function When({ at }: { at: string | null | undefined }) {
  return at ? <>{formatDateTime(at)}</> : <span className="history-not-recorded">{NOT_RECORDED}</span>
}

function Who({ name }: { name: string | null | undefined }) {
  return name ? <>{name}</> : <span className="history-not-recorded">{NOT_RECORDED}</span>
}

function RawData({ raw }: { raw: Record<string, unknown> | null }) {
  if (!raw) return null
  return (
    <details className="history-raw">
      <summary>Raw data (Admin)</summary>
      <pre>{JSON.stringify(raw, null, 2)}</pre>
    </details>
  )
}

function EventList({ events, label }: { events: VisitHistoryEvent[]; label: string }) {
  if (events.length === 0) return <p className="history-muted">No events recorded.</p>
  return (
    <ol className="history-events" aria-label={label}>
      {events.map((e, i) => (
        <li key={`${e.at}-${i}`}>
          <span className="history-event-time">{formatDateTime(e.at)}</span>
          <span className="history-event-sentence">{e.sentence}</span>
          {e.remarks && <span className="history-event-remarks">“{e.remarks}”</span>}
          <RawData raw={e.raw} />
        </li>
      ))}
    </ol>
  )
}

// ------------------------------------------------------------------------------ timings --

function Timings({ detail }: { detail: VisitHistoryDetail }) {
  const { milestones, spans } = detail.timings
  return (
    <section className="history-block" aria-labelledby={`timings-${detail.visit.shed_visit_id}`}>
      <h3 id={`timings-${detail.visit.shed_visit_id}`}>Timings</h3>
      <div className="history-timings">
        <dl className="history-milestones">
          {milestones.map((m) => (
            <div key={m.key} className="history-kv">
              <dt>{m.label}</dt>
              <dd>
                <When at={m.at} />
                {m.at && m.actor_name && <span className="history-muted"> · {m.actor_name}</span>}
                {m.note && <span className="history-note"> · {m.note}</span>}
              </dd>
            </div>
          ))}
        </dl>
        <dl className="history-spans">
          {spans.map((s) => (
            <div key={s.key} className="history-kv">
              <dt>{s.label}</dt>
              <dd>
                {s.seconds == null ? (
                  <span className="history-not-recorded">{NOT_RECORDED}</span>
                ) : (
                  <>
                    {formatDuration(s.seconds)}
                    {s.running && <span className="history-muted"> (still running)</span>}
                  </>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  )
}

// ----------------------------------------------------------------------------- bookings --

function BookingItem({ booking }: { booking: VisitBookingHistory }) {
  const equipment = booking.equipment.name
    ? [...booking.equipment.path, booking.equipment.name].filter((v, i, a) => a.indexOf(v) === i).join(' → ')
    : booking.equipment.node_id != null
      ? `Equipment #${booking.equipment.node_id} (name unavailable)`
      : NOT_RECORDED
  return (
    <details className="history-booking">
      <summary>
        <span className="history-booking-title">
          Booking #{booking.booking_id} · {equipment}
        </span>
        <Tag>{booking.booking_source_label}</Tag>
        <BookingStatusBadge status={booking.status} size="small" />
      </summary>
      <div className="history-booking-body">
        <dl className="history-grid">
          <div className="history-kv"><dt>Defect</dt><dd>{booking.defect_type ?? NOT_RECORDED}</dd></div>
          <div className="history-kv"><dt>Remarks</dt><dd>{booking.remarks}</dd></div>
          <div className="history-kv">
            <dt>Raised</dt>
            <dd><When at={booking.created_at} /> · <Who name={booking.created_by_name} /></dd>
          </div>
          {booking.origin_checksheet_id != null && (
            <div className="history-kv"><dt>From checksheet</dt><dd>#{booking.origin_checksheet_id}</dd></div>
          )}
          <div className="history-kv">
            <dt>Responsible sections</dt>
            <dd>{booking.responsible_sections.length ? booking.responsible_sections.join(', ') : 'None assigned'}</dd>
          </div>
        </dl>

        {booking.assignments.length > 0 && (
          <div className="table-scroll">
            <table className="data-table history-table">
              <caption className="visually-hidden">Section work on booking #{booking.booking_id}</caption>
              <thead>
                <tr>
                  <th scope="col">Section</th>
                  <th scope="col">Status</th>
                  <th scope="col">Assigned</th>
                  <th scope="col">Started</th>
                  <th scope="col">Attended</th>
                  <th scope="col">Reopened</th>
                </tr>
              </thead>
              <tbody>
                {booking.assignments.map((a) => (
                  <tr key={a.assignment_id}>
                    <td>{a.section_name ?? a.section_code ?? `Section ${a.section_id}`}</td>
                    <td><BookingStatusBadge status={a.status} size="small" /></td>
                    <td><When at={a.assigned_at} /><br /><span className="history-muted"><Who name={a.assigned_by_name} /></span></td>
                    <td>
                      {a.started_at ? (
                        <><When at={a.started_at} /><br /><span className="history-muted"><Who name={a.started_by_name} /></span></>
                      ) : '—'}
                    </td>
                    <td>
                      {a.attended_at ? (
                        <>
                          <When at={a.attended_at} /><br />
                          <span className="history-muted"><Who name={a.attended_by_name} /></span>
                          {a.attendance_remarks && <><br />“{a.attendance_remarks}”</>}
                        </>
                      ) : '—'}
                    </td>
                    <td>
                      {a.reopens.length === 0 ? '—' : (
                        <ul className="history-reopens">
                          {a.reopens.map((r, i) => (
                            <li key={i}>
                              {formatDateTime(r.at)} · {r.by_name}
                              {r.reason && <> — “{r.reason}”</>}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h4 className="history-subheading">Booking history</h4>
        <EventList events={booking.history} label={`History of booking #${booking.booking_id}`} />
      </div>
    </details>
  )
}

function BookingGroups({ groups, scope }: { groups: VisitBookingSectionGroup[]; scope: string }) {
  return (
    <section className="history-block">
      <h3>Bookings by section</h3>
      {scope === 'OWN_SECTION' && (
        <p className="history-muted">Showing your own section's bookings only.</p>
      )}
      {groups.length === 0 ? (
        <p className="history-muted">No bookings were raised on this visit.</p>
      ) : (
        groups.map((g) => (
          <details className="history-group" key={g.section_id ?? 'unassigned'}>
            <summary>
              <span className="history-group-title">{g.section_name}</span>
              <span className="history-group-count">
                {g.booking_count} booking{g.booking_count === 1 ? '' : 's'}
              </span>
            </summary>
            <div className="history-group-body">
              {g.bookings.map((b) => (
                <BookingItem key={`${g.section_id}-${b.booking_id}`} booking={b} />
              ))}
            </div>
          </details>
        ))
      )}
    </section>
  )
}

// -------------------------------------------------------------------------- checksheets --

export function SignedDocumentButton({ checksheet }: { checksheet: VisitChecksheetHistory }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const url = checksheet.signed_document_url
  if (!url) return null

  async function open() {
    setBusy(true)
    setError(null)
    // Opened synchronously inside the click so a popup blocker lets it through; the document is
    // fetched with the session's Authorization header and handed over as a private blob URL.
    const win = window.open('', '_blank')
    try {
      const blob = await fetchSignedChecksheet(url!)
      const pdf = blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' })
      const objectUrl = URL.createObjectURL(pdf)
      if (win) {
        win.opener = null
        win.location.href = objectUrl
      } else {
        const link = document.createElement('a')
        link.href = objectUrl
        link.download = `checksheet_${checksheet.checksheet_id}_signed.pdf`
        link.click()
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
    } catch (err) {
      win?.close()
      setError(historyErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="history-signed-doc">
      <button type="button" className="btn btn-secondary btn-small" onClick={open} aria-busy={busy} disabled={busy}>
        View signed checksheet
        <span className="visually-hidden"> #{checksheet.checksheet_id}</span>
      </button>
      {error && (
        <span className="history-inline-error" role="alert">
          {error}
        </span>
      )}
    </span>
  )
}

function ChecksheetRow({ item }: { item: VisitChecksheetHistory }) {
  return (
    <tr>
      <td>
        <div>{item.equipment_label ?? item.template_name ?? `Checksheet #${item.checksheet_id}`}</div>
        <div className="history-muted">
          #{item.checksheet_id}
          {item.equipment_label && item.template_name ? ` · ${item.template_name}` : ''}
          {item.maintenance_type ? ` · ${item.maintenance_type}` : ''}
        </div>
        {item.requirement && (
          <div className="history-tags">
            {!item.requirement.is_required && <Tag>Optional</Tag>}
            {!item.requirement.is_active && <Tag>Requirement deactivated</Tag>}
          </div>
        )}
      </td>
      <td><ChecksheetStatusBadge status={item.status} size="small" /></td>
      <td><When at={item.created_at} /><br /><span className="history-muted"><Who name={item.created_by_name} /></span></td>
      <td>
        {item.submitted_at ? (
          <><When at={item.submitted_at} /><br /><span className="history-muted"><Who name={item.submitted_by_name} /></span></>
        ) : '—'}
      </td>
      <td>
        {item.approved_at ? (
          <><When at={item.approved_at} /><br /><span className="history-muted"><Who name={item.approved_by_name} /></span></>
        ) : item.rejected_at ? (
          <>
            Rejected <When at={item.rejected_at} /><br />
            <span className="history-muted"><Who name={item.rejected_by_name} /></span>
            {item.rejection_reason && <><br />“{item.rejection_reason}”</>}
          </>
        ) : '—'}
      </td>
      <td>
        {item.signed ? (
          <>
            Signed <When at={item.signed_at} /><br />
            <span className="history-muted"><Who name={item.signed_by_name} /></span>
            {item.signature_verification && <><br /><Tag>{item.signature_verification}</Tag></>}
            <br />
            {item.signed_document_url ? (
              <SignedDocumentButton checksheet={item} />
            ) : (
              <span className="history-muted">Signed document not available</span>
            )}
          </>
        ) : (
          <span className="history-muted">Not signed</span>
        )}
      </td>
    </tr>
  )
}

function Checksheets({ detail }: { detail: VisitHistoryDetail }) {
  const { checksheets } = detail
  const groups = groupChecksheets(checksheets.items)
  return (
    <section className="history-block">
      <h3>Checksheets</h3>
      {!checksheets.available ? (
        <p className="history-warning" role="status">
          {checksheets.message ?? 'Checksheets are unavailable right now.'}
        </p>
      ) : groups.length === 0 ? (
        <p className="history-muted">No checksheets are recorded for this visit.</p>
      ) : (
        groups.map((g) => (
          <details className="history-group" key={g.key}>
            <summary>
              <span className="history-group-title">{g.label}</span>
              <span className="history-group-count">
                {g.count} checksheet{g.count === 1 ? '' : 's'}
              </span>
            </summary>
            <div className="history-group-body">
              {g.sections.map((s) => (
                <div key={s.sectionName} className="history-section-bucket">
                  <h4 className="history-subheading">{s.sectionName}</h4>
                  <div className="table-scroll">
                    <table className="data-table history-table">
                      <caption className="visually-hidden">{g.label} checksheets of {s.sectionName}</caption>
                      <thead>
                        <tr>
                          <th scope="col">Equipment / checksheet</th>
                          <th scope="col">Status</th>
                          <th scope="col">Created</th>
                          <th scope="col">Submitted</th>
                          <th scope="col">Reviewed</th>
                          <th scope="col">Signature</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.items.map((item) => (
                          <ChecksheetRow key={item.checksheet_id} item={item} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          </details>
        ))
      )}
    </section>
  )
}

// ------------------------------------------------------------------------------- panel --

export function VisitHistoryDetailPanel({ detail }: { detail: VisitHistoryDetail }) {
  const { visit } = detail
  return (
    <div className="history-detail">
      {visit.closure?.kind === 'ADMIN_RESET' && (
        <p className="history-reset-notice" role="note">
          This visit was closed administratively by a system reset
          {visit.closure.at ? ` on ${formatDateTime(visit.closure.at)}` : ''} — it was not a Shed Out.
          {visit.closure.reason ? ` Reason: ${visit.closure.reason}` : ''}
        </p>
      )}
      <Timings detail={detail} />
      <BookingGroups groups={detail.bookings} scope={detail.booking_scope} />
      <Checksheets detail={detail} />
      <section className="history-block">
        <h3>Visit events</h3>
        <EventList events={detail.events} label={`Events of visit #${visit.shed_visit_id}`} />
      </section>
    </div>
  )
}
