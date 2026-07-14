// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, renderHook } from '@testing-library/react'
import { expect, test } from 'vitest'
import { useStageFilter } from './useStageFilter'
import type { MetricRow } from './types'

// The contamination this exists to prevent, measured on the real m2_v6 run:
//   normal_combat  n=20  avg_floor 1.00 .. 1.00
//   mixed_combat   n=10  avg_floor 1.00 .. 1.00
//   boss_combat    n=10  avg_floor 1.00 .. 1.00
//   act1          n=425  avg_floor 7.27 .. 13.73
// Plotted on one axis that is 40 points pinned at 1.0 followed by a jump to 10,
// which flattens the only curve that carries any signal.
const ROWS: MetricRow[] = [
  { iteration: 0, stage: 'normal_combat', avg_floor: 1.0 },
  { iteration: 1, stage: 'normal_combat', avg_floor: 1.0 },
  { iteration: 2, stage: 'act1', avg_floor: 9.7 },
  { iteration: 3, stage: 'act1', avg_floor: 11.4 },
]

test('defaults to the stage the run is currently training', () => {
  const { result } = renderHook(() => useStageFilter(ROWS))
  expect(result.current.stage).toBe('act1')
  expect(result.current.metrics.map(r => r.avg_floor)).toEqual([9.7, 11.4])
})

test('a chosen stage sees only its own rows', () => {
  const { result } = renderHook(() => useStageFilter(ROWS))
  act(() => result.current.setStage('normal_combat'))
  expect(result.current.metrics.map(r => r.avg_floor)).toEqual([1.0, 1.0])
})

test('stages are offered in curriculum order, not first-seen order', () => {
  const shuffled: MetricRow[] = [
    { iteration: 0, stage: 'act1' },
    { iteration: 1, stage: 'normal_combat' },
    { iteration: 2, stage: 'boss_combat' },
  ]
  const { result } = renderHook(() => useStageFilter(shuffled))
  expect(result.current.stages).toEqual(['normal_combat', 'boss_combat', 'act1'])
})

test('an explicit choice is not overridden as the run advances', () => {
  const { result, rerender } = renderHook(({ rows }) => useStageFilter(rows), {
    initialProps: { rows: ROWS },
  })
  act(() => result.current.setStage('normal_combat'))
  rerender({ rows: [...ROWS, { iteration: 4, stage: 'full_a0' }] })
  expect(result.current.stage).toBe('normal_combat')
})
