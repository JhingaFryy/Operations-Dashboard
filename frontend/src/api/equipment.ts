import { apiFetch } from './client'
import type {
  EquipmentFamily,
  EquipmentMapping,
  EquipmentNode,
  EquipmentNodeCreated,
  EquipmentNodeMatch,
  EquipmentNodeSearchResult,
  EquipmentSectionAddResult,
  ResolvedSections,
} from '../types'

export function listFamilies(): Promise<EquipmentFamily[]> {
  return apiFetch<EquipmentFamily[]>('/api/equipment/families')
}

/** Which equipment a browse is allowed to see.
 *
 * `{ locoNumber }` is what every BOOKING form uses: the server derives the locomotive's
 * technology family itself and returns nothing from the other technology, at the root and at
 * every deeper level. The browser never names a family, so it cannot ask for the wrong one -
 * sending both is rejected by the API.
 *
 * `{ family }` remains for Equipment Responsibility Mapping, which is administration of the
 * hierarchy itself and legitimately browses one family at a time. */
export type EquipmentScope = { locoNumber: string; family?: never } | { family: string; locoNumber?: never }

function scopeParams(scope: EquipmentScope): URLSearchParams {
  const params = new URLSearchParams()
  if (scope.locoNumber) params.set('loco_number', scope.locoNumber)
  else if (scope.family) params.set('family', scope.family)
  return params
}

export function listNodes(scope: EquipmentScope, parentId?: number | null): Promise<EquipmentNode[]> {
  const params = scopeParams(scope)
  if (parentId != null) params.set('parent_id', String(parentId))
  return apiFetch<EquipmentNode[]>(`/api/equipment/nodes?${params.toString()}`)
}

export function searchNodes(
  query: string,
  scope: EquipmentScope,
): Promise<EquipmentNodeSearchResult[]> {
  const params = scopeParams(scope)
  params.set('q', query)
  return apiFetch<EquipmentNodeSearchResult[]>(`/api/equipment/nodes/search?${params.toString()}`)
}

/** Existing equipment in this scope whose name matches `name` canonically (trimmed,
 * whitespace collapsed, case-insensitive).
 *
 * Returns EVERY match and chooses none: the same name legitimately occurs many times in
 * this hierarchy under different parents, so the administrator picks. */
export function matchNodesByName(
  scope: EquipmentScope,
  name: string,
): Promise<EquipmentNodeMatch[]> {
  const params = scopeParams(scope)
  params.set('name', name)
  return apiFetch<EquipmentNodeMatch[]>(`/api/equipment/nodes/match?${params.toString()}`)
}

/** Create equipment from the Equipment Responsibility Mapping page. ADMIN ONLY.
 *
 * This is the ONLY equipment-creation call in the app. The Shed In booking form used to
 * have its own narrower one; that endpoint was deleted server-side when creating equipment
 * became Admin-only master-data administration. */
export function adminCreateNode(input: {
  familyCode: string
  parentId: number | null
  name: string
  sectionCodes: string[]
}): Promise<EquipmentNodeCreated> {
  return apiFetch<EquipmentNodeCreated>('/api/equipment/admin/nodes', {
    method: 'POST',
    body: JSON.stringify({
      family_code: input.familyCode,
      parent_id: input.parentId,
      name: input.name,
      section_codes: input.sectionCodes,
    }),
  })
}

/** Edit EXISTING equipment. ADMIN ONLY, enforced server-side by require_admin.
 *
 * PARTIAL: only the keys present are sent, and only what is sent is written. A dialog that
 * changes a name must not blank a description it never displayed, so `undefined` means "leave
 * it alone" and an explicit `null` description means "clear it".
 *
 * `sectionCodes`, when given, is REPLACE-SET: it becomes the node's complete mapping and any
 * section not listed is removed. The caller must therefore load the full current mapping first.
 * Omit it to leave the mapping untouched.
 *
 * There is deliberately no parentId or familyCode: moving a node is a different operation with
 * a much larger blast radius, and the server does not accept them either.
 */
export function adminUpdateNode(
  nodeId: number,
  input: {
    name?: string
    description?: string | null
    isActive?: boolean
    sectionCodes?: string[]
  },
): Promise<EquipmentNodeCreated> {
  const body: Record<string, unknown> = {}
  if (input.name !== undefined) body.name = input.name
  // `in` rather than a truthiness or != null check: null is a real value here (clear it), and
  // only key presence distinguishes it from "not mentioned".
  if ('description' in input) body.description = input.description
  if (input.isActive !== undefined) body.is_active = input.isActive
  if (input.sectionCodes !== undefined) body.section_codes = input.sectionCodes

  return apiFetch<EquipmentNodeCreated>(`/api/equipment/admin/nodes/${nodeId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

/** ADD sections to equipment that already exists, without removing any. Idempotent.
 *
 * Not updateMapping() below, which is replace-set and would unmap any section not listed -
 * that one belongs to the mapping editor, which administers the whole set. */
export function addNodeSections(
  nodeId: number,
  sectionCodes: string[],
): Promise<EquipmentSectionAddResult> {
  return apiFetch<EquipmentSectionAddResult>(`/api/equipment/nodes/${nodeId}/sections`, {
    method: 'POST',
    body: JSON.stringify({ section_codes: sectionCodes }),
  })
}

export function getNode(nodeId: number): Promise<EquipmentNode> {
  return apiFetch<EquipmentNode>(`/api/equipment/nodes/${nodeId}`)
}

export function getMapping(nodeId: number): Promise<EquipmentMapping> {
  return apiFetch<EquipmentMapping>(`/api/equipment/nodes/${nodeId}/mapping`)
}

export function getResolvedSections(nodeId: number): Promise<ResolvedSections> {
  return apiFetch<ResolvedSections>(`/api/equipment/nodes/${nodeId}/resolved-sections`)
}

export function updateMapping(nodeId: number, sectionCodes: string[]): Promise<EquipmentMapping> {
  return apiFetch<EquipmentMapping>(`/api/equipment/nodes/${nodeId}/mapping`, {
    method: 'PUT',
    body: JSON.stringify({ section_codes: sectionCodes }),
  })
}
