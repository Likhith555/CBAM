import { useNavigate } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Textarea } from '@/components/ui/textarea'
import { Main } from '@/components/layout/main'
import type { ProcessSource } from '@/lib/cbam-api'
import { useCbamStore } from '@/stores/cbam-store'
import { CbamTopBar } from '../components/cbam-top-bar'
import { PageHeader } from '../components/page-header'
import { StepIndicator } from '../components/step-indicator'

export function AllocationPage() {
  const navigate = useNavigate()
  const allocation = useCbamStore((state) => state.allocation)
  const setAllocation = useCbamStore((state) => state.setAllocation)
  const process = useCbamStore((state) => state.process)
  const setProcess = useCbamStore((state) => state.setProcess)
  const dedicated = allocation.fuel_is_shared === false && allocation.electricity_is_shared === false

  const updateSource = (index: number, patch: Partial<ProcessSource>) =>
    setProcess({
      ...process,
      sources: process.sources.map((source, i) => (i === index ? { ...source, ...patch } : source)),
    })

  const target = Number(allocation.target_production_tonnes ?? 0)
  const total = Number(allocation.total_facility_production_tonnes ?? 0)
  const alpha = total > 0 ? Math.min(1, target / total) : 0
  const invalid = total > 0 && target > total

  return (
    <>
      <CbamTopBar />
      <Main>
        <StepIndicator current={4} />
        <PageHeader
          title='Mass allocation'
          description='Shared utilities serve several product lines. The allocation factor αₚ apportions facility emissions to the target product family by mass.'
        />

        <div className='grid gap-4 lg:grid-cols-3'>
          <Card className='lg:col-span-2'>
            <CardHeader>
              <CardTitle>Production quantities</CardTitle>
              <CardDescription>
                Use net production for the reporting period, in tonnes.
              </CardDescription>
            </CardHeader>
            <CardContent className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='target'>Target product output (t)</Label>
                <Input
                  id='target'
                  type='number'
                  step='any'
                  value={allocation.target_production_tonnes ?? ''}
                  onChange={(event) => {
                    const value = event.target.value
                    setAllocation({ target_production_tonnes: value === '' ? null : Number(value) })
                  }}
                />
              </div>
              <div className='space-y-2'>
                <Label htmlFor='total'>
                  Total facility output (t){dedicated && ' — optional, utilities are dedicated'}
                </Label>
                <Input
                  id='total'
                  type='number'
                  step='any'
                  value={allocation.total_facility_production_tonnes ?? ''}
                  onChange={(event) => {
                    const value = event.target.value
                    setAllocation({ total_facility_production_tonnes: value === '' ? null : Number(value) })
                  }}
                />
              </div>
              {(['fuel', 'electricity'] as const).map((utility) => {
                const key = utility === 'fuel' ? 'fuel_is_shared' : 'electricity_is_shared'
                const value = allocation[key]
                return (
                  <div key={utility} className='space-y-2'>
                    <Label>{utility === 'fuel' ? 'Fuel record covers' : 'Electricity record covers'}</Label>
                    <Select
                      value={value == null ? '' : value ? 'shared' : 'product'}
                      onValueChange={(choice) =>
                        setAllocation(
                          utility === 'fuel'
                            ? { fuel_is_shared: choice === 'shared' }
                            : { electricity_is_shared: choice === 'shared' }
                        )
                      }
                    >
                      <SelectTrigger><SelectValue placeholder='Select from your meter or invoice records' /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value='shared'>Shared installation use</SelectItem>
                        <SelectItem value='product'>Target product line only</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Allocation factor</CardTitle>
              <CardDescription>αₚ = target ÷ facility output</CardDescription>
            </CardHeader>
            <CardContent className='space-y-3'>
              <p className='text-4xl font-bold tabular-nums'>
                {dedicated ? '1.0000' : alpha.toFixed(4)}
              </p>
              {dedicated && (
                <p className='text-xs text-muted-foreground'>
                  Both utility records cover the target line only, so αₚ = 1.
                </p>
              )}
              <p className='text-sm text-muted-foreground'>
                {target.toLocaleString()} t of {total.toLocaleString()} t
              </p>
              {invalid && (
                <p className='text-sm text-destructive'>
                  Target output cannot exceed total facility output.
                </p>
              )}
              {total <= 0 && !dedicated && (
                <p className='text-sm text-destructive'>
                  Total facility output must be greater than zero.
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className='mt-4'>
          <CardHeader>
            <CardTitle>Process emissions</CardTitle>
            <CardDescription>
              Required declaration. Either list the process sources (e.g. from forging or heat treatment chemistry)
              with evidence, or declare that there are none and say why. Nothing is assumed to be zero.
            </CardDescription>
          </CardHeader>
          <CardContent className='space-y-4'>
            <RadioGroup
              value={process.mode ?? ''}
              onValueChange={(mode) =>
                setProcess({
                  ...process,
                  mode: mode as 'none' | 'sources',
                  sources:
                    mode === 'sources' && process.sources.length === 0
                      ? [{ name: '', method: 'direct', direct_tco2: null, evidence_ref: '' }]
                      : process.sources,
                })
              }
              className='flex flex-wrap gap-6'
            >
              <label className='flex items-center gap-2 text-sm'>
                <RadioGroupItem value='none' /> No process emissions
              </label>
              <label className='flex items-center gap-2 text-sm'>
                <RadioGroupItem value='sources' /> List process sources
              </label>
            </RadioGroup>

            {process.mode === 'none' && (
              <div className='space-y-2'>
                <Label htmlFor='process-reason'>Written reason (required)</Label>
                <Textarea
                  id='process-reason'
                  value={process.reason ?? ''}
                  onChange={(event) => setProcess({ ...process, reason: event.target.value })}
                  placeholder='e.g. Cold forging and electric heat treatment only; no carbonate or reductant use'
                />
              </div>
            )}

            {process.mode === 'sources' && (
              <div className='space-y-3'>
                {process.sources.map((source, index) => (
                  <div key={index} className='grid gap-3 rounded-lg border p-3 sm:grid-cols-[2fr_1fr_2fr_auto]'>
                    <div className='space-y-1.5'>
                      <Label>Source name</Label>
                      <Input value={source.name} onChange={(e) => updateSource(index, { name: e.target.value })} />
                    </div>
                    <div className='space-y-1.5'>
                      <Label>Direct tCO₂</Label>
                      <Input
                        type='number'
                        step='any'
                        value={source.direct_tco2 ?? ''}
                        onChange={(e) =>
                          updateSource(index, { direct_tco2: e.target.value === '' ? null : Number(e.target.value) })
                        }
                      />
                    </div>
                    <div className='space-y-1.5'>
                      <Label>Evidence reference</Label>
                      <Input
                        value={source.evidence_ref ?? ''}
                        onChange={(e) => updateSource(index, { evidence_ref: e.target.value })}
                      />
                    </div>
                    <Button
                      variant='ghost'
                      size='icon'
                      className='self-end'
                      aria-label='Remove source'
                      onClick={() =>
                        setProcess({ ...process, sources: process.sources.filter((_, i) => i !== index) })
                      }
                    >
                      <Trash2 className='size-4' />
                    </Button>
                  </div>
                ))}
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() =>
                    setProcess({
                      ...process,
                      sources: [...process.sources, { name: '', method: 'direct', direct_tco2: null, evidence_ref: '' }],
                    })
                  }
                >
                  <Plus className='size-4' /> Add source
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        <div className='mt-6 flex justify-end gap-2'>
          <Button
            onClick={() => {
              if (allocation.fuel_is_shared === null || allocation.electricity_is_shared === null) {
                toast.error('Choose the allocation basis shown by both utility records.')
                return
              }
              if (!(target > 0) || (!dedicated && !(total > 0)) || invalid) {
                toast.error('Enter valid target and total production quantities.')
                return
              }
              toast.success('Allocation saved.')
              void navigate({ to: '/passport' })
            }}
            disabled={(!dedicated && total <= 0) || invalid}
          >
            Save and calculate
          </Button>
        </div>
      </Main>
    </>
  )
}
