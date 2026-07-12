import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { fetchEpisodes, fetchTimeline } from '../api'
import { num, stageLabel } from '../format'
import type { Episode, Run, TimelineItem } from '../types'
import { Empty, OutcomeBadge, RoomIcon } from '../components/ui'

const PAGE_SIZE = 30
const ACT1_TOP_FLOOR = 17

export interface EpisodeFilters {
  search: string
  split: string
  outcome: string
  iteration: string
}

export const EMPTY_FILTERS: EpisodeFilters = { search: '', split: '', outcome: '', iteration: '' }

export function Episodes({ run, filters, onFilters, onOpen }: {
  run: Run
  filters: EpisodeFilters
  onFilters: (filters: EpisodeFilters) => void
  onOpen: (episode: Episode) => void
}) {
  const [items, setItems] = useState<Episode[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [timeline, setTimeline] = useState<TimelineItem[]>([])

  useEffect(() => { setPage(1) }, [run.name, filters])
  // Effects key on `run` (fresh object per 5s poll) so transient failures heal.
  useEffect(() => {
    void fetchEpisodes(run.name, { page, pageSize: PAGE_SIZE, ...filters })
      .then(data => { setItems(data.items); setTotal(data.total) })
      .catch(() => { setItems([]); setTotal(0) })
  }, [run, page, filters])
  useEffect(() => {
    if (run.name === 'legacy') { setTimeline([]); return }
    void fetchTimeline(run.name).then(data => setTimeline(data.items)).catch(() => setTimeline([]))
  }, [run])

  const iterations = useMemo(
    () => [...new Set(timeline.map(item => item.iteration).filter((value): value is number => value !== null))],
    [timeline])
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section className="panel">
      <div className="filter-row">
        <div style={{ position: 'relative' }}>
          <Search style={{ position: 'absolute', left: 8, top: 8, width: 14, height: 14, color: 'var(--ink-3)' }} />
          <input
            style={{ paddingLeft: 28 }}
            value={filters.search}
            onChange={event => onFilters({ ...filters, search: event.target.value })}
            placeholder="搜索 seed"
          />
        </div>
        <select value={filters.iteration} onChange={event => onFilters({ ...filters, iteration: event.target.value })}>
          <option value="">全部迭代</option>
          {iterations.map(iteration => <option key={iteration} value={String(iteration)}>迭代 {iteration}</option>)}
        </select>
        <select value={filters.split} onChange={event => onFilters({ ...filters, split: event.target.value })}>
          <option value="">全部来源</option>
          <option value="train">训练</option>
          <option value="dev">验证</option>
          <option value="replay">回放</option>
        </select>
        <select value={filters.outcome} onChange={event => onFilters({ ...filters, outcome: event.target.value })}>
          <option value="">全部结果</option>
          <option value="win">胜利</option>
          <option value="loss">失败</option>
        </select>
        <div className="spacer" />
        <span className="count">{total.toLocaleString()} 场对局</span>
      </div>

      {items.length ? (
        <table className="ep-table">
          <thead>
            <tr>
              <th>结果</th><th>对局 / SEED</th><th>阶段</th><th>路线</th>
              <th>楼层</th><th>血量</th><th>奖励</th><th>步数</th>
            </tr>
          </thead>
          <tbody>
            {items.map(episode => (
              <tr key={`${episode.path ?? episode.episode_id}-${episode.iteration}`}
                  onClick={() => onOpen(episode)}>
                <td><OutcomeBadge outcome={episode.outcome} /></td>
                <td>
                  <b>{episode.episode_id}</b>
                  <small>
                    {episode.split === 'replay' ? '回放' : episode.split === 'dev' ? '验证' : episode.split ?? ''}
                    {episode.iteration !== null && episode.iteration !== undefined ? ` · 迭代 ${episode.iteration}` : ''}
                  </small>
                </td>
                <td>{stageLabel(episode.stage)}</td>
                <td><RouteStrip episode={episode} /></td>
                <td><FloorBar floor={episode.final_floor} /></td>
                <td className="num">{episode.final_hp ?? '—'}{episode.max_hp ? ` / ${episode.max_hp}` : ''}</td>
                <td className="num">{num(episode.total_reward)}</td>
                <td className="num">{episode.steps}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <Empty text="没有匹配的对局" />}

      <div className="pager">
        <button disabled={page === 1} onClick={() => setPage(page - 1)} aria-label="上一页"><ChevronLeft /></button>
        <span>第 {page} / {pages} 页</span>
        <button disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="下一页"><ChevronRight /></button>
      </div>
    </section>
  )
}

function RouteStrip({ episode }: { episode: Episode }) {
  const route = episode.route ?? []
  if (!route.length) return <span style={{ color: 'var(--ink-3)' }}>—</span>
  return (
    <span className="route-strip" title={route.map(point => `${point.floor ?? '?'}F ${point.room_type ?? ''}`).join(' → ')}>
      {route.slice(0, 12).map((point, index) => (
        <span key={index} className={point.room_type?.toLowerCase().includes('boss') ? 'boss' : ''}>
          <RoomIcon room={point.room_type} />
        </span>
      ))}
      {route.length > 12 && <span style={{ fontSize: 10, color: 'var(--ink-3)' }}>+{route.length - 12}</span>}
    </span>
  )
}

function FloorBar({ floor }: { floor?: number | null }) {
  if (typeof floor !== 'number') return <span style={{ color: 'var(--ink-3)' }}>—</span>
  const ratio = Math.min(1, floor / ACT1_TOP_FLOOR)
  return (
    <span className="floor-bar">
      <span className="track"><span className="fill" style={{ width: `${ratio * 100}%` }} /></span>
      <span className="num">{num(floor, 0)}</span>
    </span>
  )
}
