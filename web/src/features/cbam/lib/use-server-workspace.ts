import { useCallback, useEffect, useMemo, useState } from 'react'
import { useCbamStore } from '@/stores/cbam-store'
import { upsertWorkspace, type WorkspaceState } from '@/lib/cbam-api'
import { buildWorkspaceInputs, missingProfileItems } from './workspace-inputs'

/**
 * Keeps the server-side workspace in step with the browser state. Every time the
 * effective inputs change they are PUT to the backend, which re-runs the data-gap
 * register and the passport input-hash check, and returns the new state. This is
 * how a filled gap updates the status without a page reload.
 */
export function useServerWorkspace() {
  const workspace = useCbamStore((s) => s.workspace)
  const allocation = useCbamStore((s) => s.allocation)
  const documents = useCbamStore((s) => s.documents)
  const overrides = useCbamStore((s) => s.overrides)
  const process = useCbamStore((s) => s.process)
  const manualValues = useCbamStore((s) => s.manualValues)
  const actor = useCbamStore((s) => s.actingAs.name)

  const workspaceId = workspace.installation_id.trim()
  const profileMissing = useMemo(
    () => missingProfileItems(workspace),
    [workspace]
  )
  const inputs = useMemo(
    () =>
      buildWorkspaceInputs(
        workspace,
        allocation,
        documents,
        overrides,
        process,
        manualValues
      ),
    [workspace, allocation, documents, overrides, process, manualValues]
  )
  const payloadKey = JSON.stringify([
    inputs,
    workspace.company_name,
    workspace.installation_name,
    workspace.reporting_start ?? null,
    workspace.reporting_end ?? null,
  ])

  const [state, setState] = useState<WorkspaceState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)

  const sync = useCallback(async () => {
    if (!workspaceId || profileMissing.length > 0) return null
    setSyncing(true)
    try {
      const next = await upsertWorkspace(workspaceId, {
        company_name: workspace.company_name,
        installation_name: workspace.installation_name,
        cn_code: workspace.cn_code,
        reporting_start: workspace.reporting_start || null,
        reporting_end: workspace.reporting_end || null,
        inputs,
        actor: actor.trim() || 'system',
      })
      setState(next)
      setError(null)
      return next
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not reach the backend.'
      )
      return null
    } finally {
      setSyncing(false)
    }
    // payloadKey captures every value sent above
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadKey, workspaceId, profileMissing.length])

  // Debounced so typing in a field does not send a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => void sync(), 300)
    return () => clearTimeout(timer)
  }, [sync])

  return {
    workspaceId,
    inputs,
    state,
    setState,
    sync,
    syncing,
    error,
    profileMissing,
  }
}
