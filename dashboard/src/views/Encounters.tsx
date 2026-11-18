import { useEffect, useMemo, useState } from 'react'
import { Crown, LoaderCircle, Skull, Swords } from 'lucide-react'
import { fetchEncounters } from '../api'
import { num, pct } from '../format'
import type { EncounterKind, EncounterRow, EncounterStats, Run } from '../types'
import { Empty, Meter, Panel } from '../components/ui'
import { useI18n } from '../i18n'

/* A run-level win rate says the agent lost; it never says to whom. This page
 * scores every recorded fight and splits it by class, which is where the
 * actionable asymmetry lives: normal monsters sit at ~99% while one elite is a
 * coin flip and one boss eats half the runs that reach it. It is also the
 * cheapest engine-bug detector we have — a boss at 100% with zero HP lost is a
 * fight that never spawned (the KAISER_CRAB free win). */

const KIND_ICONS: Record<EncounterKind, typeof Swords> = {
  monster: Swords, elite: Skull, boss: Crown,
}

/** Fewer fights than this and the win rate is noise, not a measurement. */
const THIN_SAMPLE = 8

type SortKey = 'losses' | 'fights' | 'win_rate' | 'avg_hp_lost'

// Deaths first, not fights first: the question this page answers is "what is
// killing the runs", and a rare encounter that always kills belongs at the top.
const DEFAULT_SORT: SortKey = 'losses'

const WINDOWS = [0, 10, 25, 50]

export function Encounters({ run }: { run: Run }) {
  const { t } = useI18n()
  const [data, setData] = useState<EncounterStats | null>(null)
  const [error, setError] = useState('')
  const [split, setSplit] = useState('dev')
  const [window_, setWindow] = useState(0)
  const [perEnemy, setPerEnemy] = useState(false)
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT)

  const building = data?.progress.building ?? false
  useEffect(() => {
    let cancelled = false
    const load = () => fetchEncounters(run.name, split, window_)
      .then(payload => { if (!cancelled) { setData(payload); setError('') } })
      .catch(err => { if (!cancelled) setError(String(err)) })
    void load()
    // While the background scan is filling the cache the table grows under you;
    // once it is done this settles to the same 5s cadence as the rest of the app.
    const timer = window.setInterval(() => { if (!document.hidden) void load() }, building ? 2000 : 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [run.name, split, window_, building])

  const rows = useMemo(() => {
    if (!data) return {} as Record<EncounterKind, EncounterRow[]>
    const source = perEnemy ? data.enemies : data.encounters
    const kinds: EncounterKind[] = ['monster', 'elite', 'boss']
    return Object.fromEntries(kinds.map(kind => [
      kind, sorted(source.filter(row => row.kind === kind), sort),
    ])) as Record<EncounterKind, EncounterRow[]>
  }, [data, perEnemy, sort])

  if (error) return <Empty text={error} />
  if (!data) return <Empty text={t('common.loading')} />

  const { progress, scope } = data
  return <div className="stack">
    <div className="enc-controls">
      <Segmented value={split} onChange={setSplit} options={[
        { value: 'dev', label: t('episodes.validation') },
        { value: 'train', label: t('episodes.training') },
        { value: '', label: t('enc.allSplits') },
      ]} />
      <Segmented value={String(window_)} onChange={value => setWindow(Number(value))}
                 options={WINDOWS.map(size => ({
                   value: String(size),
                   label: size ? t('enc.lastN', { count: size }) : t('enc.allIterations'),
                 }))} />
      <Segmented value={perEnemy ? 'enemy' : 'group'} onChange={value => setPerEnemy(value === 'enemy')}
                 options={[
                   { value: 'group', label: t('enc.byGroup') },
                   { value: 'enemy', label: t('enc.byEnemy') },
                 ]} />
      <span className="spacer" />
      {building && <span className="enc-progress">
        <LoaderCircle className="spin" />
        {t('enc.scanning', { done: progress.scanned, total: progress.total })}
      </span>}
    </div>

    <div className="enc-scope">
      {t('enc.scope', {
        episodes: scope.episodes, fights: scope.fights,
        from: scope.iteration_from ?? '—', to: scope.iteration_to ?? '—',
      })}
    </div>

    {(['monster', 'elite', 'boss'] as EncounterKind[]).map(kind => {
      const Icon = KIND_ICONS[kind]
      const list = rows[kind] ?? []
      return (
        <Panel key={kind} icon={<Icon />} title={t(`enc.kind.${kind}`)} tip={t(`enc.tip.${kind}`)}
               extra={t('enc.rowCount', { count: list.length })}>
          {list.length
            ? <EncounterTable rows={list} sort={sort} onSort={setSort} />
            : <Empty text={progress.building ? t('enc.scanningEmpty') : t('common.noData')} />}
        </Panel>
      )
    })}
  </div>
}

function EncounterTable({ rows, sort, onSort }: {
  rows: EncounterRow[]; sort: SortKey; onSort: (key: SortKey) => void
}) {
  const { t } = useI18n()
  const columns: { key: SortKey; label: string; tip?: string }[] = [
    { key: 'fights', label: t('enc.col.fights') },
    { key: 'win_rate', label: t('enc.col.winRate') },
    { key: 'losses', label: t('enc.col.losses') },
    { key: 'avg_hp_lost', label: t('enc.col.hpLost') },
  ]
  return (
    <div className="table-wrap">
      <table className="enc-table">
        <thead>
          <tr>
            <th>{t('enc.col.encounter')}</th>
            {columns.map(column => (
              <th key={column.key} className="sortable">
                <button type="button" className={sort === column.key ? 'active' : ''}
                        onClick={() => onSort(column.key)}>{column.label}</button>
              </th>
            ))}
            <th>{t('enc.col.hpIn')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} className={row.fights < THIN_SAMPLE ? 'thin' : ''}
                title={row.fights < THIN_SAMPLE ? t('enc.thinSample', { count: THIN_SAMPLE }) : undefined}>
              <td>
                <b>{row.label}</b>
                {row.acts.length > 0 && <small>{t('enc.acts', { acts: row.acts.join(', ') })}</small>}
              </td>
              <td className="num">{row.fights}</td>
              <td className="win-cell">
                <Meter value={row.win_rate ?? 0} tone={winTone(row.win_rate)} />
                <span className="num">{pct(row.win_rate)}</span>
              </td>
              <td className="num">{row.losses || '—'}</td>
              <td className="num">{row.avg_hp_lost === null ? '—' : num(row.avg_hp_lost, 1)}</td>
              <td className="num dim">{row.avg_hp_in === null ? '—' : num(row.avg_hp_in, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Reusable pill switch — same shape as StagePicker but for arbitrary values. */
function Segmented({ value, onChange, options }: {
  value: string; onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="stage-picker" role="tablist">
      {options.map(option => (
        <button key={option.value} role="tab" aria-selected={value === option.value}
                className={value === option.value ? 'active' : ''}
                onClick={() => onChange(option.value)}>{option.label}</button>
      ))}
    </div>
  )
}

function winTone(rate: number | null): 'good' | 'warn' | 'accent' {
  if (rate === null) return 'accent'
  if (rate >= 0.9) return 'good'
  if (rate >= 0.65) return 'accent'
  return 'warn'
}

export function sorted(rows: EncounterRow[], key: SortKey): EncounterRow[] {
  // Win rate ascending (worst first) because the interesting end of that column
  // is the bottom; every other column reads worst-first when descending.
  const ascending = key === 'win_rate'
  return [...rows].sort((a, b) => {
    const left = a[key] ?? (ascending ? 1 : -1)
    const right = b[key] ?? (ascending ? 1 : -1)
    if (left !== right) return ascending ? left - right : right - left
    return b.fights - a.fights || a.label.localeCompare(b.label)
  })
}
