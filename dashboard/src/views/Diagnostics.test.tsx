// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Diagnostics } from './Diagnostics'
import { I18nProvider } from '../i18n'
import type { MetricRow, Run } from '../types'

const RUN: Run = {
  name: 'm2_v6', config: {}, history_count: 2, episode_count: 0, checkpoints: 0,
  latest: { iteration: 1 }, best: { iteration: 1 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: true, episodes: false },
}

function mockMetrics(rows: MetricRow[]) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({ run: 'm2_v6', source: 'history.jsonl', metrics: [], rows }),
  })))
}

// no global setup file, so RTL's auto-cleanup is not wired up: without this the
// first test's alarm banner survives into the next test's DOM
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const view = () => render(<I18nProvider><Diagnostics run={RUN} /></I18nProvider>)

test('raises the alarm when losing out-returns winning', async () => {
  // the shape that actually shipped: a deep death returned +1.76, a win +0.65
  mockMetrics([
    { iteration: 0, stage: 'act1', win_return: 0.65, loss_return: 1.76, inverted: 1 },
  ])
  view()
  expect(await screen.findByRole('alert')).toHaveTextContent(/REWARD INVERTED|奖励反转/)
})

test('stays quiet when winning pays more', async () => {
  mockMetrics([
    { iteration: 0, stage: 'act1', win_return: 0.65, loss_return: -1.05, inverted: 0 },
  ])
  view()
  // matched on the reassurance sentence, not on the numbers: the tooltip quotes
  // the historical +1.76 / +0.65 figures and would collide
  await screen.findByText(/赢更值|Winning pays more/)
  expect(screen.queryByRole('alert')).toBeNull()
})

test('charts every action type the run reports, so a structural zero is visible', async () => {
  mockMetrics([
    {
      iteration: 0, stage: 'act1', win_return: 1, loss_return: -1, inverted: 0,
      action_play_card: 0.55, action_end_turn: 0.25, action_use_potion: 0,
    },
  ])
  view()
  // the legend names each series; a potion series pinned at zero is the tell
  expect(await screen.findByText('use_potion')).toBeInTheDocument()
  expect(screen.getByText('play_card')).toBeInTheDocument()
})

test('hides the boss funnel on a combat stage, where it is undefined', async () => {
  // combat stages spawn the fight next to the boss; "reached the boss" is meaningless
  mockMetrics([
    { iteration: 0, stage: 'boss_combat', win_return: 1, loss_return: -1, inverted: 0 },
  ])
  view()
  expect(await screen.findByText(/整局|full run/i)).toBeInTheDocument()
})
