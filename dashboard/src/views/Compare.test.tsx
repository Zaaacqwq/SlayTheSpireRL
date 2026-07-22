// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Compare } from './Compare'
import { I18nProvider } from '../i18n'
import type { Run } from '../types'

const makeRun = (name: string): Run => ({
  name, config: {}, history_count: 1, episode_count: 0, checkpoints: 0,
  latest: { iteration: 1 }, best: { iteration: 1 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: true, episodes: false },
})

const runs = [makeRun('m2_v6'), makeRun('m2_v7_clean_init0')]

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

test('overlays the current run and fills its comparison row', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({
      run: 'm2_v6', source: 'history.jsonl', metrics: [],
      rows: [
        { iteration: 10, stage: 'act1', train_win_rate: 0.2, dev_win_rate: 0.26, reached_boss_rate: 0.5, boss_conversion: 0.1 },
      ],
    }),
  })))
  render(<I18nProvider><Compare runs={runs} current="m2_v6" /></I18nProvider>)
  // both runs are pickable
  expect(screen.getAllByText('m2_v6').length).toBeGreaterThan(0)
  expect(screen.getByText('m2_v7_clean_init0')).toBeInTheDocument()
  // the preselected run's best dev win (26%) appears in the table after fetch
  await waitFor(() => expect(screen.getByText('26%')).toBeInTheDocument())
})
