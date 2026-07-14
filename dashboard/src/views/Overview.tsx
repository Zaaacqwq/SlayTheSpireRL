import { useEffect, useMemo, useState } from 'react'
import { Activity, Database, Layers3, Settings2, TrendingUp } from 'lucide-react'
import { fetchMetrics, fetchTimeline } from '../api'
import { num, pct, stageLabel, stageTint } from '../format'
import type { MetricRow, Run, TimelineItem } from '../types'
import { FloorChart, MiniChart, StageLegend, WinRateChart, stageSegments } from '../components/charts'
import { Empty, Panel, StatTile } from '../components/ui'
import { useI18n } from '../i18n'

export function Overview({ run, onInspectIteration }: {
  run: Run
  onInspectIteration: (iteration: number) => void
}) {
  const { t, locale } = useI18n()
  const [metrics, setMetrics] = useState<MetricRow[]>([])
  const [timeline, setTimeline] = useState<TimelineItem[]>([])

  useEffect(() => {
    if (run.name === 'legacy') { setMetrics([]); setTimeline([]); return }
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
    void fetchTimeline(run.name).then(data => setTimeline(data.items)).catch(() => setTimeline([]))
    // history_count/episode_count advance during live training, refreshing charts.
  }, [run.name, run.history_count, run.episode_count])

  const segments = useMemo(() => stageSegments(metrics), [metrics])
  const latest = metrics[metrics.length - 1]
  const bestDev = useMemo(() => {
    const rows = metrics.filter(row => typeof row.dev_win_rate === 'number')
    return rows.length ? rows.reduce((a, b) => (b.dev_win_rate as number) > (a.dev_win_rate as number) ? b : a) : undefined
  }, [metrics])
  const replayGroups = timeline.filter(item => item.split === 'replay' && item.iteration !== null)

  return <>
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
        up={typeof bestDev?.dev_win_rate === 'number' && (bestDev.dev_win_rate as number) > 0}
      />
      <StatTile
        label={t('overview.recorded')}
        value={run.episode_count.toLocaleString()}
        detail={t('overview.checkpoints', { count: run.checkpoints })}
        tip={t('tip.checkpoint')}
        dim={run.episode_count === 0}
      />
    </div>

    <div className="chart-stack">
      <Panel icon={<TrendingUp />} title={t('overview.winChart')} tip={t('tip.iteration')} extra={t('overview.stageBands')}>
        <StageLegend segments={segments} />
        <WinRateChart rows={metrics} segments={segments} />
      </Panel>
      <Panel icon={<Activity />} title={t('overview.floorChart')} tip={t('tip.avgFloor')}>
        <FloorChart rows={metrics} segments={segments} />
      </Panel>
    </div>

    <div className="mini-grid">
      <MiniChart rows={metrics} dataKey="loss" title={`${t('overview.loss')} loss`} tip={t('tip.loss')} />
      <MiniChart rows={metrics} dataKey="value_loss" title={`${t('overview.valueLoss')} value loss`} tip={t('tip.valueLoss')} />
      <MiniChart rows={metrics} dataKey="entropy" title={`${t('overview.entropy')} entropy`} tip={t('tip.entropy')} />
    </div>

    <div className="overview-lower">
      <Panel icon={<Layers3 />} title={t('overview.curriculum')} tip={t('tip.stage')}>
        {segments.length ? (
          <div className="stage-cards">
            {segments.map(segment => (
              <div key={`${segment.stage}-${segment.from}`} className="stage-card"
                   style={{ borderLeftColor: stageTint(segment.stage) }}>
                <b>{stageLabel(segment.stage, locale)}</b>
                <small>{t('common.iteration', { value: segment.from })} – {segment.to}{segment.resumed ? ` · ${t('overview.resumed')}` : ''}</small>
                <em>{t('overview.iterations', { count: segment.to - segment.from + 1 })}</em>
              </div>
            ))}
          </div>
        ) : <Empty text={t('common.noData')} />}
      </Panel>

      <Panel icon={<Database />} title={t('overview.recordedBatches')} extra={t('overview.clickBatch')}>
        {replayGroups.length || timeline.length ? (
          <div className="ckpt-strip">
            {timeline.filter(item => item.iteration !== null).map(item => (
              <button key={`${item.split}-${item.iteration}`} className="ckpt-chip"
                      onClick={() => onInspectIteration(item.iteration as number)}>
                <b>{t('common.iteration', { value: item.iteration as number })} · {item.split === 'replay' ? t('nav.replay') : item.split === 'dev' ? (locale === 'en-US' ? 'Validation' : '验证') : (locale === 'en-US' ? 'Training' : '训练')}</b>
                {stageLabel(item.stage, locale)} · {item.wins}/{item.episodes} {t('common.win')} · reward {num(item.avg_reward)}
              </button>
            ))}
          </div>
        ) : <Empty text={t('overview.noRecorded')} />}
      </Panel>
    </div>

    <Panel icon={<Settings2 />} title={t('overview.config')}>
      <ConfigGrid config={run.config} />
    </Panel>
  </>
}

function ConfigGrid({ config }: { config: Record<string, unknown> }) {
  const { t } = useI18n()
  const rows = Object.entries(config).filter(([, value]) => typeof value !== 'object').slice(0, 18)
  if (!rows.length) return <Empty text={t('common.noData')} />
  return <>
    <div className="config-grid">
      {rows.map(([key, value]) => (
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{String(value ?? '—')}</b></div>
      ))}
    </div>
    <details>
      <summary>{t('overview.rawConfig')}</summary>
      <pre className="raw">{JSON.stringify(config, null, 2)}</pre>
    </details>
  </>
}
