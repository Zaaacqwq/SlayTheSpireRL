import { useEffect, useMemo, useState } from 'react'
import { STAGE_ORDER } from './format'
import type { MetricRow } from './types'

/** View one curriculum stage at a time.
 *
 * The stages are not comparable quantities. A combat stage is a single fight
 * spawned on floor 1, so its avg_floor is the constant 1.0 and it has no boss
 * funnel at all; its win rate (~0.8) measures something entirely different from a
 * full run's (~0.1). Plotting them on one axis flattens the only curve that
 * carries signal — 40 points pinned at 1.0, then a jump to 10.
 *
 * Defaults to the stage the run is currently training, which is the one you
 * actually want when you open the page mid-run.
 */
export function useStageFilter(rows: MetricRow[]) {
  const stages = useMemo(() => {
    const present = new Set(rows.map(row => String(row.stage ?? '')).filter(Boolean))
    return STAGE_ORDER.filter(stage => present.has(stage))
  }, [rows])

  const latest = rows.length ? String(rows[rows.length - 1].stage ?? '') : ''
  const [stage, setStage] = useState('')

  // follow the run as it advances, until the user picks a stage themselves
  const [pinned, setPinned] = useState(false)
  useEffect(() => {
    if (!pinned && latest) setStage(latest)
  }, [latest, pinned])

  const choose = (next: string) => { setPinned(true); setStage(next) }

  const metrics = useMemo(
    () => (stage ? rows.filter(row => row.stage === stage) : rows),
    [rows, stage],
  )

  return { stages, stage, setStage: choose, metrics }
}
