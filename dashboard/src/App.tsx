import { useEffect, useMemo, useState } from 'react'
import {
  Activity, BarChart3, BookOpen, GitCompare, Languages, LoaderCircle, Pause, Play,
  Radio, RefreshCw, Route, Stethoscope,
} from 'lucide-react'
import { fetchRuns, fetchTrainingControl, pauseTraining, startTraining } from './api'
import type { Episode, MetricRow, Run, TrainingControlStatus } from './types'
import { Overview } from './views/Overview'
import { Diagnostics } from './views/Diagnostics'
import { Training } from './views/Training'
import { EMPTY_FILTERS, Episodes, type EpisodeFilters } from './views/Episodes'
import { Replay, type ReplayTarget } from './views/Replay'
import { Live } from './views/Live'
import { Compare } from './views/Compare'
import { AlertBanner, StatusPill, ThemeToggle } from './components/ui'
import { num, pct } from './format'
import { useI18n } from './i18n'

type Tab = 'overview' | 'diagnostics' | 'training' | 'episodes' | 'replay' | 'live' | 'compare'

const NAV: { tab: Tab; icon: typeof BarChart3 }[] = [
  { tab: 'overview', icon: BarChart3 },
  { tab: 'diagnostics', icon: Stethoscope },
  { tab: 'training', icon: Activity },
  { tab: 'episodes', icon: BookOpen },
  { tab: 'replay', icon: Route },
  { tab: 'live', icon: Radio },
  { tab: 'compare', icon: GitCompare },
]

/* Actions whose structural zero is a real bug rather than a strategy choice — the
 * potion regression that hid for five versions was use_potion pinned here. */
const CORE_ACTIONS = ['play_card', 'end_turn', 'use_potion', 'select_map_node', 'select_card_reward']

interface Alert { level: 'critical' | 'warn'; title: string; body?: string }

/** Every tripwire the run history exposes, derived from the latest metric row so
 *  the banner rides on top of every page — the "impossible to miss" mechanism. */
function useTripwires(run: Run | undefined): Alert[] {
  const { t } = useI18n()
  return useMemo(() => {
    if (!run) return []
    const latest = run.latest as MetricRow | undefined
    if (!latest) return []
    const alerts: Alert[] = []
    const vis = latest.visibility

    if (Number(latest.inverted) === 1) {
      alerts.push({ level: 'critical', title: t('alert.inverted'), body: t('alert.invertedBody') })
    }
    if (vis) {
      const breaches: string[] = []
      // `violations` is the authoritative abort list: the backend only records an
      // unknown-entities violation when distinct identities exceed the tolerance
      // budget (a systemic break). The sparse runtime tail (summoned creatures,
      // relic-keyed event options) is reported for review but must not raise a
      // critical "contract breached" banner.
      if (vis.violations?.length) breaches.push(t('alert.visViolations', { count: vis.violations.length }))
      if (Number(vis.nonfinite_features) > 0) breaches.push(t('alert.visNonfinite', { count: vis.nonfinite_features as number }))
      if (vis.pointer_misses && Object.keys(vis.pointer_misses).length) breaches.push(t('alert.visPointer'))
      if (breaches.length) alerts.push({ level: 'critical', title: t('alert.visibility'), body: breaches.join(' · ') })
    }
    const kl = Number(latest.approx_kl)
    const hardKl = Number((run.config as Record<string, unknown>).hard_kl_stop)
    if (Number.isFinite(kl) && Number.isFinite(hardKl) && hardKl > 0 && kl > hardKl) {
      alerts.push({ level: 'warn', title: t('alert.kl'), body: t('alert.klBody', { kl: num(kl, 4), limit: num(hardKl, 4) }) })
    }
    const unreachable = new Set([...(vis?.never_offered_actions ?? []), ...(vis?.never_chosen_actions ?? [])])
    const coreZero = CORE_ACTIONS.filter(action => unreachable.has(action))
    if (coreZero.length) {
      alerts.push({ level: 'warn', title: t('alert.structuralZero'), body: t('alert.structuralZeroBody', { actions: coreZero.join(', ') }) })
    }
    return alerts
  }, [run, t])
}

export default function App() {
  const { t, locale, setLocale } = useI18n()
  const [runs, setRuns] = useState<Run[]>([])
  const [runName, setRunName] = useState('')
  const [tab, setTab] = useState<Tab>('overview')
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<Date | null>(null)
  const [filters, setFilters] = useState<EpisodeFilters>(EMPTY_FILTERS)
  const [replayTarget, setReplayTarget] = useState<ReplayTarget | null>(null)
  const [trainingControl, setTrainingControl] = useState<TrainingControlStatus | null>(null)
  const [controlBusy, setControlBusy] = useState(false)

  const refresh = async () => {
    try {
      const payload = await fetchRuns()
      const next = [...payload.items]
      setRuns(next)
      setRunName(current => current && next.some(run => run.name === current) ? current : next[0]?.name || '')
      setUpdated(new Date())
      setError('')
    } catch (err) {
      setError(String(err))
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => { if (!document.hidden) void refresh() }, 5000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!runName) { setTrainingControl(null); return }
    let cancelled = false
    const refreshControl = async () => {
      try {
        const status = await fetchTrainingControl(runName)
        if (!cancelled) setTrainingControl(status)
      } catch (err) {
        if (!cancelled) setTrainingControl(null)
      }
    }
    void refreshControl()
    const timer = window.setInterval(() => { if (!document.hidden) void refreshControl() }, 3000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [runName])

  const run = runs.find(item => item.name === runName)
  const alerts = useTripwires(run)

  const selectRun = (name: string) => {
    setRunName(name)
    setFilters(EMPTY_FILTERS)
    setReplayTarget(null)
  }

  const openEpisode = (episode: Episode) => {
    setReplayTarget({ episodeId: episode.episode_id, iteration: episode.iteration, split: episode.split })
    setTab('replay')
  }

  const inspectIteration = (iteration: number) => {
    setFilters({ ...EMPTY_FILTERS, iteration: String(iteration) })
    setTab('episodes')
  }

  const running = trainingControl?.state === 'running' || trainingControl?.state === 'starting'
  const toggleTraining = async () => {
    if (!run || controlBusy) return
    setControlBusy(true)
    try {
      const status = running ? await pauseTraining(run.name) : await startTraining(run.name)
      setTrainingControl(status)
      setError('')
      await refresh()
    } catch (err) {
      setError(String(err))
    } finally {
      setControlBusy(false)
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">尖</div>
          <div><b>{t('brand.name')}</b><span>{t('brand.subtitle')}</span></div>
        </div>
        <nav>
          {NAV.map(({ tab: name, icon: Icon }) => (
            <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>
              <Icon />{t(`nav.${name}`)}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="live-dot" />{t('common.autoRefresh')}<br />
          <small>{updated ? t('common.updated', { time: updated.toLocaleTimeString(locale) }) : t('common.connecting')}</small>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div className="run-select">
            <label>{t('run.label')}</label>
            <select value={runName} onChange={event => selectRun(event.target.value)}>
              {runs.map(item => (
                <option key={item.name} value={item.name}>
                  {item.name}{item.episode_count ? ` · ${t('episodes.count', { count: item.episode_count })}` : ''}
                </option>
              ))}
            </select>
          </div>
          {trainingControl && (
            <StatusPill
              state={running ? (trainingControl.state) : 'stopped'}
              label={t(`control.state.${trainingControl.state}`)}
              sub={t('control.iter', { iteration: trainingControl.last_iteration ?? '—' })}
            />
          )}
          <div className="spacer" />
          {run && (
            <button
              className={`training-control ${trainingControl?.state || 'loading'}`}
              disabled={controlBusy || !trainingControl}
              onClick={() => void toggleTraining()}
              title={t(trainingControl?.state === 'stopped' ? 'control.startTip' : 'control.pauseTip')}
            >
              {controlBusy || !trainingControl ? <LoaderCircle className="spin" />
                : running ? <Pause /> : <Play />}
              <b>{controlBusy ? t('control.working') : running ? t('control.pause') : t('control.start')}</b>
            </button>
          )}
          <button className="icon-btn" onClick={() => void refresh()} title={t('common.refresh')} aria-label={t('common.refresh')}>
            <RefreshCw />
          </button>
          <ThemeToggle />
          <button className="icon-btn" onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')} title="Language" aria-label="Language">
            <Languages />
          </button>
        </div>

        {error && <div className="error-banner">{error}</div>}
        {alerts.map((alert, index) => (
          <AlertBanner key={index} level={alert.level} title={alert.title} body={alert.body} />
        ))}

        <main className="page">
          <div className="page-head">
            <h1>{t(`title.${tab}`)}</h1>
            <p>{run ? `${run.name} · ${t(`sub.${tab}`)}` : t(`sub.${tab}`)}</p>
          </div>

          {!run ? (
            <div className="empty">{t('run.notFound')}</div>
          ) : tab === 'overview' ? (
            <Overview run={run} onInspectIteration={inspectIteration} />
          ) : tab === 'diagnostics' ? (
            <Diagnostics run={run} />
          ) : tab === 'training' ? (
            <Training run={run} />
          ) : tab === 'episodes' ? (
            <Episodes run={run} filters={filters} onFilters={setFilters} onOpen={openEpisode} />
          ) : tab === 'replay' ? (
            <Replay run={run} target={replayTarget} onTarget={setReplayTarget} />
          ) : tab === 'live' ? (
            <Live run={run} />
          ) : (
            <Compare runs={runs} current={run.name} />
          )}
        </main>
      </div>
    </div>
  )
}
