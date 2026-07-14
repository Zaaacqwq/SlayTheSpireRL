import { useEffect, useMemo, useState } from 'react'
import { Activity, Database, Layers3, Settings2, TrendingUp } from 'lucide-react'
import { fetchMetrics, fetchTimeline } from '../api'
import { num, pct, stageLabel, stageTint } from '../format'
import type { MetricRow, Run, TimelineItem } from '../types'
import { FloorChart, MiniChart, StageLegend, WinRateChart, stageSegments } from '../components/charts'
import { Empty, Panel, StatTile } from '../components/ui'

export function Overview({ run, onInspectIteration }: {
  run: Run
  onInspectIteration: (iteration: number) => void
}) {
  const [metrics, setMetrics] = useState<MetricRow[]>([])
  const [timeline, setTimeline] = useState<TimelineItem[]>([])

  useEffect(() => {
    if (run.name === 'legacy') { setMetrics([]); setTimeline([]); return }
    void fetchMetrics(run.name).then(data => setMetrics(data.rows)).catch(() => setMetrics([]))
    void fetchTimeline(run.name).then(data => setTimeline(data.items)).catch(() => setTimeline([]))
    // history_count/episode_count advance during live training, refreshing charts.
  }, [run.name, run.history_count, run.episode_count])

  const segments = useMemo(() => stageSegments(metrics), [metrics])
  const latest = metrics[metrics.length - 1]
  const bestDev = useMemo(() => {
    const rows = metrics.filter(row => typeof row.dev_win_rate === 'number')
    return rows.length ? rows.reduce((a, b) => (b.dev_win_rate as number) > (a.dev_win_rate as number) ? b : a) : undefined
  }, [metrics])
  const replayGroups = timeline.filter(item => item.split === 'replay' && item.iteration !== null)

  return <>
    <div className="tile-row">
      <StatTile
        label="当前阶段"
        value={stageLabel(latest?.stage as string)}
        detail={latest ? `迭代 ${latest.iteration} / 共 ${metrics.length} 次记录` : '暂无指标'}
      />
      <StatTile
        label="最新训练胜率"
        value={pct(latest?.train_win_rate)}
        detail={latest ? `平均楼层 ${num(latest.avg_floor, 1)}` : undefined}
      />
      <StatTile
        label="最佳验证胜率"
        value={pct(bestDev?.dev_win_rate)}
        detail={bestDev ? `迭代 ${bestDev.iteration} · ${stageLabel(bestDev.stage as string)}` : '尚无验证评估'}
        up={typeof bestDev?.dev_win_rate === 'number' && (bestDev.dev_win_rate as number) > 0}
      />
      <StatTile
        label="已记录对局"
        value={run.episode_count.toLocaleString()}
        detail={`${run.checkpoints} 个 checkpoint`}
        dim={run.episode_count === 0}
      />
    </div>

    <div className="chart-stack">
      <Panel icon={<TrendingUp />} title="学习曲线 · 胜率" extra="背景色带 = 课程阶段">
        <StageLegend segments={segments} />
        <WinRateChart rows={metrics} segments={segments} />
      </Panel>
      <Panel icon={<Activity />} title="到达楼层">
        <FloorChart rows={metrics} segments={segments} />
      </Panel>
    </div>

    <div className="mini-grid">
      <MiniChart rows={metrics} dataKey="loss" title="总损失 loss" />
      <MiniChart rows={metrics} dataKey="value_loss" title="价值损失 value loss" />
      <MiniChart rows={metrics} dataKey="entropy" title="策略熵 entropy" />
    </div>

    <div className="overview-lower">
      <Panel icon={<Layers3 />} title="课程阶段推进">
        {segments.length ? (
          <div className="stage-cards">
            {segments.map(segment => (
              <div key={`${segment.stage}-${segment.from}`} className="stage-card"
                   style={{ borderLeftColor: stageTint(segment.stage) }}>
                <b>{stageLabel(segment.stage)}</b>
                <small>迭代 {segment.from} – {segment.to}{segment.resumed ? ' · 重返' : ''}</small>
                <em>{segment.to - segment.from + 1} 次迭代</em>
              </div>
            ))}
          </div>
        ) : <Empty text="暂无阶段数据" />}
      </Panel>

      <Panel icon={<Database />} title="已录制的对局批次" extra="点击查看该批对局">
        {replayGroups.length || timeline.length ? (
          <div className="ckpt-strip">
            {timeline.filter(item => item.iteration !== null).map(item => (
              <button key={`${item.split}-${item.iteration}`} className="ckpt-chip"
                      onClick={() => onInspectIteration(item.iteration as number)}>
                <b>迭代 {item.iteration} · {item.split === 'replay' ? '回放' : item.split === 'dev' ? '验证' : '训练'}</b>
                {stageLabel(item.stage)} · {item.wins}/{item.episodes} 胜 · 奖励 {num(item.avg_reward)}
              </button>
            ))}
          </div>
        ) : <Empty text="该实验还没有对局记录，可用 tools/m2_record_episodes.py 补录" />}
      </Panel>
    </div>

    <Panel icon={<Settings2 />} title="实验配置">
      <ConfigGrid config={run.config} />
    </Panel>
  </>
}

function ConfigGrid({ config }: { config: Record<string, unknown> }) {
  const rows = Object.entries(config).filter(([, value]) => typeof value !== 'object').slice(0, 18)
  if (!rows.length) return <Empty text="暂无配置" />
  return <>
    <div className="config-grid">
      {rows.map(([key, value]) => (
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{String(value ?? '—')}</b></div>
      ))}
    </div>
    <details>
      <summary>原始配置 JSON</summary>
      <pre className="raw">{JSON.stringify(config, null, 2)}</pre>
    </details>
  </>
}
