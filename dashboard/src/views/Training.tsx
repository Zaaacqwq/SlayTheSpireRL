import { useEffect, useMemo, useState } from 'react'
import { Gauge, ShieldCheck, TrendingDown } from 'lucide-react'
import { fetchMetrics } from '../api'
import type { MetricRow, Run, VisibilityBlock } from '../types'
import { MetricChart, MiniChart, SERIES, stageSegments, type GuideLine } from '../components/charts'
import { Empty, Panel, StagePicker } from '../components/ui'
import { useI18n } from '../i18n'
import { useStageFilter } from '../useStageFilter'

/* PPO optimisation health: is the update stable, or is KL blowing past its stop,
 * value regression collapsing, entropy crashing? Plus the visibility contract that
 * v7-clean made auto-enforced — pointer misses, unknown entities, non-finite
 * features are surfaced here instead of silently passing forward. */

export function Training({ run }: { run: Run }) {
  const { t } = useI18n()
  const [allMetrics, setMetrics] = useState<MetricRow[]>([])

  useEffect(() => {
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
  }, [run.name, run.history_count])

  const { stages, stage, setStage, metrics } = useStageFilter(allMetrics)
  const segments = useMemo(() => stageSegments(metrics), [metrics])

  const config = run.config as Record<string, unknown>
  const targetKl = Number(config.target_kl)
  const hardKl = Number(config.hard_kl_stop)
  const klGuides: GuideLine[] = []
  if (Number.isFinite(targetKl)) klGuides.push({ value: targetKl, label: 'target', color: 'var(--warning)' })
  if (Number.isFinite(hardKl)) klGuides.push({ value: hardKl, label: 'hard stop', color: 'var(--critical)' })

  const latestVis = useMemo(() => {
    for (let i = metrics.length - 1; i >= 0; i -= 1) {
      if (metrics[i].visibility) return metrics[i].visibility as VisibilityBlock
    }
    return undefined
  }, [metrics])

  if (!allMetrics.length) return <Empty text={t('chart.noMetrics')} />

  return <div className="stack">
    <StagePicker stages={stages} value={stage} onChange={setStage} allLabel={t('stage.all')} />

    <Panel icon={<Gauge />} title={t('train.kl')} tip={t('tip.kl')}>
      <MetricChart rows={metrics} segments={segments} height={220} digits={4} guides={klGuides} series={[
        { key: 'approx_kl', name: 'approx_kl', color: SERIES.violet },
      ]} />
    </Panel>

    <div className="mini-grid">
      <MiniChart rows={metrics} dataKey="clip_fraction" title="clip_fraction" color={SERIES.blue} tip={t('tip.clip')} />
      <MiniChart rows={metrics} dataKey="entropy" title="entropy" color={SERIES.aqua} tip={t('tip.entropy')} />
      <MiniChart rows={metrics} dataKey="explained_variance" title="explained_var" color={SERIES.green} tip={t('tip.explainedVar')} />
      <MiniChart rows={metrics} dataKey="grad_norm" title="grad_norm" color={SERIES.orange} tip={t('tip.gradNorm')} />
    </div>

    <div className="grid-2">
      <Panel icon={<TrendingDown />} title={t('train.losses')} tip={t('tip.loss')}>
        <MetricChart rows={metrics} segments={segments} height={200} digits={4} series={[
          { key: 'policy_loss', name: 'policy_loss', color: SERIES.blue },
          { key: 'value_loss', name: 'value_loss', color: SERIES.orange },
          { key: 'loss', name: 'loss', color: SERIES.grey },
        ]} />
      </Panel>
      <Panel icon={<Gauge />} title={t('train.throughput')} tip={t('tip.throughput')}>
        <MetricChart rows={metrics} segments={segments} height={200} digits={1} series={[
          { key: 'seconds', name: t('train.secondsPerIter'), color: SERIES.violet },
        ]} />
      </Panel>
    </div>

    <Panel
      icon={<ShieldCheck />}
      title={t('train.contract')}
      tip={t('tip.contract')}
      tone={contractBreached(latestVis) ? 'critical' : 'good'}
    >
      {latestVis ? <ContractGrid vis={latestVis} /> : <Empty text={t('common.noData')} />}
    </Panel>
  </div>
}

function count(value: unknown): number {
  if (Array.isArray(value)) return value.length
  if (value && typeof value === 'object') return Object.keys(value).length
  return Number(value) || 0
}

function contractBreached(vis?: VisibilityBlock): boolean {
  if (!vis) return false
  // unknown_entities is a tolerated runtime tail (summoned creatures, relic-keyed
  // event options); a systemic flood is already promoted into `violations`.
  return count(vis.violations) > 0 || count(vis.nonfinite_features) > 0
    || count(vis.pointer_misses) > 0
    || count(vis.candidate_collisions) > 0
}

function ContractGrid({ vis }: { vis: VisibilityBlock }) {
  const { t } = useI18n()
  const cells: { label: string; value: number; breach: boolean }[] = [
    { label: t('contract.decisions'), value: count(vis.decisions), breach: false },
    { label: t('contract.violations'), value: count(vis.violations), breach: count(vis.violations) > 0 },
    { label: t('contract.pointerMisses'), value: count(vis.pointer_misses), breach: count(vis.pointer_misses) > 0 },
    { label: t('contract.unknownEntities'), value: count(vis.unknown_entities), breach: false },
    { label: t('contract.nonfinite'), value: count(vis.nonfinite_features), breach: count(vis.nonfinite_features) > 0 },
    { label: t('contract.collisions'), value: count(vis.candidate_collisions), breach: count(vis.candidate_collisions) > 0 },
  ]
  return (
    <div className="contract-grid">
      {cells.map(cell => (
        <div key={cell.label} className={`contract-cell ${cell.breach ? 'bad' : ''}`}>
          <span>{cell.label}</span>
          <b className="num">{cell.value.toLocaleString()}</b>
        </div>
      ))}
    </div>
  )
}
