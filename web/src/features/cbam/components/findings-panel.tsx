import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'
import { useCbamStore } from '@/stores/cbam-store'
import { resolveFinding, waiveFinding, type Finding } from '@/lib/cbam-api'
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
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { StatusBadge } from './status-badge'

const RULE_LABEL: Record<Finding['rule_id'], string> = {
  V1_UNIT: 'Unit',
  V2_OVERLAP: 'Billing period',
  V3_DUPLICATE: 'Duplicate',
}

type Action =
  | { kind: 'waive'; finding: Finding }
  | {
      kind: 'resolve'
      finding: Finding
      action: 'resolve' | 'keep_this' | 'keep_other' | 'distinct'
    }

export function FindingsPanel({
  findings,
  loading,
  onValidate,
  onChanged,
  documentNames,
}: {
  findings: Finding[]
  loading: boolean
  onValidate: () => void
  onChanged: () => void
  documentNames: Record<number, string>
}) {
  const actor = useCbamStore((s) => s.actingAs.name)
  const [pending, setPending] = useState<Action | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [showClosed, setShowClosed] = useState(false)

  const open = findings.filter((f) => f.status === 'open')
  const errors = open.filter((f) => f.severity === 'error')
  const closed = findings.filter((f) => f.status !== 'open')

  const submit = async () => {
    if (!pending) return
    if (!actor.trim())
      return toast.error('Enter your name in "Acting as" first.')
    if (pending.kind === 'waive' && !text.trim())
      return toast.error('A justification is required to waive.')
    setBusy(true)
    try {
      if (pending.kind === 'waive')
        await waiveFinding(pending.finding.id, text.trim(), actor.trim())
      else
        await resolveFinding(
          pending.finding.id,
          pending.action,
          actor.trim(),
          text.trim()
        )
      toast.success(
        pending.kind === 'waive'
          ? 'Finding waived and logged.'
          : 'Finding resolved and logged.'
      )
      setPending(null)
      setText('')
      onChanged()
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : 'Action failed.')
    } finally {
      setBusy(false)
    }
  }

  const row = (finding: Finding) => (
    <div key={finding.id} className='space-y-2 rounded-lg border bg-card p-3'>
      <div className='flex flex-wrap items-center gap-2'>
        <StatusBadge
          label={finding.severity}
          variant={finding.severity === 'error' ? 'fallback' : 'human'}
        />
        <StatusBadge label={RULE_LABEL[finding.rule_id]} variant='neutral' />
        {finding.status !== 'open' && (
          <StatusBadge label={finding.status} variant='primary' />
        )}
        {finding.document_id != null && documentNames[finding.document_id] && (
          <span className='text-xs text-muted-foreground'>
            {documentNames[finding.document_id]}
          </span>
        )}
      </div>
      <p className='text-sm'>{finding.message}</p>
      {finding.suggested_fix && (
        <p className='text-xs text-muted-foreground'>
          Suggested fix: {finding.suggested_fix}
        </p>
      )}
      {finding.status !== 'open' && finding.resolution_note && (
        <p className='text-xs text-muted-foreground'>
          {finding.resolved_by}: {finding.resolution_note}
        </p>
      )}
      {finding.status === 'open' && (
        <div className='flex flex-wrap gap-2'>
          {finding.rule_id === 'V3_DUPLICATE' ? (
            <>
              <Button
                size='sm'
                variant='outline'
                onClick={() =>
                  setPending({ kind: 'resolve', finding, action: 'keep_this' })
                }
              >
                Keep this, exclude{' '}
                {documentNames[finding.related_document_id ?? -1] ??
                  'the other'}
              </Button>
              <Button
                size='sm'
                variant='outline'
                onClick={() =>
                  setPending({ kind: 'resolve', finding, action: 'keep_other' })
                }
              >
                Keep{' '}
                {documentNames[finding.related_document_id ?? -1] ??
                  'the other'}
              </Button>
              <Button
                size='sm'
                variant='outline'
                onClick={() =>
                  setPending({ kind: 'resolve', finding, action: 'distinct' })
                }
              >
                Both are distinct
              </Button>
            </>
          ) : (
            <Button
              size='sm'
              variant='outline'
              onClick={() =>
                setPending({ kind: 'resolve', finding, action: 'resolve' })
              }
            >
              Mark resolved
            </Button>
          )}
          <Button
            size='sm'
            variant='ghost'
            onClick={() => setPending({ kind: 'waive', finding })}
          >
            Waive…
          </Button>
        </div>
      )}
    </div>
  )

  return (
    <Card className='mb-4'>
      <CardHeader className='flex flex-row flex-wrap items-start justify-between gap-3'>
        <div className='space-y-1'>
          <CardTitle className='flex items-center gap-2'>
            {errors.length > 0 ? (
              <ShieldAlert className='size-5 text-red-600' />
            ) : (
              <CheckCircle2 className='size-5 text-emerald-600' />
            )}
            Validation findings
          </CardTitle>
          <CardDescription>
            Unit consistency, billing-period overlaps and duplicate documents.
            Open errors block field approval and confirmation.
          </CardDescription>
        </div>
        <div className='flex items-center gap-2'>
          <StatusBadge
            label={`${errors.length} open error${errors.length === 1 ? '' : 's'}`}
            variant={errors.length ? 'fallback' : 'primary'}
          />
          <StatusBadge
            label={`${open.length - errors.length} warning(s)`}
            variant='neutral'
          />
          <Button
            size='sm'
            variant='outline'
            onClick={onValidate}
            disabled={loading}
          >
            {loading && <Loader2 className='size-4 animate-spin' />}
            Run checks
          </Button>
        </div>
      </CardHeader>
      <CardContent className='space-y-2'>
        {open.length === 0 ? (
          <p className='flex items-center gap-2 text-sm text-muted-foreground'>
            <CheckCircle2 className='size-4 text-emerald-600' /> No open
            findings.
          </p>
        ) : (
          open.map(row)
        )}
        {closed.length > 0 && (
          <Button
            size='sm'
            variant='link'
            className='px-0'
            onClick={() => setShowClosed((v) => !v)}
          >
            {showClosed ? 'Hide' : 'Show'} {closed.length} resolved / waived
          </Button>
        )}
        {showClosed && closed.map(row)}
      </CardContent>

      <Dialog
        open={pending !== null}
        onOpenChange={(next) => !next && setPending(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              <AlertTriangle className='size-4' />
              {pending?.kind === 'waive' ? 'Waive finding' : 'Resolve finding'}
            </DialogTitle>
            <DialogDescription>{pending?.finding.message}</DialogDescription>
          </DialogHeader>
          <div className='space-y-2'>
            <Label htmlFor='finding-text'>
              {pending?.kind === 'waive' ? 'Justification (required)' : 'Note'}
            </Label>
            <Textarea
              id='finding-text'
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
            <p className='text-xs text-muted-foreground'>
              Recorded as: {actor || '— set "Acting as" first'}
            </p>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} disabled={busy}>
              {busy && <Loader2 className='size-4 animate-spin' />}
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
