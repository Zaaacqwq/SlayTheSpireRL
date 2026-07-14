import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Crosshair, Swords } from 'lucide-react'
import { fetchMetrics } from '../api'
import { num, pct } from '../format'
import type { MetricRow, Run } from '../types'
import { MetricChart, SERIES, stageSegments, type SeriesSpec } from '../components/charts'
import { Empty, Panel } from '../components/ui'
import { useI18n } from '../i18n'

/* The instruments that were missing while two bugs survived from M1 to v5:
 *  - nothing compared the return of a win against the return of a loss, so a
 *    reward function that paid more for dying at the boss than for beating it ran
 *    for the project's whole history;
 *  - nothing showed which actions the policy actually takes, so `use_potion` sat
 *    at a structural zero — the agent could see three potions at the boss and had
 *    no way to drink them.
 * Both are one glance away here. */

const ACTION_PREFIX = 'action_'

export function Diagnostics({ run }: { run: Run }) {
  const { t } = useI18n()
  const [metrics, setMetrics] = useState<MetricRow[]>([])

  useEffect(() => {
    if (run.name === 'legacy') { setMetrics([]); return }
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
  }, [run.name, run.history_count])

  const segments = useMemo(() => stageSegments(metrics), [metrics])

  const inverted = useMemo(
    () => metrics.filter(row => Number(row.inverted) === 1),
    [metrics],
  )
  const latestHealth = useMemo(
    () => [...metrics].reverse().find(row => typeof row.win_return === 'number'),
    [metrics],
  )

  const actionSeries: SeriesSpec[] = useMemo(() => {
    const keys = new Set<string>()
    for (const row of metrics) {
      for (const key of Object.keys(row)) {
        if (key.startsWith(ACTION_PREFIX)) keys.add(key)
      }
    }
    const palette = [SERIES.blue, SERIES.aqua, SERIES.yellow, SERIES.violet,
                     SERIES.red, SERIES.pink, SERIES.teal, SERIES.grey]
    return [...keys].sort().map((key, i) => ({
      key,
      name: key.slice(ACTION_PREFIX.length),
      color: palette[i % palette.length],
    }))
  }, [metrics])

  if (!metrics.length) return <Empty text={t('chart.noMetrics')} />

  return <>
    <Panel
      icon={inverted.length ? <AlertTriangle /> : <CheckCircle2 />}
      title={t('diag.rewardHealth')}
      tip={t('tip.rewardHealth')}
    >
      {inverted.length ? (
        <div className="alarm" role="alert">
          <b>{t('diag.invertedTitle')}</b>
          <span>{t('diag.invertedBody', { count: inverted.length })}</span>
        </div>
      ) : (
        <div className="reassure">
          {t('diag.healthy', {
            win: num(latestHealth?.win_return, 2),
            loss: num(latestHealth?.loss_return, 2),
          })}
        </div>
      )}
      <MetricChart rows={metrics} segments={segments} height={200} series={[
        { key: 'win_return', name: t('diag.winReturn'), color: SERIES.aqua },
        { key: 'loss_return', name: t('diag.lossReturn'), color: SERIES.red },
      ]} />
    </Panel>

    <Panel icon={<Crosshair />} title={t('diag.actionMix')} tip={t('tip.actionMix')}>
      {actionSeries.length
        ? <MetricChart rows={metrics} segments={segments} height={220} percent series={actionSeries} />
        : <Empty text={t('common.noData')} />}
    </Panel>

    <Panel icon={<Swords />} title={t('diag.bossFunnel')} tip={t('tip.bossFunnel')}>
      <MetricChart rows={metrics} segments={segments} height={200} percent series={[
        { key: 'reached_boss_rate', name: t('diag.reached'), color: SERIES.yellow },
        { key: 'boss_conversion', name: t('diag.converted'), color: SERIES.aqua },
        { key: 'boss_replay_win_rate', name: t('diag.bossReplay'), color: SERIES.violet },
      ]} />
      <div className="funnel-note">{t('diag.funnelNote', {
        reached: pct(lastOf(metrics, 'reached_boss_rate')),
        converted: pct(lastOf(metrics, 'boss_conversion')),
      })}</div>
    </Panel>
  </>
}

function lastOf(rows: MetricRow[], key: string): number | undefined {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const value = rows[i][key]
    if (typeof value === 'number') return value
  }
  return undefined
}
