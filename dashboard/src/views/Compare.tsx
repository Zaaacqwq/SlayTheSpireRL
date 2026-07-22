import { useEffect, useMemo, useState } from 'react'
import { fetchMetrics } from '../api'
import { pct } from '../format'
import type { MetricRow, Run } from '../types'
import { MetricChart, SERIES_ORDER, type SeriesSpec } from '../components/charts'
import { Empty, Panel } from '../components/ui'
import { useI18n } from '../i18n'

/* The project's whole history is "diagnose a blind spot → change code → retrain
 * from scratch," so the versions only make sense side by side. Each run's /metrics
 * is fetched and overlaid on a shared iteration axis. */

const OVERLAY_METRICS = ['train_win_rate', 'dev_win_rate', 'reached_boss_rate', 'boss_conversion']
const MAX_RUNS = 4

export function Compare({ runs, current }: { runs: Run[]; current: string }) {
  const { t } = useI18n()
  const [selected, setSelected] = useState<string[]>(() => [current].filter(Boolean))
  const [metric, setMetric] = useState('dev_win_rate')
  const [data, setData] = useState<Record<string, MetricRow[]>>({})

  useEffect(() => {
    let cancelled = false
    for (const name of selected) {
      if (data[name]) continue
      void fetchMetrics(name)
        .then(payload => { if (!cancelled) setData(current => ({ ...current, [name]: payload.rows })) })
        .catch(() => undefined)
    }
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const toggle = (name: string) => setSelected(current =>
    current.includes(name) ? current.filter(item => item !== name)
      : current.length >= MAX_RUNS ? current : [...current, name])

  const colorOf = (name: string) => SERIES_ORDER[selected.indexOf(name) % SERIES_ORDER.length]

  // Merge every selected run's chosen metric onto one iteration axis.
  const merged = useMemo(() => {
    const byIteration = new Map<number, MetricRow>()
    for (const name of selected) {
      for (const row of data[name] ?? []) {
        const value = row[metric]
        if (typeof value !== 'number') continue
        const entry = byIteration.get(row.iteration) ?? { iteration: row.iteration }
        entry[name] = value
        byIteration.set(row.iteration, entry)
      }
    }
    return [...byIteration.values()].sort((a, b) => a.iteration - b.iteration)
  }, [data, selected, metric])

  const series: SeriesSpec[] = selected.map(name => ({
    key: name, name, color: colorOf(name), showDots: metric === 'dev_win_rate',
  }))

  return <div className="stack">
    <div className="compare-picker">
      {runs.map(run => (
        <button key={run.name}
                className={`compare-chip ${selected.includes(run.name) ? 'on' : ''}`}
                style={selected.includes(run.name) ? { ['--chip-color' as string]: colorOf(run.name) } : undefined}
                onClick={() => toggle(run.name)}>
          <i />{run.name}
        </button>
      ))}
    </div>

    <Panel title={t('compare.overlay')} extra={
      <select value={metric} onChange={event => setMetric(event.target.value)}>
        {OVERLAY_METRICS.map(key => <option key={key} value={key}>{t(`compare.metric.${key}`)}</option>)}
      </select>
    }>
      {series.length ? (
        <MetricChart rows={merged} segments={[]} height={300} percent series={series} />
      ) : <Empty text={t('compare.pick')} />}
    </Panel>

    <Panel title={t('compare.table')}>
      {selected.length ? (
        <div style={{ overflowX: 'auto' }}>
          <table className="compare-table">
            <thead>
              <tr>
                <th>{t('compare.run')}</th>
                <th>{t('compare.iterations')}</th>
                <th>{t('compare.bestDev')}</th>
                <th>{t('compare.finalTrain')}</th>
                <th>{t('compare.bestReached')}</th>
                <th>{t('compare.bestConversion')}</th>
              </tr>
            </thead>
            <tbody>
              {selected.map(name => {
                const rows = data[name] ?? []
                return (
                  <tr key={name}>
                    <td><span className="run-name"><i style={{ background: colorOf(name) }} />{name}</span></td>
                    <td>{rows.length ? rows[rows.length - 1].iteration : '—'}</td>
                    <td>{pct(best(rows, 'dev_win_rate'))}</td>
                    <td>{pct(last(rows, 'train_win_rate'))}</td>
                    <td>{pct(best(rows, 'reached_boss_rate'))}</td>
                    <td>{pct(best(rows, 'boss_conversion'))}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : <Empty text={t('compare.pick')} />}
    </Panel>
  </div>
}

function best(rows: MetricRow[], key: string): number | undefined {
  let value: number | undefined
  for (const row of rows) {
    const candidate = row[key]
    if (typeof candidate === 'number') value = value === undefined ? candidate : Math.max(value, candidate)
  }
  return value
}

function last(rows: MetricRow[], key: string): number | undefined {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const candidate = rows[i][key]
    if (typeof candidate === 'number') return candidate
  }
  return undefined
}
