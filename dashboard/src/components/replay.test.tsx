// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { EnemyCard } from './replay'
import { I18nProvider } from '../i18n'

afterEach(() => cleanup())

const view = (enemy: any) =>
  render(<I18nProvider><EnemyCard enemy={enemy} /></I18nProvider>)

// Real state from m2_v7_acts12_hpshape iter 2390, m2-a0-ironclad-train-230196 step 194:
// the player read "Attack 8", had 22 HP and died, because the intent is 8 x3 = 24.
test('a multi-hit intent shows the multiplier and the total, not just per-hit damage', () => {
  view({
    name: 'Hunter Killer', hp: 50, max_hp: 90,
    intents: [{ type: 'Attack', damage: 8, hits: 3, total_damage: 24 }],
  })
  expect(screen.getByText(/Attack 8×3 = 24/)).toBeInTheDocument()
})

test('a single-hit intent stays unadorned', () => {
  view({ name: 'Bowlbug', hp: 20, max_hp: 20, intents: [{ type: 'Attack', damage: 7, hits: 1, total_damage: 7 }] })
  expect(screen.getByText(/Attack 7/)).toBeInTheDocument()
  expect(screen.queryByText(/×/)).not.toBeInTheDocument()
})

test('an intent with no damage renders its type alone', () => {
  view({ name: 'Ovicopter', hp: 130, max_hp: 130, intents: [{ type: 'Summon', damage: null }] })
  expect(screen.getByText(/Summon/)).toBeInTheDocument()
})

test('hits is optional — fall back to plain damage', () => {
  view({ name: 'Tough Egg', hp: 19, max_hp: 19, intents: [{ type: 'Attack', damage: 5 }] })
  expect(screen.getByText(/Attack 5/)).toBeInTheDocument()
  expect(screen.queryByText(/×/)).not.toBeInTheDocument()
})
