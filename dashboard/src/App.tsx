import { useEffect, useState } from 'react'
import { BarChart3, BookOpen, RefreshCw, Route } from 'lucide-react'
import { fetchRuns } from './api'
import type { Episode, Run } from './types'
import { Overview } from './views/Overview'
import { EMPTY_FILTERS, Episodes, type EpisodeFilters } from './views/Episodes'
import { Replay, type ReplayTarget } from './views/Replay'

type Tab = 'overview' | 'episodes' | 'replay'

const LEGACY_RUN = (episodeCount: number): Run => ({
  name: 'legacy', config: {}, history_count: 0, episode_count: episodeCount, checkpoints: 0,
  latest: { iteration: 0 }, best: { iteration: 0 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: false, episodes: true },
})

const TAB_TITLES: Record<Tab, [string, string]> = {
  overview: ['训练总览', '学习曲线、课程阶段与已录对局'],
  episodes: ['对局档案', '每一局的结果、路线与数据'],
  replay: ['对局复盘', '逐步回放：卡池、选择与战斗过程'],
}

export default function App() {
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
  const [title, subtitle] = TAB_TITLES[tab]

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
          <div><b>尖塔 RL</b><span>训练观测台</span></div>
        </div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>
            <BarChart3 />训练总览
          </button>
          <button className={tab === 'episodes' ? 'active' : ''} onClick={() => setTab('episodes')}>
            <BookOpen />对局档案
          </button>
          <button className={tab === 'replay' ? 'active' : ''} onClick={() => setTab('replay')}>
            <Route />对局复盘
          </button>
        </nav>
        <div className="run-picker">
          <label>训练实验</label>
          <select value={runName} onChange={event => selectRun(event.target.value)}>
            <optgroup label="训练实验">
              {trainingRuns.map(item => (
                <option key={item.name} value={item.name}>
                  {item.name}{item.episode_count ? ` · ${item.episode_count}局` : ''}
                </option>
              ))}
            </optgroup>
            {legacy && (
              <optgroup label="历史轨迹">
                <option value="legacy">legacy · {legacy.episode_count} 局</option>
              </optgroup>
            )}
          </select>
        </div>
        <div className="rail-foot">
          <span className="live-dot" />每 5 秒自动刷新<br />
          <small>{updated ? `已同步 ${updated.toLocaleTimeString()}` : '连接中…'}</small>
        </div>
      </aside>

      <main className="content">
        <div className="view-head">
          <div><h1>{title}</h1><p>{run ? `${run.name} · ${subtitle}` : subtitle}</p></div>
          <button className="ckpt-chip" onClick={() => void refresh()}>
            <RefreshCw style={{ width: 13, height: 13, verticalAlign: -2, marginRight: 6 }} />刷新
          </button>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {!run ? (
          <div className="empty">未发现训练记录：确认 rl/runs 下有实验目录</div>
        ) : tab === 'overview' ? (
          <Overview run={run} onInspectIteration={inspectIteration} />
        ) : tab === 'episodes' ? (
          <Episodes run={run} filters={filters} onFilters={setFilters} onOpen={openEpisode} />
        ) : (
          <Replay run={run} target={replayTarget} onTarget={setReplayTarget} />
        )}
      </main>
    </div>
  )
}
