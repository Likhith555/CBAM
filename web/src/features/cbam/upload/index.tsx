import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { CalendarRange, FileUp, Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Main } from '@/components/layout/main'
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
import {
  API_URL,
  ApiError,
  excludeDocument,
  getDocument,
  getWorkspaceDocuments,
  updateDocumentMetadata,
  uploadDocument,
  type DocumentMetadata,
  type DocumentRecordWithMeta,
  type DuplicateDocumentBody,
} from '@/lib/cbam-api'
import { useCbamStore } from '@/stores/cbam-store'
import { CbamTopBar } from '../components/cbam-top-bar'
import { PageHeader } from '../components/page-header'
import { StepIndicator } from '../components/step-indicator'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.csv,.xlsx'

const META_FIELDS: { key: keyof DocumentMetadata; label: string; type?: string }[] = [
  { key: 'supplier', label: 'Supplier / utility' },
  { key: 'account_number', label: 'Meter or consumer number' },
  { key: 'utility_type', label: 'Utility type (electricity, fuel, …)' },
  { key: 'bill_number', label: 'Bill / invoice number' },
  { key: 'bill_date', label: 'Bill date', type: 'date' },
  { key: 'period_start', label: 'Billing period start', type: 'date' },
  { key: 'period_end', label: 'Billing period end (inclusive)', type: 'date' },
]

export function UploadPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const documents = useCbamStore((state) => state.documents)
  const workspace = useCbamStore((state) => state.workspace)
  const addDocument = useCbamStore((state) => state.addDocument)
  const removeDocument = useCbamStore((state) => state.removeDocument)
  const actor = useCbamStore((state) => state.actingAs.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [duplicate, setDuplicate] = useState<DuplicateDocumentBody | null>(null)
  const [records, setRecords] = useState<Record<string, DocumentRecordWithMeta>>({})
  const [editing, setEditing] = useState<DocumentRecordWithMeta | null>(null)
  const [meta, setMeta] = useState<DocumentMetadata>({})

  const loadRecords = useCallback(async () => {
    if (!workspace.installation_id.trim()) return
    try {
      const rows = await getWorkspaceDocuments(workspace.installation_id.trim())
      setRecords(Object.fromEntries(rows.map((row) => [row.document_hash, row])))
    } catch {
      /* backend offline; the upload error banner explains */
    }
  }, [workspace.installation_id])

  useEffect(() => {
    const timer = setTimeout(() => void loadRecords(), 0)
    return () => clearTimeout(timer)
  }, [loadRecords, documents])

  const loadExisting = async () => {
    if (!duplicate) return
    try {
      const existing = await getDocument(duplicate.existing_document.document_hash)
      addDocument(existing)
      toast.success(`Loaded the existing record ${existing.filename}.`)
      setDuplicate(null)
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : 'Could not load the existing record.')
    }
  }

  const saveMeta = async () => {
    if (!editing) return
    if (!actor.trim()) {
      toast.error('Enter your name in "Acting as" (Review step) first.')
      return
    }
    try {
      const payload: DocumentMetadata = {}
      for (const field of META_FIELDS) payload[field.key] = (meta[field.key] || null) as never
      await updateDocumentMetadata(editing.document_hash, { ...payload, actor: actor.trim() })
      toast.success('Billing details saved and logged.')
      setEditing(null)
      void loadRecords()
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : 'Could not save.')
    }
  }

  const remove = async (documentHash: string) => {
    removeDocument(documentHash)
    try {
      await excludeDocument(documentHash, actor.trim() || 'system', 'Removed on the Upload step.')
    } catch {
      /* local removal still applies */
    }
  }

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    if (!workspace.installation_id.trim()) {
      setError('Save the actual workspace and installation ID before uploading records.')
      return
    }
    setBusy(true)
    setError(null)
    for (const file of Array.from(files)) {
      try {
        const result = await uploadDocument(file, workspace.installation_id)
        addDocument({ ...result, file_size_bytes: file.size })
        toast.success(
          `${result.filename}: ${result.extracted_fields.length} field(s) extracted.`
        )
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) {
          setDuplicate(cause.body as DuplicateDocumentBody)
          toast.warning(`${file.name}: duplicate of an existing document.`)
          continue
        }
        const message =
          cause instanceof Error ? cause.message : 'Upload failed.'
        setError(`${file.name}: ${message}`)
        toast.error(`${file.name}: ${message}`)
      }
    }
    setBusy(false)
    if (inputRef.current) inputRef.current.value = ''
  }

  const rows = Object.values(documents)

  return (
    <>
      <CbamTopBar />
      <Main>
        <StepIndicator current={2} />
        <PageHeader
          title='Upload records'
          description='Electricity bills, fuel invoices, precursor declarations, and production logs. Each file is hashed with SHA-256 and parsed for activity data.'
        >
          {rows.length > 0 && (
            <Button asChild>
              <Link to='/review'>Review extraction</Link>
            </Button>
          )}
        </PageHeader>

        {error && (
          <Alert variant='destructive' className='mb-4'>
            <AlertTitle>Upload problem</AlertTitle>
            <AlertDescription>
              {error}
              <span className='mt-1 block text-xs'>
                Check that the backend is running at {API_URL}.
              </span>
            </AlertDescription>
          </Alert>
        )}

        {duplicate && (
          <Alert className='mb-4 border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100'>
            <AlertTitle>Duplicate document rejected (same SHA-256)</AlertTitle>
            <AlertDescription>
              <p>{duplicate.detail}</p>
              <p className='mt-1 font-mono text-xs'>
                Existing: {duplicate.existing_document.filename} ·{' '}
                <a
                  className='underline'
                  href={`${API_URL}${duplicate.existing_document.url}`}
                  target='_blank'
                  rel='noreferrer'
                >
                  {duplicate.existing_document.document_hash.slice(0, 16)}…
                </a>
              </p>
              <div className='mt-2 flex gap-2'>
                <Button size='sm' onClick={() => void loadExisting()}>
                  Use the existing record
                </Button>
                <Button size='sm' variant='outline' onClick={() => setDuplicate(null)}>
                  Dismiss
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Source documents</CardTitle>
            <CardDescription>
              PDF, PNG, JPG, CSV, or XLSX. Upload each activity type as its own
              file so extraction stays unambiguous.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <label
              htmlFor='record-upload'
              className='flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center transition-colors hover:bg-muted/50'
            >
              {busy ? (
                <Loader2 className='size-6 animate-spin text-muted-foreground' />
              ) : (
                <FileUp className='size-6 text-muted-foreground' />
              )}
              <span className='text-sm font-medium'>
                {busy ? 'Parsing documents…' : 'Click to choose files'}
              </span>
              <span className='text-xs text-muted-foreground'>
                Multiple files allowed. Nothing leaves this machine except the
                request to your local backend.
              </span>
              <input
                ref={inputRef}
                id='record-upload'
                type='file'
                multiple
                accept={ACCEPTED}
                className='sr-only'
                disabled={busy}
                onChange={(event) => void handleFiles(event.target.files)}
              />
            </label>
          </CardContent>
        </Card>

        <Card className='mt-4'>
          <CardHeader>
            <CardTitle>Evidence ledger</CardTitle>
            <CardDescription>
              {rows.length === 0
                ? 'No documents ingested yet.'
                : `${rows.length} document(s) ingested.`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {rows.length === 0 ? (
              <p className='text-sm text-muted-foreground'>
                Upload a record to populate the ledger.
              </p>
            ) : (
              <div className='overflow-x-auto'>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>File</TableHead>
                      <TableHead>SHA-256</TableHead>
                      <TableHead className='text-right'>Pages</TableHead>
                      <TableHead className='text-right'>Fields</TableHead>
                      <TableHead>Billing details</TableHead>
                      <TableHead className='text-right'>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((document) => (
                      <TableRow key={document.document_hash}>
                        <TableCell className='font-medium'>
                          {document.filename}
                        </TableCell>
                        <TableCell className='font-mono text-xs'>
                          {document.document_hash.slice(0, 12)}…
                        </TableCell>
                        <TableCell className='text-right'>
                          {document.page_count}
                        </TableCell>
                        <TableCell className='text-right'>
                          {document.extracted_fields.length}
                        </TableCell>
                        <TableCell className='text-xs'>
                          {(() => {
                            const record = records[document.document_hash]
                            if (!record) return <span className='text-muted-foreground'>—</span>
                            return (
                              <button
                                type='button'
                                className='flex items-center gap-1.5 text-start underline-offset-2 hover:underline'
                                onClick={() => {
                                  setMeta(
                                    Object.fromEntries(META_FIELDS.map((f) => [f.key, record[f.key] ?? ''])) as DocumentMetadata
                                  )
                                  setEditing(record)
                                }}
                              >
                                <CalendarRange className='size-3.5 shrink-0' />
                                {record.period_start
                                  ? `${record.period_start} → ${record.period_end ?? '?'}`
                                  : 'Add period'}
                                {record.bill_number && ` · #${record.bill_number}`}
                              </button>
                            )
                          })()}
                        </TableCell>
                        <TableCell className='text-right'>
                          <Button
                            variant='ghost'
                            size='icon'
                            aria-label={`Remove ${document.filename}`}
                            onClick={() => void remove(document.document_hash)}
                          >
                            <Trash2 className='size-4' />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
        <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Billing details · {editing?.filename}</DialogTitle>
              <DialogDescription>
                Used to detect overlapping billing periods, coverage gaps and near-duplicate bills. Changes are
                audit-logged.
              </DialogDescription>
            </DialogHeader>
            <div className='grid gap-3 sm:grid-cols-2'>
              {META_FIELDS.map((field) => (
                <div key={field.key} className='space-y-1.5'>
                  <Label htmlFor={`meta-${field.key}`}>{field.label}</Label>
                  <Input
                    id={`meta-${field.key}`}
                    type={field.type ?? 'text'}
                    value={(meta[field.key] as string) ?? ''}
                    onChange={(event) => setMeta((m) => ({ ...m, [field.key]: event.target.value }))}
                  />
                </div>
              ))}
            </div>
            <DialogFooter>
              <Button variant='outline' onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button onClick={() => void saveMeta()}>Save details</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Main>
    </>
  )
}
