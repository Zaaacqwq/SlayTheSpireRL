// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Encounters, sorted } from './Encounters'
import { I18nProvider } from '../i18n'
import type { EncounterRow, EncounterStats, Run } from '../types'

const RUN: Run = {
  name: 'm2_v8', config: {}, history_count: 2, episode_count: 12, checkpoints: 0,
  latest: { iteration: 1 }, best: { iteration: 1 },
  stats: { wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0 },
  availability: { metrics: true, episodes: true },
}

function fight(partial: Partial<EncounterRow> & { label: string; kind: EncounterRow['kind'] }): EncounterRow {
  return {
    key: `${partial.kind}/${partial.label}`, fights: 10, wins: 10, losses: 0, unresolved: 0,
    win_rate: 1, avg_hp_lost: 5, avg_hp_in: 70, acts: [1], ...partial,
  }
}

const NIBBIT = fight({ label: 'Nibbit', kind: 'monster', fights: 120, wins: 120, win_rate: 1 })
const DECIMILLIPEDE = fight({
  label: 'Decimillipede x3', kind: 'elite', fights: 38, wins: 22, losses: 16,
  win_rate: 0.5789, avg_hp_lost: 33.1,
})
const KAISER = fight({
  label: 'Kaiser Crab', kind: 'boss', fights: 40, wins: 40, losses: 0, win_rate: 1,
  avg_hp_lost: null, acts: [2],
})

function mock(payload: Partial<EncounterStats>) {
  const body: EncounterStats = {
    run: 'm2_v8', encounters: [], enemies: [],
    by_kind: { monster: [], elite: [], boss: [] },
    scope: { split: 'dev', episodes: 700, fights: 4377, iteration_from: 9, iteration_to: 139 },
    progress: { scanned: 886, total: 886, building: false, error: null },
    ...payload,
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => body })))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const view = () => render(<I18nProvider><Encounters run={RUN} /></I18nProvider>)

test('splits fights into normal, elite and boss tables', async () => {
  mock({
    encounters: [NIBBIT, DECIMILLIPEDE, KAISER],
    by_kind: { monster: [NIBBIT], elite: [DECIMILLIPEDE], boss: [KAISER] },
  })
  view()

  const elite = (await screen.findByText('精英怪')).closest('section')!
  expect(within(elite).getByText('Decimillipede x3')).toBeInTheDocument()
  expect(within(elite).getByText('57.9%')).toBeInTheDocument()
  // a normal monster never lands in the elite table
  expect(within(elite).queryByText('Nibbit')).toBeNull()
  expect(within(screen.getByText('Boss').closest('section')!).getByText('Kaiser Crab')).toBeInTheDocument()
})

test('a boss with no HP lost is shown as such — that is the free-win bug', async () => {
  // KAISER_CRAB_BOSS spawned zero enemies: 100% win rate, nothing ever hit us
  mock({ encounters: [KAISER], by_kind: { monster: [], elite: [], boss: [KAISER] } })
  view()

  const boss = (await screen.findByText('Kaiser Crab')).closest('tr')!
  expect(within(boss).getByText('100%')).toBeInTheDocument()
  expect(within(boss).getAllByText('—').length).toBeGreaterThan(0)
})

test('switching to the per-monster view asks for the same scope', async () => {
  mock({
    encounters: [DECIMILLIPEDE], enemies: [fight({ label: 'Decimillipede', kind: 'elite', fights: 38, win_rate: 0.5789 })],
    by_kind: { monster: [], elite: [DECIMILLIPEDE], boss: [] },
  })
  view()
  fireEvent.click(await screen.findByRole('tab', { name: '按单个怪' }))

  expect(screen.getByText('Decimillipede')).toBeInTheDocument()
  expect(screen.queryByText('Decimillipede x3')).toBeNull()
})

test('reports scan progress while the backend is still reading episodes', async () => {
  mock({ progress: { scanned: 120, total: 6218, building: true, error: null } })
  view()

  expect(await screen.findByText(/120 \/ 6218/)).toBeInTheDocument()
})

test('sorting defaults to what kills runs, not to what is common', () => {
  const rare = fight({ label: 'rare', kind: 'elite', fights: 4, losses: 3, win_rate: 0.25, avg_hp_lost: 40 })
  const common = fight({ label: 'common', kind: 'elite', fights: 200, losses: 1, win_rate: 0.995, avg_hp_lost: 9 })

  expect(sorted([common, rare], 'losses').map(row => row.label)).toEqual(['rare', 'common'])
  expect(sorted([common, rare], 'fights').map(row => row.label)).toEqual(['common', 'rare'])
  // win rate is the one column whose interesting end is the bottom
  expect(sorted([common, rare], 'win_rate').map(row => row.label)).toEqual(['rare', 'common'])
  // a null (never resolved) never outranks a real bad number
  const unknown = fight({ label: 'unknown', kind: 'elite', win_rate: null, avg_hp_lost: null })
  expect(sorted([unknown, rare], 'win_rate')[0].label).toBe('rare')
  expect(sorted([unknown, common], 'avg_hp_lost')[0].label).toBe('common')
})
