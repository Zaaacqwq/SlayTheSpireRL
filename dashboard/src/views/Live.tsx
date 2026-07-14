import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowLeft, Pause, Play, Terminal, Trash2, Users } from 'lucide-react'
import { fetchLiveEvents, fetchLiveWorkers } from '../api'
import { num, stageLabel } from '../format'
import { useI18n } from '../i18n'
import type { LiveEvent, LiveSnapshot, LiveWorker, Run } from '../types'
import { Empty, InfoTip } from '../components/ui'

const EMPTY: LiveSnapshot = {
  enabled: false, session_id: null, updated_at: null, worker_count: 0,
  dropped_events: 0, stale: false, workers: [],
}

export function Live({ run }: { run: Run }) {
  const { t, locale } = useI18n()
  const [snapshot, setSnapshot] = useState<LiveSnapshot>(EMPTY)
  const [selected, setSelected] = useState<number | null>(null)

  useEffect(() => {
    setSelected(null)
    let cancelled = false
    const refresh = () => void fetchLiveWorkers(run.name)
      .then(data => { if (!cancelled) setSnapshot(data) })
      .catch(() => { if (!cancelled) setSnapshot(value => ({ ...value, stale: true })) })
    refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [run.name])

  if (!snapshot.enabled) return (
    <section className="panel live-disabled">
      <Terminal />
      <div><h2>{t('live.disabledTitle')}</h2><p>{t('live.disabledBody')}</p></div>
    </section>
  )
  if (selected !== null) {
    const worker = snapshot.workers.find(item => item.worker_id === selected)
    return <WorkerConsole run={run} workerId={selected} worker={worker}
                          sessionId={snapshot.session_id} stale={snapshot.stale}
                          onBack={() => setSelected(null)} />
  }

  const active = snapshot.workers.filter(worker => ['running', 'starting'].includes(worker.status)).length
  return <>
    <div className="live-summary">
      <span><Users />{t('live.workers', { active, total: snapshot.worker_count })}</span>
      <span><Activity />{t('live.rate', { rate: num(snapshot.action_rate ?? 0, 1) })}</span>
      {snapshot.dropped_events > 0 && <span className="warn">{t('live.dropped', { count: snapshot.dropped_events })}</span>}
      {snapshot.stale && <span className="warn">{t('live.stale')}</span>}
    </div>
    <div className="worker-grid">
      {Array.from({ length: snapshot.worker_count }, (_, workerId) => {
        const worker = snapshot.workers.find(item => item.worker_id === workerId)
        return <WorkerCard key={workerId} workerId={workerId} worker={worker}
                           locale={locale} onClick={() => setSelected(workerId)} />
      })}
    </div>
  </>
}

function WorkerCard({ workerId, worker, locale, onClick }: {
  workerId: number; worker?: LiveWorker; locale: 'zh-CN' | 'en-US'; onClick: () => void
}) {
  const { t } = useI18n()
  const status = worker?.status ?? 'idle'
  return (
    <button className={`worker-card status-${status}`} onClick={onClick}>
      <div className="worker-head">
        <b>{t('live.worker', { id: workerId + 1 })}</b>
        <span className="worker-status"><i />{t(`status.${status}`)}</span>
      </div>
      <div className="worker-meta">
        <span>{worker?.stage ? stageLabel(worker.stage, locale) : '—'}</span>
        <span>{worker?.iteration !== undefined ? t('common.iteration', { value: worker.iteration }) : '—'}</span>
      </div>
      <strong>{worker?.seed ?? t('live.waiting')}</strong>
      <div className="worker-position">
        <span>{t('live.floorRound', { floor: worker?.floor ?? '—', round: worker?.round ?? '—' })}</span>
        <span>{t('live.step', { step: worker?.step ?? 0 })}</span>
      </div>
      <p><small>{t('live.lastAction')}</small>{worker?.action ? readableAction(worker, locale) : '—'}</p>
      <footer><span>{num(worker?.action_rate ?? 0, 1)} actions/s</span><span>{t('live.inspect')} →</span></footer>
    </button>
  )
}

function WorkerConsole({ run, workerId, worker, sessionId, stale, onBack }: {
  run: Run; workerId: number; worker?: LiveWorker; sessionId: string | null
  stale: boolean; onBack: () => void
}) {
  const { t, locale } = useI18n()
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [cursor, setCursor] = useState<number | undefined>(undefined)
  const [knownSession, setKnownSession] = useState(sessionId)
  const [paused, setPaused] = useState(false)
  const [autoscroll, setAutoscroll] = useState(true)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setEvents([]); setCursor(undefined); setKnownSession(sessionId); setPaused(false)
  }, [run.name, workerId, sessionId])

  useEffect(() => {
    if (paused) return
    let cancelled = false
    const refresh = () => void fetchLiveEvents(run.name, workerId, cursor)
      .then(data => {
        if (cancelled) return
        if (knownSession !== data.session_id) {
          setKnownSession(data.session_id); setEvents(data.items); setCursor(data.next_after)
        } else if (data.items.length) {
          setEvents(current => [...current, ...data.items].slice(-1000))
          setCursor(data.next_after)
        }
      }).catch(() => undefined)
    refresh()
    const timer = window.setInterval(refresh, 400)
    return () => { cancelled = true; clearInterval(timer) }
  }, [run.name, workerId, cursor, knownSession, paused])

  useEffect(() => {
    if (autoscroll && !paused && typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [events, autoscroll, paused])

  return <section className="worker-console">
    <div className="console-toolbar">
      <button onClick={onBack}><ArrowLeft />{t('live.back')}</button>
      <div>
        <b>{t('live.worker', { id: workerId + 1 })}</b>
        <span className={`worker-status status-${worker?.status ?? 'idle'}`}><i />{t(`status.${worker?.status ?? 'idle'}`)}</span>
      </div>
      <span className="spacer" />
      {stale && <span className="warn">{t('live.stale')}</span>}
      <button onClick={() => setPaused(value => !value)}>{paused ? <Play /> : <Pause />}{paused ? t('live.resume') : t('live.pause')}</button>
      <label className="auto-scroll"><input type="checkbox" checked={autoscroll} onChange={event => setAutoscroll(event.target.checked)} />{t('live.autoscroll')}</label>
      <button onClick={() => setEvents([])}><Trash2 />{t('live.clear')}</button>
    </div>
    <div className="console-context">
      <span>{worker?.seed ?? '—'}</span>
      <span>{worker?.stage ? stageLabel(worker.stage, locale) : '—'}</span>
      <span>{worker?.iteration !== undefined ? t('common.iteration', { value: worker.iteration }) : '—'}</span>
      <span>{t('live.floorRound', { floor: worker?.floor ?? '—', round: worker?.round ?? '—' })}</span>
    </div>
    <div className="console-stream">
      {events.length ? events.map(event => <EventLine key={`${event.session_id}-${event.seq}`} event={event} />)
        : <Empty text={t('live.noEvents')} />}
      <div ref={endRef} />
    </div>
  </section>
}

function EventLine({ event }: { event: LiveEvent }) {
  const { t, locale } = useI18n()
  const time = new Date(event.timestamp).toLocaleTimeString(locale, { hour12: false })
  const isAction = event.type === 'action'
  const heading = isAction ? readableAction(event, locale)
    : event.type === 'episode_start' ? `${locale === 'en-US' ? 'Episode started' : '开始对局'} · ${event.seed}`
      : event.type === 'episode_end' ? `${locale === 'en-US' ? 'Episode ended' : '对局结束'} · ${event.outcome ? t('common.win') : t('common.loss')}`
        : event.type === 'episode_error' ? `${t('status.error')} · ${event.error}` : t(`status.${event.status}`)
  return <article className={`console-event event-${event.type}`}>
    <time>{time}</time><span className="seq">#{event.seq}</span>
    <div><b>{heading}</b><small>{event.phase ?? ''} · {t('live.step', { step: event.step ?? 0 })}</small></div>
    {isAction && <div className="event-metrics">
      <span>r {num(event.reward, 3)}<InfoTip text={t('tip.reward')} /></span>
      <span>V {num(event.value, 3)}<InfoTip text={t('tip.value')} /></span>
      <span>log p {num(event.logp, 3)}<InfoTip text={t('tip.logp')} /></span>
    </div>}
    <details><summary>{t('live.rawCommand')}</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
  </article>
}

function readableAction(event: LiveWorker, locale: 'zh-CN' | 'en-US'): string {
  const action = event.action?.action ?? 'unknown'
  const zh: Record<string, string> = {
    play_card: '打出', end_turn: '结束回合', use_potion: '使用药水', select_map_node: '选择路线',
    select_card_reward: '选择卡牌奖励', skip_card_reward: '跳过卡牌奖励', choose_option: '选择选项',
    select_bundle: '选择组合', select_cards: '选择卡牌', skip_select: '跳过选择', buy_card: '购买卡牌',
    buy_relic: '购买遗物', buy_potion: '购买药水', remove_card: '移除卡牌', leave_room: '离开房间',
  }
  const en: Record<string, string> = {
    play_card: 'Play', end_turn: 'End turn', use_potion: 'Use potion', select_map_node: 'Choose route',
    select_card_reward: 'Take card reward', skip_card_reward: 'Skip card reward', choose_option: 'Choose option',
    select_bundle: 'Choose bundle', select_cards: 'Select cards', skip_select: 'Skip selection', buy_card: 'Buy card',
    buy_relic: 'Buy relic', buy_potion: 'Buy potion', remove_card: 'Remove card', leave_room: 'Leave room',
  }
  const verb = (locale === 'en-US' ? en : zh)[action] ?? action.replaceAll('_', ' ')
  const selected = event.selected_label ? ` ${event.selected_label}` : ''
  const target = event.target !== null && event.target !== undefined
    ? ` → ${locale === 'en-US' ? 'target' : '目标'} ${event.target}` : ''
  return `${verb}${selected}${target}`
}
