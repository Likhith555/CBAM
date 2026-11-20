import {
  overrideKey,
  type Allocation,
  type FieldOverride,
  type ManualValue,
  type Workspace,
} from '@/stores/cbam-store'
import type {
  DocumentExtractionResult,
  ProcessDeclaration,
  SourcedValue,
  WorkspaceInputs,
} from '@/lib/cbam-api'

export type StepRoute = '/workspace' | '/upload' | '/allocation'
export type MissingInput = { label: string; step: StepRoute }

/** Register slugs for the fuel types offered on the workspace step. */
export const FUEL_KEYS: Record<string, string> = {
  'Furnace Oil': 'furnace_oil',
  'Diesel (HSD)': 'diesel',
  'Natural Gas': 'natural_gas',
  LPG: 'lpg',
}

export function slug(text: string) {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'precursor'
  )
}

const EMPTY: SourcedValue = { value: null }

type Field = { name: string; value: number; unit: string; ref: string }

function effectiveFields(
  documents: Record<string, DocumentExtractionResult>,
  overrides: Record<string, FieldOverride>
): Field[] {
  return Object.values(documents).flatMap((document) =>
    document.extracted_fields.map((field) => {
      const override =
        overrides[overrideKey(document.document_hash, field.field_name)]
      return {
        name: field.field_name.toLowerCase(),
        value: Number(override?.override_value ?? field.extracted_value),
        unit: override?.override_unit || field.unit,
        ref: `${document.filename} (sha256 ${document.document_hash.slice(0, 12)})`,
      }
    })
  )
}

function fromField(field: Field | undefined): SourcedValue {
  return field
    ? { value: field.value, unit: field.unit, evidence_ref: field.ref }
    : EMPTY
}

function manual(
  values: Record<string, ManualValue>,
  key: string,
  fallback: SourcedValue
): SourcedValue {
  const entry = values[key]
  if (!entry) return fallback
  return {
    value: entry.value,
    unit: entry.unit,
    evidence_ref: entry.evidence_ref,
  }
}

/** Profile items the browser needs before it can talk to the server workspace. */
export function missingProfileItems(workspace: Workspace): MissingInput[] {
  const checks: [boolean, MissingInput][] = [
    [
      !workspace.company_name.trim(),
      { label: 'Company details', step: '/workspace' },
    ],
    [
      !workspace.installation_id.trim(),
      {
        label: 'Installation ID (used as the workspace ID)',
        step: '/workspace',
      },
    ],
    [
      !workspace.cn_code.trim(),
      { label: 'Product CN code', step: '/workspace' },
    ],
    [
      !workspace.fuel_name,
      { label: 'Fuel type recorded on the bill', step: '/workspace' },
    ],
    [
      !workspace.precursor_material_name,
      { label: 'Precursor material name', step: '/workspace' },
    ],
  ]
  return checks.filter(([missing]) => missing).map(([, item]) => item)
}

/**
 * Builds the server-side workspace inputs. Missing values are sent as null so
 * the server's data-gap register can list them; nothing is defaulted here.
 */
export function buildWorkspaceInputs(
  workspace: Workspace,
  allocation: Allocation,
  documents: Record<string, DocumentExtractionResult>,
  overrides: Record<string, FieldOverride>,
  process: ProcessDeclaration,
  manualValues: Record<string, ManualValue>
): WorkspaceInputs {
  const fields = effectiveFields(documents, overrides)
  const first = (predicate: (name: string) => boolean) =>
    fields.find(
      (field) => predicate(field.name) && Number.isFinite(field.value)
    )
  const electricity = first((name) => name.includes('electricity'))
  const fuel = first(
    (name) =>
      name.includes('fuel') ||
      name.includes('furnace_oil') ||
      name.includes('diesel') ||
      name.includes('gas_quantity')
  )
  const precursor = first(
    (name) =>
      (name.includes('precursor') || name.includes('wire_rod')) &&
      name.includes('quantity')
  )
  const precursorSee = first(
    (name) =>
      (name.includes('precursor') || name.includes('wire_rod')) &&
      (name.includes('see') ||
        name.includes('emission') ||
        name.includes('factor'))
  )
  const target = allocation.target_production_tonnes
  const total = allocation.total_facility_production_tonnes
  const dedicated =
    allocation.fuel_is_shared === false &&
    allocation.electricity_is_shared === false
  const fuelKey =
    FUEL_KEYS[workspace.fuel_name] ?? slug(workspace.fuel_name || 'fuel')
  const precursorKey = slug(workspace.precursor_material_name || 'precursor')

  return {
    cn_code: workspace.cn_code || null,
    includes_indirect: true,
    production: {
      product_family: workspace.target_product_family || null,
      target:
        target != null && target > 0 ? { value: target, unit: 't' } : EMPTY,
      total_installation:
        total != null && total > 0 ? { value: total, unit: 't' } : EMPTY,
      utility_coverage: dedicated ? 'dedicated' : 'shared',
    },
    uses_fuel: true,
    fuels: workspace.fuel_name
      ? [
          {
            fuel_key: fuelKey,
            name: workspace.fuel_name,
            quantity: manual(
              manualValues,
              `fuel.${fuelKey}.quantity`,
              fromField(fuel)
            ),
            ncv: manual(manualValues, `fuel.${fuelKey}.ncv`, EMPTY),
            ef: manual(manualValues, `fuel.${fuelKey}.ef`, EMPTY),
            oxidation_factor: manual(manualValues, `fuel.${fuelKey}.of`, EMPTY),
            is_shared: allocation.fuel_is_shared !== false,
          },
        ]
      : [],
    electricity: {
      consumed: manual(
        manualValues,
        'electricity.consumed',
        fromField(electricity)
      ),
      grid_ef: manual(manualValues, 'electricity.grid_ef', EMPTY),
      is_shared: allocation.electricity_is_shared !== false,
    },
    precursors: workspace.precursor_material_name
      ? [
          {
            key: precursorKey,
            name: workspace.precursor_material_name,
            cn_code: workspace.precursor_cn_code || null,
            country_of_origin: workspace.precursor_country || null,
            quantity: manual(
              manualValues,
              `precursor.${precursorKey}.quantity`,
              fromField(precursor)
            ),
            supplier_see: manual(
              manualValues,
              `precursor.${precursorKey}.supplier_see`,
              fromField(precursorSee)
            ),
            directly_attributable: true,
          },
        ]
      : [],
    process,
  }
}

/** Which step fixes a gap that has no fallback. */
export function stepForGap(inputKey: string): StepRoute {
  if (inputKey.startsWith('production.') || inputKey.startsWith('process.'))
    return '/allocation'
  if (inputKey.endsWith('.records')) return '/workspace'
  return '/upload'
}

/** Gaps a user may fill by typing a documented value (lab NCV, supplier factor letter, ...). */
export function isManualValueGap(inputKey: string) {
  return (
    /^fuel\.[a-z_]+\.(ncv|ef|of)$/.test(inputKey) ||
    inputKey === 'electricity.grid_ef' ||
    inputKey.endsWith('.supplier_see')
  )
}

export function defaultUnitFor(inputKey: string, fuelUnit = 'GJ/L') {
  if (inputKey.endsWith('.ncv')) return fuelUnit
  if (inputKey.endsWith('.ef')) return 'tCO2/GJ'
  if (inputKey.endsWith('.of')) return 'fraction'
  if (inputKey === 'electricity.grid_ef') return 'tCO2/MWh'
  if (inputKey.endsWith('.supplier_see')) return 'tCO2/t'
  return ''
}

export function statusVariant(status: string) {
  return status === 'Draft' ? ('human' as const) : ('primary' as const)
}
