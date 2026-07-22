import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { MetricRow } from '../types'
import { num, pct, stageLabel, stageTint } from '../format'
import { Empty, InfoTip } from './ui'
import { useI18n } from '../i18n'

/* Chart chrome reads CSS custom properties, so a single theme swap repaints every
 * mark. SVG stroke/fill accept var() references. Categorical slots are the
 * validated reference palette (see theme.css / dataviz validate_palette.js). */
const INK_MUTED = 'var(--ink-3)'
const GRID = 'var(--grid)'
export const SERIES = {
  blue: 'var(--series-1)', aqua: 'var(--series-2)', yellow: 'var(--series-3)',
  green: 'var(--series-4)', violet: 'var(--series-5)', red: 'var(--series-6)',
  magenta: 'var(--series-7)', orange: 'var(--series-8)', grey: 'var(--ink-3)',
}
/* Fixed categorical order — assigned in sequence, never cycled. */
export const SERIES_ORDER = [
  SERIES.blue, SERIES.aqua, SERIES.yellow, SERIES.green,
  SERIES.violet, SERIES.red, SERIES.magenta, SERIES.orange,
]

const TOOLTIP_STYLE = {
  background: 'var(--tooltip-bg)', border: '1px solid var(--border)',
  borderRadius: 10, fontSize: 12, color: 'var(--ink)', boxShadow: 'var(--shadow-lift)',
} as const

export interface StageSegment {
  stage: string
  from: number
  to: number
  resumed: boolean
}

/** Contiguous iteration ranges per curriculum stage, for band tints. */
export function stageSegments(rows: MetricRow[]): StageSegment[] {
  const segments: StageSegment[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    const stage = String(row.stage || 'unknown')
    const last = segments[segments.length - 1]
    if (last && last.stage === stage) {
      last.to = row.iteration
    } else {
      segments.push({ stage, from: row.iteration, to: row.iteration, resumed: seen.has(stage) })
      seen.add(stage)
    }
  }
  return segments
}

function StageBands({ segments }: { segments: StageSegment[] }) {
  return <>
    {segments.map(segment => (
      <ReferenceArea
        key={`${segment.stage}-${segment.from}`}
        x1={segment.from} x2={segment.to}
        fill={stageTint(segment.stage)} fillOpacity={0.08} strokeOpacity={0}
      />
    ))}
  </>
}

export interface SeriesSpec {
  key: string
  name: string
  color: string
  showDots?: boolean
}

export interface GuideLine {
  value: number
  label: string
  color?: string
}

export function MetricChart({ rows, segments, series, height, percent = false, guides, digits = 3, hidden, onToggle }: {
  rows: MetricRow[]; segments: StageSegment[]; series: SeriesSpec[]
  height: number; percent?: boolean; guides?: GuideLine[]; digits?: number
  // When onToggle is supplied the legend becomes a filter: click an entry to
  // hide/show its line. `hidden` is the set of currently-hidden series keys.
  hidden?: Set<string>; onToggle?: (key: string) => void
}) {
  const { t } = useI18n()
  if (!rows.length) return <Empty text={t('chart.noMetrics')} />
  const drawn = onToggle ? series.filter(spec => !hidden?.has(spec.key)) : series
  return <>
    {series.length > 1 && (
      <div className="legend-row">
        {series.map(spec => {
          const off = hidden?.has(spec.key) ?? false
          if (!onToggle) return <span key={spec.key}><i style={{ background: spec.color }} />{spec.name}</span>
          return (
            <button
              key={spec.key}
              type="button"
              className={`legend-toggle${off ? ' off' : ''}`}
              aria-pressed={!off}
              onClick={() => onToggle(spec.key)}
            >
              <i style={{ background: spec.color }} />{spec.name}
            </button>
          )
        })}
      </div>
    )}
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 14, bottom: 2, left: 0 }}>
        <StageBands segments={segments} />
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="iteration" type="number" domain={['dataMin', 'dataMax']}
               allowDecimals={false} tickCount={12}
               stroke={INK_MUTED} tickLine={false} fontSize={11} />
        <YAxis
          stroke={INK_MUTED} tickLine={false} axisLine={false} fontSize={11} width={44}
          domain={percent ? [0, 1] : ['auto', 'auto']}
          tickFormatter={value => percent ? `${Math.round(Number(value) * 100)}%` : String(value)}
        />
        {guides?.map(guide => (
          <ReferenceLine key={guide.label} y={guide.value} stroke={guide.color ?? 'var(--serious)'}
                         strokeDasharray="4 4" strokeWidth={1.5}
                         label={{ value: guide.label, position: 'right', fill: 'var(--ink-3)', fontSize: 10 }} />
        ))}
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={iteration => t('common.iteration', { value: String(iteration) })}
          formatter={(value, name) => [percent ? pct(value) : num(value, digits), String(name)]}
        />
        {drawn.map(spec => (
          <Line key={spec.key} type="linear" dataKey={spec.key} name={spec.name}
                stroke={spec.color} strokeWidth={2}
                dot={spec.showDots ? { r: 2.5, fill: spec.color, stroke: 'var(--surface)', strokeWidth: 1.5 } : false}
                activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }}
                connectNulls isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  </>
}

export function WinRateChart({ rows, segments }: { rows: MetricRow[]; segments: StageSegment[] }) {
  const { t } = useI18n()
  return <MetricChart rows={rows} segments={segments} height={260} percent series={[
    { key: 'train_win_rate', name: t('chart.trainWin'), color: SERIES.blue },
    { key: 'dev_win_rate', name: t('chart.devWin'), color: SERIES.aqua, showDots: true },
  ]} />
}

export function FloorChart({ rows, segments }: { rows: MetricRow[]; segments: StageSegment[] }) {
  const { t } = useI18n()
  return <MetricChart rows={rows} segments={segments} height={170} digits={1} series={[
    { key: 'avg_floor', name: t('chart.avgFloor'), color: SERIES.yellow },
    { key: 'dev_avg_floor', name: t('chart.devFloor'), color: SERIES.violet, showDots: true },
  ]} />
}

/** Compact single-series card used in metric grids (PPO health, sparkline row). */
export function MiniChart({ rows, dataKey, title, tip, color = SERIES.blue, guides }: {
  rows: MetricRow[]; dataKey: string; title: string; tip?: string; color?: string; guides?: GuideLine[]
}) {
  const { t } = useI18n()
  const latest = lastNumeric(rows, dataKey)
  return (
    <div className="mini">
      <div className="mini-head">
        <span>{title}{tip && <InfoTip text={tip} />}</span>
        <b className="num">{latest === undefined ? '—' : num(latest, 4)}</b>
      </div>
      {rows.length ? (
        <ResponsiveContainer width="100%" height={92}>
          <LineChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="iteration" type="number" domain={['dataMin', 'dataMax']}
                   allowDecimals={false} tickCount={4}
                   stroke={INK_MUTED} tickLine={false} fontSize={9} />
            <YAxis stroke={INK_MUTED} tickLine={false} axisLine={false} fontSize={9} width={30} />
            {guides?.map(guide => (
              <ReferenceLine key={guide.label} y={guide.value} stroke={guide.color ?? 'var(--serious)'}
                             strokeDasharray="3 3" strokeWidth={1} />
            ))}
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={iteration => t('common.iteration', { value: String(iteration) })}
              formatter={value => [num(value, 4), title]}
            />
            <Line type="linear" dataKey={dataKey} stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      ) : <Empty text={t('common.noData')} />}
    </div>
  )
}

function lastNumeric(rows: MetricRow[], key: string): number | undefined {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const value = rows[i][key]
    if (typeof value === 'number') return value
  }
  return undefined
}

export function StageLegend({ segments }: { segments: StageSegment[] }) {
  const { locale } = useI18n()
  const stages = [...new Set(segments.map(segment => segment.stage))]
  if (!stages.length) return null
  return (
    <div className="stage-chips">
      {stages.map(stage => (
        <span key={stage} className="stage-chip">
          <i style={{ background: stageTint(stage) }} />{stageLabel(stage, locale)}
        </span>
      ))}
    </div>
  )
}
