import { useEffect, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  AlertOctagon,
  ArrowRight,
  Circle,
  ClipboardList,
  Loader2,
} from 'lucide-react'
import { toast } from 'sonner'
import { useCbamStore } from '@/stores/cbam-store'
import {
  applyFallback,
  getRegisterFactors,
  type FactorRead,
  type Gap,
} from '@/lib/cbam-api'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  defaultUnitFor,
  isManualValueGap,
  stepForGap,
} from '../lib/workspace-inputs'
import { StatusBadge } from './status-badge'

const STEP_LABEL = {
  '/workspace': 'Workspace',
  '/upload': 'Upload',
  '/allocation': 'Allocation',
} as const

function fallbackKey(inputKey: string) {
  return inputKey.endsWith('.supplier_see')
    ? inputKey.replace(/\.supplier_see$/, '.default_see')
    : inputKey
}

type Props = {
  workspaceId: string
  gaps: Gap[]
  incomplete: boolean
  factorSetVersion: string | null
  onChanged: () => void
}

/**
 * Missing-inputs checklist driven by the server's data-gap register.
 * Fallbacks are never applied silently: each needs an explicit click and a reason.
 */
export function GapChecklist({
  workspaceId,
  gaps,
  incomplete,
  factorSetVersion,
  onChanged,
}: Props) {
  const actor = useCbamStore((s) => s.actingAs.name)
  const setManualValue = useCbamStore((s) => s.setManualValue)
  const [factors, setFactors] = useState<Record<string, FactorRead>>({})
  const [fallbackFor, setFallbackFor] = useState<Gap | null>(null)
  const [valueFor, setValueFor] = useState<Gap | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [manual, setManual] = useState({ value: '', unit: '', evidence: '' })

  useEffect(() => {
    getRegisterFactors(factorSetVersion ?? undefined)
      .then((rows) =>
        setFactors(Object.fromEntries(rows.map((row) => [row.key, row])))
      )
      .catch(() => setFactors({}))
  }, [factorSetVersion])

  const open = gaps.filter((gap) => gap.status === 'open')
  if (open.length === 0) return null

  const submitFallback = async () => {
    if (!fallbackFor) return
    if (!actor.trim())
      return toast.error('Enter your name in "Acting as" first.')
    if (!reason.trim())
      return toast.error('Explain why measured data is unavailable.')
    setBusy(true)
    try {
      await applyFallback(
        workspaceId,
        fallbackFor.input_key,
        reason.trim(),
        actor.trim()
      )
      toast.success(
        `Fallback applied for ${fallbackFor.input_key}. Logged as an assumption.`
      )
      setFallbackFor(null)
      setReason('')
      onChanged()
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : 'Could not apply the fallback.'
      )
    } finally {
      setBusy(false)
    }
  }

  const submitValue = () => {
    if (!valueFor) return
    const numeric = Number(manual.value)
    if (!Number.isFinite(numeric) || manual.value === '')
      return toast.error('Enter a number.')
    if (!manual.evidence.trim())
      return toast.error(
        'Add the evidence reference (certificate, letter, page).'
      )
    setManualValue(valueFor.input_key, {
      value: numeric,
      unit: manual.unit || defaultUnitFor(valueFor.input_key),
      evidence_ref: manual.evidence.trim(),
    })
    toast.success('Value recorded. The gap list updates automatically.')
    setValueFor(null)
  }

  const fb = fallbackFor
    ? factors[fallbackKey(fallbackFor.input_key)]
    : undefined

  return (
    <Card
      className={
        incomplete
          ? 'border-red-300 bg-red-50/60 dark:border-red-500/40 dark:bg-red-500/5'
          : 'border-amber-300/60 bg-amber-50/60 dark:border-amber-500/30 dark:bg-amber-500/5'
      }
    >
      <CardHeader>
        <CardTitle className='flex items-center gap-2'>
          {incomplete ? (
            <AlertOctagon className='size-5 text-red-600' />
          ) : (
            <ClipboardList className='size-5 text-amber-600' />
          )}
          {incomplete
            ? 'Missing inputs'
            : 'Fallbacks waiting for your decision'}
        </CardTitle>
        <CardDescription>
          {incomplete
            ? 'These inputs have no approved fallback. No SEE value is produced until they are provided.'
            : 'These inputs are missing but the factor register holds an approved fallback. Apply it explicitly, with a reason, or enter a documented value.'}
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-2'>
        {open.map((gap) => {
          const step = stepForGap(gap.input_key)
          const register = factors[fallbackKey(gap.input_key)]
          return (
            <div
              key={gap.input_key}
              className='flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3'
            >
              <div className='flex min-w-0 items-start gap-2'>
                <Circle
                  className={`mt-1 size-2.5 shrink-0 ${gap.fallback_available ? 'text-amber-500' : 'text-red-500'}`}
                />
                <div className='min-w-0'>
                  <p className='text-sm font-medium'>{gap.description}</p>
                  <p className='font-mono text-xs text-muted-foreground'>
                    {gap.input_key}
                  </p>
                  {gap.fallback_available && register && (
                    <p className='text-xs text-muted-foreground'>
                      Register fallback: {register.value} {register.unit} ·{' '}
                      {register.publisher} ({register.year}) · set{' '}
                      {register.factor_set_version}
                    </p>
                  )}
                  {gap.fallback_allowed_flag === false && (
                    <p className='text-xs text-red-700 dark:text-red-300'>
                      A register default exists but is not approved for use
                      (allowed = false).
                    </p>
                  )}
                </div>
              </div>
              <div className='flex flex-wrap items-center gap-2'>
                <StatusBadge
                  label={
                    gap.fallback_available
                      ? 'Fallback available'
                      : 'No fallback'
                  }
                  variant={gap.fallback_available ? 'human' : 'fallback'}
                />
                {gap.fallback_available && (
                  <Button size='sm' onClick={() => setFallbackFor(gap)}>
                    Apply fallback
                  </Button>
                )}
                {isManualValueGap(gap.input_key) ? (
                  <Button
                    size='sm'
                    variant='outline'
                    onClick={() => {
                      setManual({
                        value: '',
                        unit: defaultUnitFor(gap.input_key),
                        evidence: '',
                      })
                      setValueFor(gap)
                    }}
                  >
                    Enter value
                  </Button>
                ) : (
                  <Button asChild size='sm' variant='outline'>
                    <Link to={step}>
                      {STEP_LABEL[step]} <ArrowRight className='size-3.5' />
                    </Link>
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </CardContent>

      <Dialog
        open={fallbackFor !== null}
        onOpenChange={(next) => !next && setFallbackFor(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply fallback</DialogTitle>
            <DialogDescription>
              {fallbackFor?.description}. The value is logged as an assumption
              with its source, and the uncertainty band widens to the
              register&apos;s distribution.
            </DialogDescription>
          </DialogHeader>
          {fb && (
            <div className='rounded-md border bg-muted/40 p-3 text-sm'>
              <p className='font-semibold'>
                {fb.value} {fb.unit}
              </p>
              <p className='text-xs text-muted-foreground'>{fb.source_title}</p>
              <p className='text-xs text-muted-foreground'>
                {fb.publisher} · {fb.year} · factor set {fb.factor_set_version}{' '}
                ·{' '}
                {fb.uncertainty_type === 'triangular'
                  ? `triangular ${fb.uncertainty_low}–${fb.uncertainty_high}`
                  : `±${((fb.uncertainty_rel ?? 0) * 100).toFixed(0)}%`}
              </p>
            </div>
          )}
          <div className='space-y-2'>
            <Label htmlFor='fallback-reason'>
              Why is measured data unavailable?
            </Label>
            <Textarea
              id='fallback-reason'
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder='e.g. Supplier has not issued a fuel analysis for this period'
            />
            <p className='text-xs text-muted-foreground'>
              Applied by: {actor || '— set "Acting as" first'}
            </p>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setFallbackFor(null)}>
              Cancel
            </Button>
            <Button onClick={() => void submitFallback()} disabled={busy}>
              {busy && <Loader2 className='size-4 animate-spin' />}
              Apply fallback
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={valueFor !== null}
        onOpenChange={(next) => !next && setValueFor(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enter a documented value</DialogTitle>
            <DialogDescription>{valueFor?.description}</DialogDescription>
          </DialogHeader>
          <div className='grid grid-cols-2 gap-3'>
            <div className='space-y-2'>
              <Label htmlFor='manual-value'>Value</Label>
              <Input
                id='manual-value'
                type='number'
                step='any'
                value={manual.value}
                onChange={(event) =>
                  setManual((m) => ({ ...m, value: event.target.value }))
                }
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='manual-unit'>Unit</Label>
              <Input
                id='manual-unit'
                value={manual.unit}
                onChange={(event) =>
                  setManual((m) => ({ ...m, unit: event.target.value }))
                }
              />
            </div>
          </div>
          <div className='space-y-2'>
            <Label htmlFor='manual-evidence'>Evidence reference</Label>
            <Input
              id='manual-evidence'
              value={manual.evidence}
              placeholder='e.g. Lab certificate LC-2026-114, page 2'
              onChange={(event) =>
                setManual((m) => ({ ...m, evidence: event.target.value }))
              }
            />
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setValueFor(null)}>
              Cancel
            </Button>
            <Button onClick={submitValue}>Save value</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
