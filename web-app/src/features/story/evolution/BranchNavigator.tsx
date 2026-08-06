import { GitBranch, Languages, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useTranslation } from '@/i18n'
import type { SessionSnapshot } from './useSimulationSession'
import type { BranchComparison, BranchManifest } from './useSimulationSession'

interface Props {
  session: SessionSnapshot
  branches: BranchManifest[]
  pending: (name: 'fork' | 'restore' | 'locale' | 'compare') => boolean
  onFork: (branchId: string) => Promise<unknown>
  onRestore: (checkpointId: string) => Promise<unknown>
  onLocale: (locale: string) => Promise<unknown>
  onCompare: (left: string, right: string) => Promise<BranchComparison | null>
}

export function BranchNavigator(props: Props) {
  const { t } = useTranslation('evolution')
  const [branchId, setBranchId] = useState('alternate')
  const [checkpointId, setCheckpointId] = useState(props.session.checkpoint_id ?? '')
  const [locale, setLocale] = useState(props.session.content_locale)
  const [compareWith, setCompareWith] = useState(
    props.branches.find((branch) => branch.branch_id !== props.session.branch_id)
      ?.branch_id ?? props.session.branch_id
  )
  const [comparison, setComparison] = useState<BranchComparison | null>(null)
  return (
    <section className="grid gap-4 border bg-background p-4 xl:grid-cols-3">
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {t('branch.title')}
        </p>
        <div className="flex gap-2">
          <Input
            aria-label={t('branch.newId')}
            onChange={(event) => setBranchId(event.target.value)}
            value={branchId}
          />
          <Button
            disabled={!props.session.checkpoint_id || props.pending('fork')}
            onClick={() => void props.onFork(branchId)}
            variant="outline"
          >
            <GitBranch size={14} /> {t('branch.create')}
          </Button>
        </div>
      </div>
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {t('restore.title')}
        </p>
        <div className="flex gap-2">
          <Input
            aria-label={t('restore.checkpoint')}
            onChange={(event) => setCheckpointId(event.target.value)}
            value={checkpointId}
          />
          <Button
            disabled={!checkpointId || props.pending('restore')}
            onClick={() => void props.onRestore(checkpointId)}
            variant="outline"
          >
            <RotateCcw size={14} /> {t('restore.action')}
          </Button>
        </div>
        <div className="mt-2 flex gap-2">
          <Input
            aria-label={t('locale')}
            onChange={(event) => setLocale(event.target.value)}
            value={locale}
          />
          <Button
            disabled={!locale || props.pending('locale')}
            onClick={() => void props.onLocale(locale)}
            variant="outline"
          >
            <Languages size={14} /> {t('localeApply')}
          </Button>
        </div>
      </div>
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {t('branch.navigator')}
        </p>
        <div className="space-y-1 border p-2 text-xs">
          {props.branches.map((branch) => (
            <div className="flex items-center justify-between gap-2" key={branch.branch_id}>
              <span style={{ paddingLeft: branch.parent_branch_id ? 12 : 0 }}>
                {branch.parent_branch_id ? '└─ ' : ''}{branch.branch_id}
              </span>
              <span className="font-mono text-muted-foreground">{branch.head_step}</span>
            </div>
          ))}
        </div>
        {props.branches.length > 1 && (
          <div className="mt-2 flex gap-2">
            <select
              aria-label={t('branch.compareWith')}
              className="h-9 min-w-0 flex-1 border bg-background px-2 text-sm"
              onChange={(event) => setCompareWith(event.target.value)}
              value={compareWith}
            >
              {props.branches.map((branch) => (
                <option key={branch.branch_id} value={branch.branch_id}>
                  {branch.branch_id}
                </option>
              ))}
            </select>
            <Button
              disabled={compareWith === props.session.branch_id || props.pending('compare')}
              onClick={async () => {
                setComparison(
                  await props.onCompare(props.session.branch_id, compareWith)
                )
              }}
              variant="outline"
            >
              {t('branch.compare')}
            </Button>
          </div>
        )}
        {comparison && (
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="border p-2">
              <p className="font-semibold">{comparison.left.branch_id}</p>
              {comparison.only_left_events.map((event) => <p className="mt-1" key={event}>{event}</p>)}
              {comparison.only_left_entities.map((id) => <p className="mt-1" key={id}>+ {id}</p>)}
            </div>
            <div className="border p-2">
              <p className="font-semibold">{comparison.right.branch_id}</p>
              {comparison.only_right_events.map((event) => <p className="mt-1" key={event}>{event}</p>)}
              {comparison.only_right_entities.map((id) => <p className="mt-1" key={id}>+ {id}</p>)}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
