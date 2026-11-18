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

/* Which decision each action answers (rl/src/sts2rl/protocol.py legal_actions).
 * Sharing one denominator across all of them makes the rare-by-construction
 * actions unreadable: a run is offered remove_card a handful of times against
 * thousands of play_card decisions, so it sits at ~0% whether the policy loves
 * it or cannot reach it. Normalising inside the phase compares each action
 * against the alternatives it actually competed with. */
type PhaseKey = 'combat' | 'map' | 'reward' | 'choice' | 'select' | 'shop' | 'other'

const ACTION_PHASE: Record<string, PhaseKey> = {
  play_card: 'combat', use_potion: 'combat', end_turn: 'combat',
  select_map_node: 'map',
  select_card_reward: 'reward', skip_card_reward: 'reward',
  choose_option: 'choice',
  select_cards: 'select', skip_select: 'select', select_bundle: 'select',
  buy_card: 'shop', buy_relic: 'shop', buy_potion: 'shop',
  remove_card: 'shop', leave_room: 'shop',
}

// 'other' is the escape hatch for an action the protocol grows later: it shows up
// in its own bucket instead of silently vanishing from the chart.
const PHASE_ORDER: PhaseKey[] = ['combat', 'map', 'reward', 'choice', 'select', 'shop', 'other']

const phaseOf = (key: string): PhaseKey => ACTION_PHASE[key.slice(ACTION_PREFIX.length)] ?? 'other'

export function Diagnostics({ run }: { run: Run }) {
  const { t } = useI18n()
  const [allMetrics, setMetrics] = useState<MetricRow[]>([])
  const [phase, setPhase] = useState<PhaseKey | ''>('')

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

  const actionKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const row of metrics) {
      for (const [key, value] of Object.entries(row)) {
        if (key.startsWith(ACTION_PREFIX) && typeof value === 'number') keys.add(key)
      }
    }
    return [...keys]
  }, [metrics])

  const phases = useMemo(
    () => PHASE_ORDER.filter(name => actionKeys.some(key => phaseOf(key) === name)),
    [actionKeys],
  )
  // A combat stage only ever reaches one phase, so the overview would be a flat
  // 100% line. Drop straight into that phase's internal mix instead.
  const shown = phases.length > 1 ? phase : phases[0] ?? ''
  const mix = useMemo(
    () => (shown ? phaseMix(metrics, actionKeys, shown) : overviewMix(metrics, actionKeys, phases)),
    [metrics, actionKeys, shown, phases],
  )
  const mixSeries: SeriesSpec[] = mix.series.map((spec, i) => ({
    ...spec,
    name: shown ? spec.name : t(`phase.${spec.name}`),
    color: SERIES_ORDER[i % SERIES_ORDER.length],
  }))

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

    <Panel icon={<Crosshair />} title={t('diag.actionMix')} tip={t('tip.actionPhase')}>
      {reach.length > 0 && (
        <div className="reach-row">
          {reach.map(item => (
            <span key={item.action} className={`reach-chip ${item.state}`} title={t(`reach.${item.state}`)}>
              <i />{item.action}<b className="num">{pct(item.share)}</b>
            </span>
          ))}
        </div>
      )}
      {phases.length > 1 && (
        <div className="stage-picker" role="tablist">
          <button role="tab" aria-selected={phase === ''} className={phase === '' ? 'active' : ''}
                  onClick={() => setPhase('')}>{t('phase.all')}</button>
          {phases.map(name => (
            <button key={name} role="tab" aria-selected={phase === name}
                    className={phase === name ? 'active' : ''}
                    onClick={() => setPhase(name)}>{t(`phase.${name}`)}</button>
          ))}
        </div>
      )}
      {mixSeries.length ? <>
        <MetricChart rows={mix.rows} segments={segments} height={220} percent series={mixSeries} />
        <div className="funnel-note">
          {shown
            ? t('diag.phaseShareNote', { phase: t(`phase.${shown}`), share: pct(mix.share) })
            : t('diag.phaseOverviewNote')}
        </div>
      </> : <Empty text={t('common.noData')} />}
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

interface Mix {
  rows: MetricRow[]
  /** `name` carries the phase key (overview) or the bare action name; the caller
   *  localises it and assigns the palette slot. */
  series: { key: string; name: string }[]
  share?: number
}

/** How the policy's decision time splits across phases — combat vs shop vs map.
 *  Shares still sum to 1 across the whole chart, so this is the outer ring. */
function overviewMix(metrics: MetricRow[], keys: string[], phases: PhaseKey[]): Mix {
  const rows = metrics.map(row => {
    const out: MetricRow = { iteration: row.iteration, stage: row.stage }
    for (const name of phases) {
      let total = 0
      let seen = false
      for (const key of keys) {
        const value = row[key]
        if (phaseOf(key) === name && typeof value === 'number') { total += value; seen = true }
      }
      if (seen) out[`phase_${name}`] = total
    }
    return out
  })
  return { rows, series: phases.map(name => ({ key: `phase_${name}`, name })) }
}

/** One phase's internal mix: each action as a share of the decisions made *in
 *  that phase*. An iteration that never entered the phase contributes nothing
 *  rather than a fake zero, so the shop curve does not crater on a combat stage. */
function phaseMix(metrics: MetricRow[], keys: string[], phase: PhaseKey): Mix {
  const inPhase = keys.filter(key => phaseOf(key) === phase)
  let lastShare: number | undefined
  const rows = metrics.map(row => {
    const out: MetricRow = { iteration: row.iteration, stage: row.stage }
    const total = inPhase.reduce(
      (sum, key) => sum + (typeof row[key] === 'number' ? (row[key] as number) : 0), 0)
    if (total > 0) {
      for (const key of inPhase) {
        const value = row[key]
        if (typeof value === 'number') out[key] = value / total
      }
      lastShare = total
    }
    return out
  })
  const peak = new Map<string, number>()
  for (const row of rows) {
    for (const key of inPhase) peak.set(key, Math.max(peak.get(key) ?? 0, Number(row[key]) || 0))
  }
  const series = [...inPhase]
    .sort((a, b) => (peak.get(b) ?? 0) - (peak.get(a) ?? 0))
    .map(key => ({ key, name: key.slice(ACTION_PREFIX.length) }))
  return { rows, series, share: lastShare }
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
