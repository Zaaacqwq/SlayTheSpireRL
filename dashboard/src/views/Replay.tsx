import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Route } from 'lucide-react'
import { fetchEpisodeDetail, fetchEpisodes, fetchTimeline } from '../api'
import { num, outcomeLabel, stageLabel } from '../format'
import type { Episode, ReplayStep, Run, TimelineItem } from '../types'
import { DeckRail, EnemyCard, GameCard, PlayerStrip, StateDelta } from '../components/replay'
import { Empty, OutcomeBadge, Panel, RoomIcon, roomLabel } from '../components/ui'

export interface ReplayTarget {
  episodeId: string
  iteration?: number | null
  split?: string
}

export function Replay({ run, target, onTarget }: {
  run: Run
  target: ReplayTarget | null
  onTarget: (target: ReplayTarget) => void
}) {
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [rows, setRows] = useState<ReplayStep[]>([])
  const [selected, setSelected] = useState(0)

  const iteration = target?.iteration ?? null

  useEffect(() => {
    if (run.name === 'legacy') { setTimeline([]); return }
    void fetchTimeline(run.name).then(data => setTimeline(data.items)).catch(() => setTimeline([]))
  }, [run])

  useEffect(() => {
    const query = iteration !== null ? { iteration: String(iteration) } : {}
    void fetchEpisodes(run.name, { pageSize: 200, ...query }).then(data => {
      setEpisodes(data.items)
      const current = target?.episodeId
      if (!current || !data.items.some(item => item.episode_id === current)) {
        const first = data.items[0]
        if (first) onTarget({ episodeId: first.episode_id, iteration: first.iteration, split: first.split })
      }
    }).catch(() => setEpisodes([]))
    // `run` (not run.name) so the 5s poll retries after a transient fetch failure.
  }, [run, iteration])

  useEffect(() => {
    if (!target?.episodeId) { setRows([]); return }
    void fetchEpisodeDetail(run.name, target.episodeId, target.iteration, target.split)
      .then(data => { setRows(data.rows); setSelected(0) })
      .catch(() => setRows([]))
  }, [run.name, target?.episodeId, target?.iteration, target?.split])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') setSelected(value => Math.max(0, value - 1))
      if (event.key === 'ArrowRight') setSelected(value => Math.min(rows.length - 1, value + 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows.length])

  const iterations = useMemo(
    () => [...new Set(timeline.map(item => item.iteration).filter((value): value is number => value !== null))],
    [timeline])
  const rooms = useMemo(() => groupRooms(rows), [rows])
  const activeRoom = useMemo(() => {
    let index = 0
    rooms.forEach((room, roomIndex) => { if (selected >= room.index) index = roomIndex })
    return index
  }, [rooms, selected])

  const step = rows[selected]
  const next = rows[selected + 1]
  const episode = episodes.find(item =>
    item.episode_id === target?.episodeId &&
    (target?.iteration === undefined || item.iteration === target?.iteration))

  return <>
    <div className="replay-bar">
      {iterations.length > 0 && <>
        <label>checkpoint</label>
        <select
          value={iteration ?? ''}
          onChange={event => onTarget({
            episodeId: target?.episodeId ?? '',
            iteration: event.target.value === '' ? null : Number(event.target.value),
          })}>
          <option value="">全部迭代</option>
          {iterations.map(value => <option key={value} value={value}>迭代 {value}</option>)}
        </select>
      </>}
      <label>对局</label>
      <select
        value={target?.episodeId ?? ''}
        onChange={event => {
          const picked = episodes.find(item => item.episode_id === event.target.value)
          if (picked) onTarget({ episodeId: picked.episode_id, iteration: picked.iteration, split: picked.split })
        }}>
        {episodes.map(item => (
          <option key={`${item.path ?? item.episode_id}`} value={item.episode_id}>
            {item.episode_id} · {outcomeLabel(item.outcome)} · {num(item.final_floor, 0)}层
          </option>
        ))}
      </select>
      {episode && <OutcomeBadge outcome={episode.outcome} />}
      {episode && <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>
        {stageLabel(episode.stage)}{episode.iteration !== null ? ` · 迭代 ${episode.iteration}` : ''}
      </span>}
    </div>

    {!step ? <Empty text="暂无可复盘数据" /> : (
      <div className="replay-grid">
        <Panel icon={<Route />} title="路线">
          <div className="route-rail">
            {rooms.map((room, index) => (
              <button key={room.index} className={`room ${activeRoom === index ? 'active' : ''}`}
                      onClick={() => setSelected(room.index)}>
                <RoomIcon room={room.step.room_type} />
                <span>
                  <b>{room.step.floor ?? '—'}层 · {roomLabel(room.step.room_type) || room.step.decision}</b>
                  <small>第 {room.index + 1} 步起</small>
                </span>
              </button>
            ))}
          </div>
        </Panel>

        <section>
          <div className="step-head">
            <div>
              <p>第 {selected + 1} 步 · 回合 {step.round ?? '—'} · 第 {step.act ?? '—'} 幕 · {step.floor ?? '—'} 层</p>
              <h2>{decisionHeading(step)}</h2>
            </div>
            <div className="step-nav">
              <button aria-label="上一步" disabled={selected === 0}
                      onClick={() => setSelected(selected - 1)}><ChevronLeft /></button>
              <span>{selected + 1} / {rows.length}</span>
              <button aria-label="下一步" disabled={selected === rows.length - 1}
                      onClick={() => setSelected(selected + 1)}><ChevronRight /></button>
            </div>
          </div>
          <input className="scrubber" type="range" aria-label="复盘进度"
                 min={0} max={Math.max(0, rows.length - 1)} value={selected}
                 onChange={event => setSelected(Number(event.target.value))} />
          <PlayerStrip step={step} />
          <StateDelta step={step} next={next} />
          <ChoiceBoard step={step} />
          <div className="action-line">
            <div><span>实际选择</span><b>{actionLabel(step)}</b></div>
            <div><span>奖励</span><b className="num">{num(step.reward, 3)}</b></div>
            <div><span>价值估计</span><b className="num">{num(step.value, 3)}</b></div>
            <div><span>log&nbsp;p</span><b className="num">{num(step.logp, 3)}</b></div>
          </div>
          <PileRow step={step} />
          <details>
            <summary>原始状态 JSON</summary>
            <pre className="raw">{JSON.stringify(step.state, null, 2)}</pre>
          </details>
        </section>

        <DeckRail step={step} />
      </div>
    )}
  </>
}

interface RoomGroup { step: ReplayStep; index: number }

function groupRooms(rows: ReplayStep[]): RoomGroup[] {
  const rooms: RoomGroup[] = []
  rows.forEach((row, index) => {
    if (row.room_type === 'Map') return
    const prior = rooms[rooms.length - 1]?.step
    if (!prior || prior.floor !== row.floor || prior.room_type !== row.room_type) {
      rooms.push({ step: row, index })
    }
  })
  return rooms
}

function decisionHeading(step: ReplayStep): string {
  switch (step.decision) {
    case 'combat_play': return '战斗'
    case 'card_reward': return '卡牌奖励（卡池选择）'
    case 'card_select': return '选牌'
    case 'event_choice': return '事件抉择'
    case 'map_select': return '地图路线选择'
    case 'rest_site': return '休息点'
    default: return roomLabel(step.room_type) || step.decision || '决策'
  }
}

function actionLabel(step: ReplayStep): string {
  const action = step.action?.action || 'unknown'
  const picked = (step.selected as any)?.name || (step.selected as any)?.title
  const target = step.action?.args?.target_index
  return `${action.replaceAll('_', ' ')}${picked ? ` · ${picked}` : ''}${
    target !== undefined && target !== null ? ` → 目标 ${target}` : ''}`
}

function ChoiceBoard({ step }: { step: ReplayStep }) {
  const isCombat = Boolean(step.hand?.length || step.enemies?.length)
  const offered = step.hand?.length ? step.hand : step.cards?.length ? step.cards : step.options ?? []
  const selectedIndex = (step.selected as any)?.index
  return (
    <div className="board">
      {isCombat && (
        <div>
          <h3>敌人与意图</h3>
          <div className="entity-grid">
            {step.enemies?.length
              ? step.enemies.map((enemy: any, index: number) => <EnemyCard key={index} enemy={enemy} />)
              : <Empty text="无敌人" />}
          </div>
        </div>
      )}
      <div style={isCombat ? undefined : { gridColumn: '1 / -1' }}>
        <h3>{step.hand?.length ? '手牌' : step.cards?.length ? '可选卡池' : step.options?.length ? '可选项' : step.choices?.length ? '可选路线' : '可选动作'}</h3>
        <div className="card-grid">
          {offered.map((card: any, index: number) => (
            <GameCard key={index} card={card} option={!step.hand?.length}
                      selected={selectedIndex !== undefined && card.index === selectedIndex} />
          ))}
          {!offered.length && step.choices?.length
            ? step.choices.map((choice: any, index: number) => (
                <GameCard key={index} option card={{
                  name: `${roomLabel(choice.type)} (${choice.col ?? '?'}, ${choice.row ?? '?'})`,
                  description: '',
                }} />
              ))
            : null}
          {!offered.length && !step.choices?.length && <Empty text="此步无可选项" />}
        </div>
      </div>
    </div>
  )
}

function PileRow({ step }: { step: ReplayStep }) {
  const state = step.state as any
  if (!state) return null
  return (
    <div className="pile-row">
      <span>抽牌堆 {state.draw_pile_count ?? '—'}</span>
      <span>弃牌堆 {state.discard_pile_count ?? '—'}</span>
      {step.terminated && <span>本局结束 · {outcomeLabel(step.outcome)}</span>}
    </div>
  )
}
