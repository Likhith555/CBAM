import { UserRound } from 'lucide-react'
import { useCbamStore } from '@/stores/cbam-store'
import type { ActorRole } from '@/lib/cbam-api'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const ROLES: { value: ActorRole; label: string }[] = [
  { value: 'exporter', label: 'Exporter' },
  { value: 'reviewer', label: 'Expert reviewer' },
  { value: 'verifier', label: 'Recording a verifier statement' },
]

/** Prototype "acting as" selector. Real authentication is future work. */
export function ActingAs({ compact = false }: { compact?: boolean }) {
  const actingAs = useCbamStore((s) => s.actingAs)
  const setActingAs = useCbamStore((s) => s.setActingAs)
  return (
    <div
      className={
        compact ? 'flex flex-wrap items-end gap-2' : 'grid gap-3 sm:grid-cols-2'
      }
    >
      <div className='space-y-1.5'>
        <Label
          htmlFor='acting-name'
          className='flex items-center gap-1.5 text-xs'
        >
          <UserRound className='size-3.5' /> Acting as (your name)
        </Label>
        <Input
          id='acting-name'
          value={actingAs.name}
          placeholder='Type your name'
          className='h-8 w-48'
          onChange={(event) => setActingAs({ name: event.target.value })}
        />
      </div>
      <div className='space-y-1.5'>
        <Label className='text-xs'>Role</Label>
        <Select
          value={actingAs.role}
          onValueChange={(role) => setActingAs({ role: role as ActorRole })}
        >
          <SelectTrigger className='h-8 w-56'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ROLES.map((role) => (
              <SelectItem key={role.value} value={role.value}>
                {role.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
