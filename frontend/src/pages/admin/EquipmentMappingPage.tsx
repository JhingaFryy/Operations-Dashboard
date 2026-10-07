import { useCallback, useEffect, useState } from 'react'
import { listFamilies, getMapping, getResolvedSections, updateMapping } from '../../api/equipment'
import { listSections } from '../../api/sections'
import { AdminEditEquipmentDialog } from '../../components/equipment/AdminEditEquipmentDialog'
import { friendlyErrorMessage } from '../../api/client'
import { FamilySelect } from '../../components/equipment/FamilySelect'
import { HierarchyBrowser } from '../../components/equipment/HierarchyBrowser'
import { EquipmentSearchBox } from '../../components/equipment/EquipmentSearchBox'
import { SelectedNodePanel } from '../../components/equipment/SelectedNodePanel'
import { SectionMappingEditor } from '../../components/equipment/SectionMappingEditor'
import { EmptyState } from '../../components/ui/States'
import { PageHeader } from '../../components/ui/PageHeader'
import {
  AdminAddEquipmentDialog,
  type AdminAddEquipmentResult,
} from '../../components/equipment/AdminAddEquipmentDialog'
import { isAdmin } from '../../auth/permissions'
import { useAuth } from '../../auth/useAuth'
import type {
  EquipmentFamily,
  EquipmentMapping,
  EquipmentNode,
  EquipmentNodeSearchResult,
  PathItem,
  ResolvedSections,
  Section,
} from '../../types'

export function EquipmentMappingPage() {
  const { user } = useAuth()
  // The page itself is reachable with can_manage_equipment_mapping (Admin, or a
  // Supervisor explicitly granted it). CREATING equipment is narrower - Admin only -
  // because it adds master data rather than editing who maintains what. Hiding the
  // button is usability; POST /api/equipment/admin/nodes refuses everyone else.
  const mayCreateEquipment = isAdmin(user)

  const [families, setFamilies] = useState<EquipmentFamily[]>([])
  const [familiesLoading, setFamiliesLoading] = useState(true)
  const [familiesError, setFamiliesError] = useState<string | null>(null)
  const [familyCode, setFamilyCode] = useState<string | null>(null)

  const [sections, setSections] = useState<Section[]>([])
  const [sectionsLoading, setSectionsLoading] = useState(true)

  const [selectedNode, setSelectedNode] = useState<EquipmentNode | null>(null)
  const [selectedPath, setSelectedPath] = useState<PathItem[]>([])
  const [editOpen, setEditOpen] = useState(false)

  const [directMapping, setDirectMapping] = useState<EquipmentMapping | null>(null)
  const [resolvedSections, setResolvedSections] = useState<ResolvedSections | null>(null)
  const [mappingLoading, setMappingLoading] = useState(false)
  const [mappingError, setMappingError] = useState<string | null>(null)

  const [addOpen, setAddOpen] = useState(false)
  /** Bumped after a create so the hierarchy refetches and shows the new item. */
  const [hierarchyVersion, setHierarchyVersion] = useState(0)

  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(
    null,
  )

  useEffect(() => {
    listFamilies()
      .then(setFamilies)
      .catch((err) => setFamiliesError(friendlyErrorMessage(err)))
      .finally(() => setFamiliesLoading(false))
  }, [])

  useEffect(() => {
    listSections()
      .then(setSections)
      .catch(() => setSections([]))
      .finally(() => setSectionsLoading(false))
  }, [])

  const loadMapping = useCallback((nodeId: number) => {
    setMappingLoading(true)
    setMappingError(null)
    Promise.all([getMapping(nodeId), getResolvedSections(nodeId)])
      .then(([mapping, resolved]) => {
        setDirectMapping(mapping)
        setResolvedSections(resolved)
      })
      .catch((err) => setMappingError(friendlyErrorMessage(err)))
      .finally(() => setMappingLoading(false))
  }, [])

  function handleFamilyChange(code: string | null) {
    setFamilyCode(code)
    setSelectedNode(null)
    setSelectedPath([])
    setDirectMapping(null)
    setResolvedSections(null)
    setSaveMessage(null)
  }

  function handleHierarchySelect(node: EquipmentNode, chain: EquipmentNode[]) {
    setSelectedNode(node)
    setSelectedPath(chain.map((n) => ({ id: n.id, name: n.name })))
    setSaveMessage(null)
    loadMapping(node.id)
  }

  function handleSearchSelect(result: EquipmentNodeSearchResult) {
    const { path, ...node } = result
    setSelectedNode(node)
    setSelectedPath(path)
    setSaveMessage(null)
    loadMapping(node.id)
  }

  function handleEquipmentAdded(result: AdminAddEquipmentResult) {
    setAddOpen(false)
    // Show the administrator what they just did, on the same page: select the item,
    // load its mapping, and refetch the hierarchy so it appears in the tree too.
    setSelectedNode(result.node)
    setSelectedPath(result.path)
    setHierarchyVersion((v) => v + 1)
    loadMapping(result.node.id)
    setSaveMessage({
      type: 'success',
      text: result.reused
        ? `"${result.node.name}" already existed. It is now mapped to ${result.sectionCodes.join(', ')}.`
        : `"${result.node.name}" was added${
            result.sectionCodes.length > 0
              ? ` and mapped to ${result.sectionCodes.join(', ')}`
              : ', inheriting its parent’s sections'
          }.`,
    })
  }

  function handleSave(codes: string[]) {
    if (!selectedNode) return
    setSaving(true)
    setSaveMessage(null)
    updateMapping(selectedNode.id, codes)
      .then(() => {
        setSaveMessage({ type: 'success', text: 'Mapping saved.' })
        loadMapping(selectedNode.id)
      })
      .catch((err) => setSaveMessage({ type: 'error', text: friendlyErrorMessage(err) }))
      .finally(() => setSaving(false))
  }

  function handleClear() {
    if (!selectedNode) return
    setSaving(true)
    setSaveMessage(null)
    updateMapping(selectedNode.id, [])
      .then(() => {
        setSaveMessage({ type: 'success', text: 'Direct mapping cleared.' })
        loadMapping(selectedNode.id)
      })
      .catch((err) => setSaveMessage({ type: 'error', text: friendlyErrorMessage(err) }))
      .finally(() => setSaving(false))
  }

  return (
    <div className="equipment-mapping-page">
      <PageHeader
        title="Equipment Responsibility Mapping"
        description={
          <>
            Defines which section(s) are responsible for maintaining equipment master/inventory
            information (make, model, serial number, commissioning date, and future asset/failure
            history). This mapping does not control booking visibility — every booking is visible to
            every authorized section in the <a href="/booking-pool">Booking Pool</a>, grouped by
            equipment, regardless of this mapping.
          </>
        }
      />

      {mayCreateEquipment && (
        <div className="equipment-mapping-actions">
          <button type="button" className="btn btn-primary" onClick={() => setAddOpen(true)}>
            + Add Equipment
          </button>
        </div>
      )}

      {familiesError && (
        <div className="form-error" role="alert">
          {familiesError}
        </div>
      )}

      {/* Desktop split pane: browse/search on the left, the selected node's
          details, resolved responsibility and mapping controls on the right.
          Pure layout — the same components, the same state, the same
          requests and the same mapping semantics as before. */}
      {saveMessage && (
        <div
          className={saveMessage.type === 'success' ? 'form-success' : 'form-error'}
          role={saveMessage.type === 'success' ? 'status' : 'alert'}
        >
          {saveMessage.text}
        </div>
      )}

      <div className="equipment-mapping-layout">
        <div className="equipment-mapping-browse">
          <div className="panel equipment-picker">
            <h2 className="section-title">Browse equipment</h2>

            <FamilySelect
              families={families}
              value={familyCode}
              onChange={handleFamilyChange}
              loading={familiesLoading}
            />

            {familyCode && (
              <EquipmentSearchBox scope={{ family: familyCode }} onSelectResult={handleSearchSelect} />
            )}

            {familyCode ? (
              <HierarchyBrowser
                key={`${familyCode}-${hierarchyVersion}`}
                scope={{ family: familyCode }}
                onSelectNode={handleHierarchySelect}
              />
            ) : (
              !familiesLoading && (
                <p className="field-hint">
                  Choose a locomotive family to browse or search its equipment.
                </p>
              )
            )}
          </div>
        </div>

        <div className="equipment-mapping-detail">
          {!selectedNode ? (
            /* Not a blank panel: say what the administrator does next, and
               which step they are on. */
            <EmptyState
              title={
                familyCode
                  ? 'Select a piece of equipment'
                  : 'Start by choosing a locomotive family'
              }
              description={
                familyCode
                  ? 'Pick an item from the hierarchy on the left, or search for it by name. Its current direct mapping, resolved responsibility and the section editor will appear here.'
                  : 'Choose a family on the left, then browse or search its equipment. Selecting an item shows its responsible section(s) here and lets you change them.'
              }
            />
          ) : (
            <>
              <SelectedNodePanel
                node={selectedNode}
                path={selectedPath}
                directMapping={directMapping}
                resolvedSections={resolvedSections}
                loading={mappingLoading}
                error={mappingError}
              />

              {/* Editing equipment master data is ADMIN-ONLY, enforced server-side by
                  require_admin on PATCH /api/equipment/admin/nodes/{id}. This button follows the
                  same permission the Add button does; hiding it is a usability decision, not
                  the boundary. */}
              {mayCreateEquipment && (
                <div className="equipment-mapping-actions">
                  <button type="button" className="btn" onClick={() => setEditOpen(true)}>
                    Edit Equipment
                  </button>
                </div>
              )}

              {!mappingLoading && !mappingError && directMapping && (
                <>
                  <SectionMappingEditor
                    nodeId={selectedNode.id}
                    sections={sections}
                    sectionsLoading={sectionsLoading}
                    selectedCodes={directMapping.section_codes}
                    hasDirectMapping={directMapping.section_codes.length > 0}
                    saving={saving}
                    onSave={handleSave}
                    onClear={handleClear}
                  />
                </>
              )}
            </>
          )}
        </div>
      </div>

      {editOpen && selectedNode && (
        <AdminEditEquipmentDialog
          node={selectedNode}
          path={selectedPath}
          familyCode={familyCode}
          sections={sections}
          sectionsLoading={sectionsLoading}
          onDone={(result) => {
            setEditOpen(false)
            // Keep the node selected and refresh what the page shows about it - a rename must
            // not deselect the thing the administrator was working on.
            setSelectedNode(result.node)
            loadMapping(result.node.id)
            setHierarchyVersion((v) => v + 1)
            setSaveMessage({
              type: 'success',
              text: `"${result.node.name}" was updated.`,
            })
          }}
          onCancel={() => setEditOpen(false)}
        />
      )}

      {addOpen && (
        <AdminAddEquipmentDialog
          families={families}
          initialFamilyCode={familyCode}
          parent={selectedNode ? { node: selectedNode, path: selectedPath } : null}
          onDone={handleEquipmentAdded}
          onCancel={() => setAddOpen(false)}
        />
      )}
    </div>
  )
}
