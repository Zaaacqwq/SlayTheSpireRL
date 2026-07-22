import { describe, expect, it } from 'vitest'
import { coreActionsForStage } from './App'

describe('coreActionsForStage', () => {
  it('only guards in-combat actions during combat-only curriculum stages', () => {
    // normal_combat is a single isolated fight — no map, reward, shop, or potions,
    // so use_potion / select_map_node / select_card_reward are absent by design and
    // must not trip the structural-zero (potion-bug) alarm.
    for (const stage of ['normal_combat', 'mixed_combat', 'boss_combat']) {
      expect(coreActionsForStage(stage)).toEqual(['play_card', 'end_turn'])
    }
  })

  it('guards the full core action set on run stages where potions/map/rewards exist', () => {
    for (const stage of ['act1', 'acts_12', 'full_a0']) {
      const core = coreActionsForStage(stage)
      expect(core).toContain('use_potion')
      expect(core).toContain('select_map_node')
      expect(core).toContain('select_card_reward')
    }
  })

  it('defaults to the full set when the stage is unknown', () => {
    expect(coreActionsForStage(undefined)).toContain('use_potion')
  })
})
