// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { I18nProvider } from '../i18n'
import type { Run } from '../types'
import { Live } from './Live'

const run: Run = {
  name: 'test-run', config: {}, history_count: 0, episode_count: 0, checkpoints: 0,
  latest: { iteration: 0 }, best: { iteration: 0 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: false, episodes: false },
}

const snapshot = {
  enabled: true, session_id: 's1', updated_at: new Date().toISOString(), worker_count: 12,
  dropped_events: 0, stale: false, action_rate: 8.5,
  workers: Array.from({ length: 12 }, (_, worker_id) => ({
    worker_id, status: worker_id === 0 ? 'running' : 'idle', seq: worker_id === 0 ? 1 : 0,
    updated_at: new Date().toISOString(), seed: worker_id === 0 ? 'seed-0' : undefined,
    iteration: 7, stage: 'normal_combat', floor: 1, round: 2, step: 3,
    action: worker_id === 0 ? { cmd: 'action', action: 'play_card', args: { card_index: 0 } } : undefined,
    selected_label: worker_id === 0 ? 'Bash' : undefined,
  })),
}

describe('live worker console', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/events') ? {
        enabled: true, session_id: 's1', worker_id: 0, next_after: 1,
        dropped_events: 0, stale: false,
        items: [{ ...snapshot.workers[0], session_id: 's1', timestamp: new Date().toISOString(),
          type: 'action', seq: 1, reward: 0, value: 0.4, logp: -0.2 }],
      } : snapshot
      return { ok: true, json: async () => body } as Response
    }))
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows 12 workers and drills into one console', async () => {
    render(<I18nProvider><Live run={run} /></I18nProvider>)
    await waitFor(() => expect(screen.getByText('Worker 12')).toBeInTheDocument())
    expect(screen.getByText('1/12 个 worker 活跃')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Worker 1'))
    await waitFor(() => expect(screen.getByText(/打出 Bash/)).toBeInTheDocument())
    expect(screen.getByText('全部 Worker')).toBeInTheDocument()
    fireEvent.click(screen.getByText('暂停显示'))
    expect(screen.getByText('继续显示')).toBeInTheDocument()
  })

  it('explains why an older run has no live stream', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ ...snapshot, enabled: false, workers: [] }) })) as any)
    render(<I18nProvider><Live run={run} /></I18nProvider>)
    await waitFor(() => expect(screen.getByText('这个 run 尚未启用实时事件')).toBeInTheDocument())
  })
})
