import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Crosshair, Swords } from 'lucide-react'
import { fetchMetrics } from '../api'
import { isRunStage, num, pct } from '../format'
import type { MetricRow, Run, VisibilityBlock } from '../types'
import { MetricChart, SERIES_ORDER, stageSegments, type SeriesSpec } from '../components/charts'
import { Empty, Meter, Panel, StagePicker } from '../components/ui'
import { useI18n } from '../i18n'
import { useStageFilter } from '../useStageFilter'

/* The instruments that were missing while two bugs survived from M1 to v5:
 *  - nothing compared the return of a win against the return of a loss, so a
 *    reward function that paid more for dying at the boss than for beating it ran
 *    for the project's whole history;
 *  - nothing showed which actions the policy actually takes, so `use_potion` sat
 *    at a structural zero — the agent could see three potions at the boss and had
 *    no way to drink them.
 * Both are one glance away here. */

const ACTION_PREFIX = 'action_'
const TOP_ACTIONS = 6

export function Diagnostics({ run }: { run: Run }) {
  const { t } = useI18n()
  const [allMetrics, setMetrics] = useState<MetricRow[]>([])

  useEffect(() => {
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
  }, [run.name, run.history_count])

  const { stages, stage, setStage, metrics } = useStageFilter(allMetrics)
  const segments = useMemo(() => stageSegments(metrics), [metrics])
  const showFunnel = stage === '' ? stages.some(isRunStage) : isRunStage(stage)

  const inverted = useMemo(() => metrics.filter(row => Number(row.inverted) === 1), [metrics])
  const latestHealth = useMemo(
    () => [...metrics].reverse().find(row => typeof row.win_return === 'number'),
    [metrics],
  )

  // Every action type present, ranked by peak share, each with its own colour.
  // Many are structurally tiny, so charting all 14 at once is unreadable — the
  // legend below is a click filter (see hiddenActions) that starts with just the
  // busiest TOP_ACTIONS shown and lets you toggle any line in or out.
  const actionSeries: SeriesSpec[] = useMemo(() => {
    const peak = new Map<string, number>()
    for (const row of metrics) {
      for (const [key, value] of Object.entries(row)) {
        if (key.startsWith(ACTION_PREFIX) && typeof value === 'number') {
          peak.set(key, Math.max(peak.get(key) ?? 0, value))
        }
      }
    }
    return [...peak.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([key], i) => ({ key, name: key.slice(ACTION_PREFIX.length), color: SERIES_ORDER[i % SERIES_ORDER.length] }))
  }, [metrics])

  // The chart's legend is itself the filter now (see MetricChart); we only seed
  // which lines start hidden — the tail past the busiest TOP_ACTIONS, so the
  // default view stays legible while every action stays one click away.
  const actionDefaultHidden = useMemo(
    () => actionSeries.slice(TOP_ACTIONS).map(spec => spec.key),
    [actionSeries],
  )

  const reach = useMemo(() => reachability(metrics), [metrics])

  // The per-act funnel keys (reached_act{n}_rate / cleared_act{n}_rate) appear
  // once training runs under multi-act telemetry. Older runs only carry the
  // act-1-anchored reached_boss_rate, so we detect how many acts are present and
  // fall back to the single funnel when none are.
  const actCount = useMemo(() => {
    let max = 0
    for (const row of metrics) {
      for (let n = 1; n <= 3; n += 1) {
        if (typeof row[`reached_act${n}_rate`] === 'number') max = Math.max(max, n)
      }
    }
    return max
  }, [metrics])

  if (!allMetrics.length) return <Empty text={t('chart.noMetrics')} />

  // A "reach act N" and a "beat act N" curve per reachable act, plus boss-replay
  // retention if present. Both halves of the funnel belong on the trend, not just
  // the bar steps — otherwise "reached the boss but lost there" is invisible over
  // time. reached/beat get adjacent palette slots so each act reads as a pair.
  const funnelTrend: SeriesSpec[] = Array.from({ length: actCount }, (_, i) => i + 1).flatMap(n => [
    { key: `reached_act${n}_rate`, name: t('diag.reachAct', { act: n }), color: SERIES_ORDER[(2 * (n - 1)) % SERIES_ORDER.length] },
    { key: `cleared_act${n}_rate`, name: t('diag.beatAct', { act: n }), color: SERIES_ORDER[(2 * (n - 1) + 1) % SERIES_ORDER.length] },
  ])
  if (metrics.some(row => typeof row.boss_replay_win_rate === 'number')) {
    funnelTrend.push({ key: 'boss_replay_win_rate', name: t('diag.bossReplay'), color: 'var(--series-7)' })
  }

  return <div className="stack">
    <StagePicker stages={stages} value={stage} onChange={setStage} allLabel={t('stage.all')} />

    <Panel
      icon={inverted.length ? <AlertTriangle /> : <CheckCircle2 />}
      title={t('diag.rewardHealth')}
      tip={t('tip.rewardHealth')}
      tone={inverted.length ? 'critical' : undefined}
    >
      {inverted.length ? (
        <div className="alarm" role="alert">
          <b>{t('diag.invertedTitle')}</b>
          <span>{t('diag.invertedBody', { count: inverted.length })}</span>
        </div>
      ) : (
        <div className="reassure">
          {t('diag.healthy', { win: num(latestHealth?.win_return, 2), loss: num(latestHealth?.loss_return, 2) })}
        </div>
      )}
      <MetricChart rows={metrics} segments={segments} height={200} series={[
        { key: 'win_return', name: t('diag.winReturn'), color: 'var(--series-2)' },
        { key: 'loss_return', name: t('diag.lossReturn'), color: 'var(--series-6)' },
      ]} />
    </Panel>

    <Panel icon={<Crosshair />} title={t('diag.actionMix')} tip={t('tip.actionMix')}>
      {reach.length > 0 && (
        <div className="reach-row">
          {reach.map(item => (
            <span key={item.action} className={`reach-chip ${item.state}`} title={t(`reach.${item.state}`)}>
              <i />{item.action}<b className="num">{pct(item.share)}</b>
            </span>
          ))}
        </div>
      )}
      {actionSeries.length
        ? <MetricChart rows={metrics} segments={segments} height={220} percent series={actionSeries}
                       defaultHidden={actionDefaultHidden} />
        : <Empty text={t('common.noData')} />}
    </Panel>

    <Panel icon={<Swords />} title={t('diag.bossFunnel')} tip={t('tip.bossFunnel')}>
      {!showFunnel ? <Empty text={t('stage.noFunnel')} />
      : actCount > 0 ? <>
        <div className="funnel-steps">
          {Array.from({ length: actCount }, (_, i) => i + 1).flatMap(n => [
            { key: `r${n}`, label: t('diag.reachAct', { act: n }), value: lastOf(metrics, `reached_act${n}_rate`), tone: 'accent' as const },
            { key: `c${n}`, label: t('diag.beatAct', { act: n }), value: lastOf(metrics, `cleared_act${n}_rate`), tone: 'good' as const },
          ]).map(step => (
            <div className="funnel-step" key={step.key}>
              <span className="funnel-label">{step.label}</span>
              <Meter value={step.value ?? 0} tone={step.tone} />
              <b className="num">{pct(step.value)}</b>
            </div>
          ))}
        </div>
        <MetricChart rows={metrics} segments={segments} height={200} percent series={funnelTrend} />
      </> : <>
        <MetricChart rows={metrics} segments={segments} height={200} percent series={[
          { key: 'reached_boss_rate', name: t('diag.reached'), color: 'var(--series-3)' },
          { key: 'boss_conversion', name: t('diag.converted'), color: 'var(--series-2)' },
          { key: 'boss_replay_win_rate', name: t('diag.bossReplay'), color: 'var(--series-5)' },
        ]} />
        <div className="funnel-note">{t('diag.funnelNote', {
          reached: pct(lastOf(metrics, 'reached_boss_rate')),
          converted: pct(lastOf(metrics, 'boss_conversion')),
        })}</div>
      </>}
    </Panel>
  </div>
}

interface Reach { action: string; state: 'ok' | 'rare' | 'zero'; share: number }

/** Per-action-type reachability from the visibility block: an action offered but
 *  never once chosen is the structural-zero signature (the potion bug). */
function reachability(metrics: MetricRow[]): Reach[] {
  const offered = new Map<string, number>()
  const chosen = new Map<string, number>()
  let latestVis: VisibilityBlock | undefined
  for (const row of metrics) {
    const vis = row.visibility
    if (!vis) continue
    latestVis = vis
    for (const [action, count] of Object.entries(vis.offered_actions ?? {})) {
      offered.set(action, (offered.get(action) ?? 0) + count)
    }
    for (const [action, count] of Object.entries(vis.chosen_actions ?? {})) {
      chosen.set(action, (chosen.get(action) ?? 0) + count)
    }
  }
  if (!offered.size) return []
  const latestDecisions = latestVis?.decisions ?? 0
  return [...offered.entries()]
    .filter(([, count]) => count > 0)
    .sort((a, b) => (chosen.get(b[0]) ?? 0) - (chosen.get(a[0]) ?? 0))
    .map(([action]) => {
      const chosenTotal = chosen.get(action) ?? 0
      const latestChosen = latestVis?.chosen_actions?.[action] ?? 0
      const share = latestDecisions ? latestChosen / latestDecisions : 0
      const state: Reach['state'] = chosenTotal === 0 ? 'zero' : share < 0.002 ? 'rare' : 'ok'
      return { action, state, share }
    })
}

function lastOf(rows: MetricRow[], key: string): number | undefined {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const value = rows[i][key]
    if (typeof value === 'number') return value
  }
  return undefined
}
