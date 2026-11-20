import { useState } from 'react'
import { ArrowRight, BadgeCheck, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { useCbamStore } from '@/stores/cbam-store'
import {
  ApiError,
  transitionPassport,
  type HistoryRow,
  type PassportState,
  type ReviewStatusName,
} from '@/lib/cbam-api'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { statusVariant } from '../lib/workspace-inputs'
import { ActingAs } from './acting-as'
import { StatusBadge } from './status-badge'

const FLOW: ReviewStatusName[] = [
  'Draft',
  'User-Confirmed',
  'Expert-Reviewed',
  'Externally Verified',
]

const FIELDS: Partial<
  Record<ReviewStatusName, { key: string; label: string; type?: string }[]>
> = {
  'Expert-Reviewed': [
    { key: 'reviewer_name', label: 'Reviewer name' },
    { key: 'organisation', label: 'Organisation' },
  ],
  'Externally Verified': [
    { key: 'verifier_body', label: 'Verifier body name' },
    { key: 'accreditation_ref', label: 'Accreditation / reference number' },
    { key: 'statement_ref', label: 'Verification statement reference' },
    { key: 'date', label: 'Statement date', type: 'date' },
  ],
}

export function StatusWorkflow({
  workspaceId,
  passport,
  history,
  onChanged,
}: {
  workspaceId: string
  passport: PassportState
  history: HistoryRow[]
  onChanged: () => void
}) {
  const actingAs = useCbamStore((s) => s.actingAs)
  const setReviewStatus = useCbamStore((s) => s.setReviewStatus)
  const next = passport.allowed_next[0]
  const requiredRole = next ? passport.required_role[next] : undefined
  const [note, setNote] = useState('')
  const [declaration, setDeclaration] = useState(false)
  const [extra, setExtra] = useState<Record<string, string>>({})
  const [problems, setProblems] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!next) return
    if (!actingAs.name.trim())
      return toast.error('Enter your name in "Acting as".')
    setBusy(true)
    setProblems([])
    try {
      const fields: Record<string, unknown> = { ...extra }
      if (next === 'User-Confirmed') fields.declaration = declaration
      await transitionPassport(workspaceId, {
        to: next,
        note,
        actor: actingAs.name.trim(),
        role: actingAs.role,
        extra_fields: fields,
      })
      setReviewStatus(next)
      toast.success(`Passport moved to ${next}.`)
      setNote('')
      setExtra({})
      setDeclaration(false)
      onChanged()
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setProblems(cause.problems.length ? cause.problems : [cause.message])
      } else {
        toast.error(
          cause instanceof Error ? cause.message : 'Transition failed.'
        )
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className='mt-4'>
      <CardHeader>
        <CardTitle className='flex items-center gap-2'>
          <BadgeCheck className='size-5' /> Review status
        </CardTitle>
        <CardDescription>
          Each passport version carries one status and moves only one step at a
          time. Editing any input, override, assumption or the factor set
          creates a new Draft version.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-5'>
        <div className='flex flex-wrap items-center gap-2'>
          {FLOW.map((status, index) => (
            <div key={status} className='flex items-center gap-2'>
              <StatusBadge
                label={status}
                variant={
                  status === passport.status ? statusVariant(status) : 'neutral'
                }
                className={
                  status === passport.status
                    ? 'ring-2 ring-offset-1'
                    : 'opacity-60'
                }
              />
              {index < FLOW.length - 1 && (
                <ArrowRight className='size-3.5 text-muted-foreground' />
              )}
            </div>
          ))}
          <span className='ms-2 text-sm text-muted-foreground'>
            Passport v{passport.version_number} · {passport.status_wording}
          </span>
        </div>

        {passport.status === 'Externally Verified' &&
          passport.verifier_details && (
            <Alert>
              <AlertTitle>Verifier details recorded by the user</AlertTitle>
              <AlertDescription>
                {Object.entries(passport.verifier_details)
                  .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`)
                  .join(' · ')}
                <span className='mt-1 block font-semibold'>
                  Recorded by the user; not verified by this tool.
                </span>
              </AlertDescription>
            </Alert>
          )}

        {next ? (
          <div className='space-y-4 rounded-lg border p-4'>
            <div className='flex flex-wrap items-center justify-between gap-3'>
              <p className='text-sm font-semibold'>
                Next step: {passport.status} → {next}{' '}
                <span className='font-normal text-muted-foreground'>
                  (by the {requiredRole})
                </span>
              </p>
              <ActingAs compact />
            </div>
            {(FIELDS[next] ?? []).length > 0 && (
              <div className='grid gap-3 sm:grid-cols-2'>
                {FIELDS[next]!.map((field) => (
                  <div key={field.key} className='space-y-1.5'>
                    <Label htmlFor={`extra-${field.key}`}>{field.label}</Label>
                    <Input
                      id={`extra-${field.key}`}
                      type={field.type ?? 'text'}
                      value={extra[field.key] ?? ''}
                      onChange={(event) =>
                        setExtra((e) => ({
                          ...e,
                          [field.key]: event.target.value,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            )}
            <div className='space-y-1.5'>
              <Label htmlFor='transition-note'>
                {next === 'Expert-Reviewed'
                  ? 'Reviewer note (required)'
                  : 'Note'}
              </Label>
              <Textarea
                id='transition-note'
                rows={2}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            {next === 'User-Confirmed' && (
              <label className='flex items-start gap-2 text-sm'>
                <Checkbox
                  checked={declaration}
                  onCheckedChange={(value) => setDeclaration(value === true)}
                  className='mt-0.5'
                />
                I declare that the activity data and supplier values in this
                passport are complete and correct to the best of my knowledge,
                and that every assumption listed is disclosed.
              </label>
            )}
            {next === 'Externally Verified' && (
              <p className='text-xs text-muted-foreground'>
                You are recording a verification statement you hold. This tool
                does not check it, and the PDF says so.
              </p>
            )}
            {problems.length > 0 && (
              <Alert variant='destructive'>
                <AlertTitle>Not yet possible</AlertTitle>
                <AlertDescription>
                  <ul className='list-inside list-disc text-sm'>
                    {problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            )}
            <Button onClick={() => void submit()} disabled={busy}>
              {busy && <Loader2 className='size-4 animate-spin' />}
              Move to {next}
            </Button>
          </div>
        ) : (
          <p className='text-sm text-muted-foreground'>
            This passport version has reached the final status.
          </p>
        )}

        <div>
          <p className='mb-2 text-sm font-semibold'>Status history</p>
          <div className='overflow-x-auto'>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Change</TableHead>
                  <TableHead>Who</TableHead>
                  <TableHead>Note</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history
                  .slice()
                  .reverse()
                  .map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className='text-xs whitespace-nowrap'>
                        {new Date(
                          row.timestamp +
                            (row.timestamp.endsWith('Z') ? '' : 'Z')
                        ).toLocaleString()}
                      </TableCell>
                      <TableCell>v{row.version_number}</TableCell>
                      <TableCell className='text-xs'>
                        {row.from ?? '(new)'} → <strong>{row.to}</strong>
                      </TableCell>
                      <TableCell className='text-xs'>
                        {row.actor}{' '}
                        <span className='text-muted-foreground'>
                          ({row.role})
                        </span>
                      </TableCell>
                      <TableCell className='max-w-md text-xs whitespace-normal text-muted-foreground'>
                        {row.note}
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
