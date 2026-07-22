// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Training } from './Training'
import { I18nProvider } from '../i18n'
import type { MetricRow, Run } from '../types'

const RUN: Run = {
  name: 'm2_v7_clean_init0',
  config: { target_kl: 0.01, hard_kl_stop: 0.02 },
  history_count: 2, episode_count: 0, checkpoints: 0,
  latest: { iteration: 1 }, best: { iteration: 1 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: true, episodes: false },
}

function mockMetrics(rows: MetricRow[]) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({ run: RUN.name, source: 'history.jsonl', metrics: [], rows }),
  })))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const view = () => render(<I18nProvider><Training run={RUN} /></I18nProvider>)

test('surfaces a clean visibility contract without alarm', async () => {
  mockMetrics([
    {
      iteration: 0, stage: 'act1', approx_kl: 0.008, loss: 0.01, policy_loss: -0.01, value_loss: 0.02,
      visibility: { decisions: 10025, pointer_misses: {}, unknown_entities: {}, nonfinite_features: 0, violations: [] },
    },
  ])
  const { container } = view()
  await waitFor(() => expect(container.querySelector('.contract-grid')).not.toBeNull())
  expect(container.querySelector('.contract-cell.bad')).toBeNull()
})

test('marks a breached contract cell when the model is fed something it cannot see', async () => {
  mockMetrics([
    {
      iteration: 0, stage: 'act1', approx_kl: 0.008,
      visibility: { decisions: 100, pointer_misses: { card_target: 3 }, unknown_entities: {}, nonfinite_features: 0, violations: [] },
    },
  ])
  const { container } = view()
  await waitFor(() => expect(container.querySelector('.contract-grid')).not.toBeNull())
  expect(container.querySelector('.contract-cell.bad')).not.toBeNull()
})
