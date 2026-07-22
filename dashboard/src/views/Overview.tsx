import { useEffect, useMemo, useState } from 'react'
import { Activity, Layers3, Settings2, TrendingUp } from 'lucide-react'
import { fetchMetrics } from '../api'
import { isRunStage, num, pct, stageLabel, STAGE_ORDER, STAGE_THRESHOLDS } from '../format'
import type { MetricRow, Run } from '../types'
import { FloorChart, MiniChart, SERIES, StageLegend, WinRateChart, stageSegments } from '../components/charts'
import { Empty, Meter, Panel, StagePicker, StatTile } from '../components/ui'
import { useI18n } from '../i18n'
import { useStageFilter } from '../useStageFilter'

/* Config keys worth showing, in order. The rest (paths, seeds, telemetry knobs)
 * stay behind the raw-JSON toggle so the setup card reads at a glance. */
const SETUP_KEYS = [
  'stage', 'max_stage', 'lr', 'target_kl', 'hard_kl_stop', 'hidden', 'layers', 'heads',
  'episodes_per_iteration', 'workers', 'boss_mix', 'minibatch_size', 'update_epochs', 'vocab_size',
]

export function Overview({ run, onInspectIteration: _onInspectIteration }: {
  run: Run
  onInspectIteration: (iteration: number) => void
}) {
  const { t, locale } = useI18n()
  const [allMetrics, setMetrics] = useState<MetricRow[]>([])

  useEffect(() => {
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
  }, [run.name, run.history_count, run.episode_count])

  const { stages, stage, setStage, metrics } = useStageFilter(allMetrics)
  const segments = useMemo(() => stageSegments(metrics), [metrics])
  const latest = metrics[metrics.length - 1]
  const bestDev = useMemo(() => {
    const rows = metrics.filter(row => typeof row.dev_win_rate === 'number')
    return rows.length ? rows.reduce((a, b) => (b.dev_win_rate as number) > (a.dev_win_rate as number) ? b : a) : undefined
  }, [metrics])
  const showFloors = stage === '' ? stages.some(isRunStage) : isRunStage(stage)

  return <div className="stack">
    <StagePicker stages={stages} value={stage} onChange={setStage} allLabel={t('stage.all')} />

    <div className="tile-row">
      <StatTile
        label={t('overview.stage')}
        value={stageLabel(latest?.stage as string, locale)}
        detail={latest ? t('overview.iterationCount', { iteration: latest.iteration, count: metrics.length }) : t('overview.noMetrics')}
        tip={t('tip.stage')}
      />
      <StatTile
        label={t('overview.latestTrain')}
        value={pct(latest?.train_win_rate)}
        detail={latest ? t('overview.avgFloor', { value: num(latest.avg_floor, 1) }) : undefined}
        tip={t('tip.trainWin')}
      />
      <StatTile
        label={t('overview.bestDev')}
        value={pct(bestDev?.dev_win_rate)}
        detail={bestDev ? `${t('common.iteration', { value: bestDev.iteration })} · ${stageLabel(bestDev.stage as string, locale)}` : t('overview.noDev')}
        tip={t('tip.devWin')}
        tone={typeof bestDev?.dev_win_rate === 'number' && (bestDev.dev_win_rate as number) > 0 ? 'good' : undefined}
      />
      <StatTile
        label={t('overview.recorded')}
        value={run.episode_count.toLocaleString()}
        detail={t('overview.checkpoints', { count: run.checkpoints })}
        tip={t('tip.checkpoint')}
        dim={run.episode_count === 0}
      />
    </div>

    <Panel icon={<TrendingUp />} title={t('overview.winChart')} tip={t('tip.iteration')} extra={t('overview.stageBands')}>
      <StageLegend segments={segments} />
      <WinRateChart rows={metrics} segments={segments} />
    </Panel>

    <div className="grid-2">
      <Panel icon={<Activity />} title={t('overview.floorChart')} tip={t('tip.avgFloor')}>
        {showFloors ? <FloorChart rows={metrics} segments={segments} /> : <Empty text={t('stage.noFloors')} />}
      </Panel>
      <Panel icon={<Layers3 />} title={t('overview.gate')} tip={t('tip.gate')}>
        <GateTracker metrics={allMetrics} currentStage={latest?.stage as string | undefined} />
      </Panel>
    </div>

    <div className="mini-grid">
      <MiniChart rows={metrics} dataKey="approx_kl" title="approx_kl" color={SERIES.violet} tip={t('tip.kl')} />
      <MiniChart rows={metrics} dataKey="entropy" title="entropy" color={SERIES.aqua} tip={t('tip.entropy')} />
      <MiniChart rows={metrics} dataKey="explained_variance" title="explained_var" color={SERIES.blue} tip={t('tip.explainedVar')} />
      <MiniChart rows={metrics} dataKey="loss" title="loss" color={SERIES.orange} tip={t('tip.loss')} />
    </div>

    <Panel icon={<Settings2 />} title={t('overview.setup')}>
      <RunSetup config={run.config} />
    </Panel>
  </div>
}

/** Curriculum ladder with the active stage's dev win rate against its gate. */
function GateTracker({ metrics, currentStage }: { metrics: MetricRow[]; currentStage?: string }) {
  const { t, locale } = useI18n()
  const present = STAGE_ORDER.filter(stage => metrics.some(row => row.stage === stage))
  if (!present.length) return <Empty text={t('common.noData')} />
  const currentIndex = currentStage ? STAGE_ORDER.indexOf(currentStage) : present.length - 1

  const bestDevFor = (stage: string): number | undefined => {
    let best: number | undefined
    for (const row of metrics) {
      if (row.stage === stage && typeof row.dev_win_rate === 'number') {
        best = best === undefined ? (row.dev_win_rate as number) : Math.max(best, row.dev_win_rate as number)
      }
    }
    return best
  }

  const activeDev = currentStage ? bestDevFor(currentStage) : undefined
  const activeThreshold = currentStage ? STAGE_THRESHOLDS[currentStage] : undefined

  return <>
    <div className="ladder">
      {present.map(stage => {
        const index = STAGE_ORDER.indexOf(stage)
        const cls = index < currentIndex ? 'done' : index === currentIndex ? 'active' : ''
        return (
          <div key={stage} className={`ladder-row ${cls}`}>
            <span className="ladder-dot" />
            <b>{stageLabel(stage, locale)}</b>
            <span className="ladder-thr">{t('overview.gateThreshold', { value: pct(STAGE_THRESHOLDS[stage]) })}</span>
          </div>
        )
      })}
    </div>
    {activeThreshold !== undefined && (
      <div style={{ marginTop: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}>
          <span style={{ color: 'var(--ink-3)' }}>{t('overview.gateProgress', { stage: stageLabel(currentStage!, locale) })}</span>
          <b className="num" style={{ color: 'var(--ink)' }}>{pct(activeDev)} / {pct(activeThreshold)}</b>
        </div>
        <Meter value={(activeDev ?? 0) / activeThreshold} target={1}
               tone={(activeDev ?? 0) >= activeThreshold ? 'good' : 'accent'} />
        <small style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>{t('overview.gateNote')}</small>
      </div>
    )}
  </>
}

function RunSetup({ config }: { config: Record<string, unknown> }) {
  const { t } = useI18n()
  const rows = SETUP_KEYS
    .filter(key => config[key] !== undefined && config[key] !== null)
    .map(key => [key, config[key]] as const)
  const reward = config.terminal_only === true ? t('overview.rewardTerminal') : t('overview.rewardShaped')
  if (!rows.length) return <Empty text={t('common.noData')} />
  return <>
    <div className="setup-grid">
      <div><span>{t('overview.reward')}</span><b>{reward}</b></div>
      {rows.map(([key, value]) => (
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{String(value)}</b></div>
      ))}
    </div>
    <details className="raw-details">
      <summary>{t('overview.rawConfig')}</summary>
      <pre className="raw">{JSON.stringify(config, null, 2)}</pre>
    </details>
  </>
}
