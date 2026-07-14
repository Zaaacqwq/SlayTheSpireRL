import { useEffect, useState } from 'react'
import { BarChart3, BookOpen, Languages, Radio, RefreshCw, Route, Stethoscope } from 'lucide-react'
import { fetchRuns } from './api'
import type { Episode, Run } from './types'
import { Overview } from './views/Overview'
import { Diagnostics } from './views/Diagnostics'
import { EMPTY_FILTERS, Episodes, type EpisodeFilters } from './views/Episodes'
import { Replay, type ReplayTarget } from './views/Replay'
import { Live } from './views/Live'
import { useI18n } from './i18n'

type Tab = 'overview' | 'diagnostics' | 'episodes' | 'replay' | 'live'

const LEGACY_RUN = (episodeCount: number): Run => ({
  name: 'legacy', config: {}, history_count: 0, episode_count: episodeCount, checkpoints: 0,
  latest: { iteration: 0 }, best: { iteration: 0 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: false, episodes: true },
})

export default function App() {
  const { t, locale, setLocale } = useI18n()
  const [runs, setRuns] = useState<Run[]>([])
  const [runName, setRunName] = useState('')
  const [tab, setTab] = useState<Tab>('overview')
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<Date | null>(null)
  const [filters, setFilters] = useState<EpisodeFilters>(EMPTY_FILTERS)
  const [replayTarget, setReplayTarget] = useState<ReplayTarget | null>(null)

  const refresh = async () => {
    try {
      const payload = await fetchRuns()
      const next = [...payload.items]
      if (payload.legacy_episode_count) next.push(LEGACY_RUN(payload.legacy_episode_count))
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

  const run = runs.find(item => item.name === runName)
  const trainingRuns = runs.filter(item => item.name !== 'legacy')
  const legacy = runs.find(item => item.name === 'legacy')
  const title = t(`title.${tab}`)
  const subtitle = t(`sub.${tab}`)

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

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark">尖</div>
          <div><b>{t('brand.name')}</b><span>{t('brand.subtitle')}</span></div>
        </div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>
            <BarChart3 />{t('nav.overview')}
          </button>
          <button className={tab === 'diagnostics' ? 'active' : ''} onClick={() => setTab('diagnostics')}>
            <Stethoscope />{t('nav.diagnostics')}
          </button>
          <button className={tab === 'episodes' ? 'active' : ''} onClick={() => setTab('episodes')}>
            <BookOpen />{t('nav.episodes')}
          </button>
          <button className={tab === 'replay' ? 'active' : ''} onClick={() => setTab('replay')}>
            <Route />{t('nav.replay')}
          </button>
          <button className={tab === 'live' ? 'active' : ''} onClick={() => setTab('live')}>
            <Radio />{t('nav.live')}
          </button>
        </nav>
        <div className="run-picker">
          <label>{t('run.label')}</label>
          <select value={runName} onChange={event => selectRun(event.target.value)}>
            <optgroup label={t('run.group')}>
              {trainingRuns.map(item => (
                <option key={item.name} value={item.name}>
                  {item.name}{item.episode_count ? ` · ${t('episodes.count', { count: item.episode_count })}` : ''}
                </option>
              ))}
            </optgroup>
            {legacy && (
              <optgroup label={t('run.legacy')}>
                <option value="legacy">legacy · {t('episodes.count', { count: legacy.episode_count })}</option>
              </optgroup>
            )}
          </select>
        </div>
        <div className="rail-foot">
          <button className="locale-toggle" onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}>
            <Languages />{locale === 'zh-CN' ? 'EN' : '中文'}
          </button>
          <span className="live-dot" />{t('common.autoRefresh')}<br />
          <small>{updated ? t('common.updated', { time: updated.toLocaleTimeString(locale) }) : t('common.connecting')}</small>
        </div>
      </aside>

      <main className="content">
        <div className="view-head">
          <div><h1>{title}</h1><p>{run ? `${run.name} · ${subtitle}` : subtitle}</p></div>
          <button className="ckpt-chip" onClick={() => void refresh()}>
            <RefreshCw style={{ width: 13, height: 13, verticalAlign: -2, marginRight: 6 }} />{t('common.refresh')}
          </button>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {!run ? (
          <div className="empty">{t('run.notFound')}</div>
        ) : tab === 'overview' ? (
          <Overview run={run} onInspectIteration={inspectIteration} />
        ) : tab === 'diagnostics' ? (
          <Diagnostics run={run} />
        ) : tab === 'episodes' ? (
          <Episodes run={run} filters={filters} onFilters={setFilters} onOpen={openEpisode} />
        ) : tab === 'replay' ? (
          <Replay run={run} target={replayTarget} onTarget={setReplayTarget} />
        ) : (
          <Live run={run} />
        )}
      </main>
    </div>
  )
}
