import type { EquipmentMapping, EquipmentNode, PathItem, ResolvedSections } from '../../types'

function ancestorName(path: PathItem[], nodeId: number | null): string {
  if (nodeId == null) return `node ${nodeId}`
  return path.find((p) => p.id === nodeId)?.name ?? `node ${nodeId}`
}

export function SelectedNodePanel({
  node,
  path,
  directMapping,
  resolvedSections,
  loading,
  error,
}: {
  node: EquipmentNode | null
  path: PathItem[]
  directMapping: EquipmentMapping | null
  resolvedSections: ResolvedSections | null
  loading: boolean
  error: string | null
}) {
  if (!node) {
    return (
      <div className="panel selected-node-panel selected-node-panel-empty">
        <p>Select equipment from the hierarchy or search above to view and configure its responsible section(s).</p>
      </div>
    )
  }

  return (
    <div className="panel selected-node-panel">
      <div className="selected-node-header">
        <h2>{node.name}</h2>
        <span className="badge badge-neutral">{node.node_type}</span>
      </div>

      {path.length > 0 && (
        <p className="selected-node-path">{path.map((p) => p.name).join(' → ')}</p>
      )}

      <p className="selected-node-id">Node ID: {node.id}</p>

      {loading && <div role="status">Loading mapping…</div>}

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {!loading && !error && directMapping && resolvedSections && (
        <div className="mapping-summary">
          <div className="mapping-block">
            <h3>Direct Mapping</h3>
            {directMapping.section_codes.length === 0 ? (
              <p className="mapping-empty">None</p>
            ) : (
              <ul className="section-badge-list">
                {directMapping.section_codes.map((code) => (
                  <li key={code} className="badge badge-direct">
                    {code}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="mapping-block">
            <h3>Resolved Responsibility</h3>
            {resolvedSections.resolution === 'NONE' ? (
              <p className="mapping-empty">No responsible section configured (direct or inherited).</p>
            ) : (
              <>
                <ul className="section-badge-list">
                  {resolvedSections.section_codes.map((code) => (
                    <li key={code} className="badge badge-resolved">
                      {code}
                    </li>
                  ))}
                </ul>
                {resolvedSections.resolution === 'EXACT' ? (
                  <p className="mapping-note">Exact mapping on this node.</p>
                ) : (
                  <p className="mapping-note">
                    Inherited from{' '}
                    <strong>{ancestorName(path, resolvedSections.resolved_from_node_id)}</strong>.
                    Most-specific mapping wins — this does not combine mappings from multiple
                    ancestor levels.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
