import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { MetricRow } from '../types'
import { num, pct, stageLabel, stageTint } from '../format'
import { Empty } from './ui'

/* Chart chrome (dataviz dark tokens). SVG attributes need literal colors. */
const INK_MUTED = '#898781'
const GRID = '#2c2c2a'
const SERIES = { blue: '#3987e5', aqua: '#199e70', yellow: '#c98500', violet: '#9085e9' }

const TOOLTIP_STYLE = {
  background: '#222221', border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 8, fontSize: 12, color: '#c3c2b7',
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
        fill={stageTint(segment.stage)} fillOpacity={0.07} strokeOpacity={0}
      />
    ))}
  </>
}

interface SeriesSpec {
  key: string
  name: string
  color: string
}

function MetricChart({ rows, segments, series, height, percent = false }: {
  rows: MetricRow[]; segments: StageSegment[]; series: SeriesSpec[]
  height: number; percent?: boolean
}) {
  if (!rows.length) return <Empty text="暂无训练指标" />
  return <>
    <div className="legend-row">
      {series.map(spec => <span key={spec.key}><i style={{ background: spec.color }} />{spec.name}</span>)}
    </div>
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 2, left: 0 }}>
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
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={iteration => `迭代 ${iteration}`}
          formatter={(value, name) => [percent ? pct(value) : num(value, 3), String(name)]}
        />
        {series.map(spec => (
          <Line key={spec.key} type="monotone" dataKey={spec.key} name={spec.name}
                stroke={spec.color} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  </>
}

export function WinRateChart({ rows, segments }: { rows: MetricRow[]; segments: StageSegment[] }) {
  return <MetricChart rows={rows} segments={segments} height={260} percent series={[
    { key: 'train_win_rate', name: '训练胜率', color: SERIES.blue },
    { key: 'dev_win_rate', name: '验证胜率', color: SERIES.aqua },
  ]} />
}

export function FloorChart({ rows, segments }: { rows: MetricRow[]; segments: StageSegment[] }) {
  return <MetricChart rows={rows} segments={segments} height={150} series={[
    { key: 'avg_floor', name: '平均到达楼层', color: SERIES.yellow },
    { key: 'dev_avg_floor', name: '验证平均楼层', color: SERIES.violet },
  ]} />
}

export function MiniChart({ rows, dataKey, title }: { rows: MetricRow[]; dataKey: string; title: string }) {
  return (
    <div className="panel">
      <div className="panel-title"><h2>{title}</h2></div>
      {rows.length ? (
        <ResponsiveContainer width="100%" height={110}>
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -14 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="iteration" type="number" domain={['dataMin', 'dataMax']}
                   allowDecimals={false} tickCount={5}
                   stroke={INK_MUTED} tickLine={false} fontSize={10} />
            <YAxis stroke={INK_MUTED} tickLine={false} axisLine={false} fontSize={10} />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={iteration => `迭代 ${iteration}`}
              formatter={value => [num(value, 4), title]}
            />
            <Line type="monotone" dataKey={dataKey} stroke={SERIES.blue} strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      ) : <Empty text="暂无数据" />}
    </div>
  )
}

export function StageLegend({ segments }: { segments: StageSegment[] }) {
  const stages = [...new Set(segments.map(segment => segment.stage))]
  if (!stages.length) return null
  return (
    <div className="stage-chips">
      {stages.map(stage => (
        <span key={stage} className="stage-chip"
              style={{ borderColor: stageTint(stage), color: stageTint(stage) }}>
          {stageLabel(stage)}
        </span>
      ))}
    </div>
  )
}
