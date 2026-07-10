"""Gymnasium environment wrapping sim/run.py for Phase B Stage 2.5.

Each episode is one mini-run: `sim.run.FIGHTS_PER_RUN` back-to-back combats
(random Act 1 encounters) where HP carries over between fights (not reset
to full) and, after each non-final win, the agent picks one of 3 random
reward cards (or skips) to add to its deck for the rest of the run. This
replaces v1/Stage 1/Stage 2's single-isolated-combat-with-a-fixed-starter-
-deck scope, which never actually exercised any card outside the 10-card
starter deck and never modeled the HP-attrition/deck-building tension that
is Slay the Spire's core loop -- see plan/stage2.5_run_continuity.md.

Still not modeled: map choice (the fight sequence is just N random
encounters, no player-chosen path), shop, events, relics, potions -- those
remain the full Stage 6 scope. Exposes `action_masks()` for sb3-contrib's
MaskablePPO.
"""

from __future__ import annotations

import random

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from sim.card import load_card_defs
from sim.combat import load_monster_data
from sim.run import FIGHTS_PER_RUN, REWARD_CHOICES, Run

MAX_HAND_SIZE = 10
MAX_ENEMIES = 3
NO_TARGET = MAX_ENEMIES  # sentinel target slot for self/all_enemies/no-target cards
COMBAT_ACTION_SPACE_SIZE = MAX_HAND_SIZE * (MAX_ENEMIES + 1) + 1  # + 1 for end_turn
END_TURN_ACTION = COMBAT_ACTION_SPACE_SIZE - 1
# Reward-pick phase actions live right after the combat action block: pick
# slot 0/1/2, or skip.
N_ACTIONS = COMBAT_ACTION_SPACE_SIZE + REWARD_CHOICES + 1
SKIP_REWARD_ACTION = N_ACTIONS - 1

# Stage 2's 14 triggered-Power cards (plan/plan.md 2026-07-10 entry). Several
# of these have no footprint in any other observation feature -- Barricade,
# Feel No Pain, Juggernaut, Vicious, Unmovable, Cruelty, Tank all resolve via
# reactive triggers or a damage-formula tweak that the existing hp/block/
# strength/etc. features can't reveal -- so without an explicit "active
# powers" block the policy would be blind to whether it has them. Fixed
# ordering also leaves room to append future powers without reshuffling
# existing feature indices.
POWER_IDS_IN_OBS = [
    "AGGRESSION", "BARRICADE", "CRIMSON_MANTLE", "CRUELTY", "DARK_EMBRACE",
    "DEMON_FORM", "FEEL_NO_PAIN", "INFERNO", "JUGGERNAUT", "PYRE", "RUPTURE",
    "TANK", "UNMOVABLE", "VICIOUS",
]

HAND_CARD_FEATURES = 7  # present, cost, is_upgraded, is_attack, is_skill, targets_single, targets_all
REWARD_CARD_FEATURES = 4  # present, cost, is_attack, is_skill
# hp_frac, block, strength, dexterity, vulnerable, weak, frail, energy_frac,
# hand_frac, deck_left_frac, is_reward_pick_phase, fight_progress_frac,
# + one flag per POWER_IDS_IN_OBS entry
PLAYER_FEATURES = 12 + len(POWER_IDS_IN_OBS)
ENEMY_FEATURES = 6  # alive, hp_frac, block, strength, vulnerable, weak

OBS_SIZE = (
    PLAYER_FEATURES
    + MAX_HAND_SIZE * HAND_CARD_FEATURES
    + MAX_ENEMIES * ENEMY_FEATURES
    + REWARD_CHOICES * REWARD_CARD_FEATURES
)


def _encode_action(hand_slot: int, target_slot: int) -> int:
    return hand_slot * (MAX_ENEMIES + 1) + target_slot


def _decode_action(action: int) -> tuple[int, int] | None:
    """Returns (hand_slot, target_slot), or None for the end_turn action.
    Only meaningful for actions < COMBAT_ACTION_SPACE_SIZE."""
    if action == END_TURN_ACTION:
        return None
    return divmod(action, MAX_ENEMIES + 1)


class STS2CombatEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        encounter_names: list[str] | None = None,
        seed: int | None = None,
        fights_per_run: int = FIGHTS_PER_RUN,
        player_max_hp: int = 80,
    ):
        super().__init__()
        self.card_defs = load_card_defs()
        self.monster_data = load_monster_data()
        if encounter_names is not None:
            # Run picks encounters itself from self.monster_data; honor a
            # restricted encounter_names list (e.g. for tests) by filtering
            # monster_data down to just those encounters.
            self.monster_data = {
                "monsters": self.monster_data["monsters"],
                "encounters": {k: v for k, v in self.monster_data["encounters"].items() if k in encounter_names},
            }
        self.fights_per_run = fights_per_run
        self.player_max_hp = player_max_hp

        self.action_space = spaces.Discrete(N_ACTIONS)
        self.observation_space = spaces.Box(
            low=-10.0, high=10.0, shape=(OBS_SIZE,), dtype=np.float32
        )

        self._py_rng = random.Random(seed)
        self.run: Run | None = None
        self._prev_enemy_hp_total = 0
        self._prev_player_hp = 0

    # ------------------------------------------------------------------
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self._py_rng = random.Random(seed)

        run_rng = random.Random(self._py_rng.random())
        self.run = Run(
            card_defs=self.card_defs,
            monster_data=self.monster_data,
            rng=run_rng,
            player_max_hp=self.player_max_hp,
            fights_per_run=self.fights_per_run,
        )
        self._prev_enemy_hp_total = sum(e.hp for e in self.run.combat.enemies)
        self._prev_player_hp = self.run.combat.player.hp

        obs = self._build_observation()
        info = {"fight_index": self.run.fight_index}
        return obs, info

    def step(self, action: int):
        assert self.run is not None, "call reset() before step()"
        action = int(action)
        was_reward_pick = self.run.phase == "reward_pick"

        if self.run.phase == "combat":
            decoded = _decode_action(action)
            combat = self.run.combat
            if decoded is None:
                self.run.end_turn()
            else:
                hand_slot, target_slot = decoded
                if hand_slot < len(combat.player.hand) and hand_slot in combat.legal_hand_indices():
                    targets = combat.targets_for(hand_slot)
                    if targets is None:
                        # self/all_enemies card: any target_slot value is accepted, ignored internally
                        self.run.play_card(hand_slot, target_index=None)
                    elif target_slot in targets:
                        self.run.play_card(hand_slot, target_index=target_slot)
                    # else: illegal target -> no-op (shouldn't happen under action_masks())
                # else: illegal hand slot -> no-op (shouldn't happen under action_masks())
        else:  # reward_pick
            reward_slot = action - COMBAT_ACTION_SPACE_SIZE
            choices = self.run.pending_reward_choices or []
            if 0 <= reward_slot < len(choices):
                self.run.pick_reward(reward_slot)
            else:
                self.run.skip_reward()

        reward = self._compute_reward(was_reward_pick)
        terminated = self.run.outcome is not None
        truncated = False
        obs = self._build_observation()
        info = {"outcome": self.run.outcome, "phase": self.run.phase, "fight_index": self.run.fight_index}
        return obs, reward, terminated, truncated, info

    def action_masks(self) -> np.ndarray:
        """sb3-contrib MaskablePPO duck-typed interface."""
        mask = np.zeros(N_ACTIONS, dtype=bool)
        if self.run is None or self.run.outcome is not None:
            return mask
        if self.run.phase == "combat":
            combat = self.run.combat
            mask[END_TURN_ACTION] = True
            for hand_slot in combat.legal_hand_indices():
                targets = combat.targets_for(hand_slot)
                if targets is None:
                    mask[_encode_action(hand_slot, NO_TARGET)] = True
                else:
                    for t in targets:
                        if t < MAX_ENEMIES:
                            mask[_encode_action(hand_slot, t)] = True
        else:  # reward_pick
            n_choices = len(self.run.pending_reward_choices or [])
            for i in range(min(n_choices, REWARD_CHOICES)):
                mask[COMBAT_ACTION_SPACE_SIZE + i] = True
            mask[SKIP_REWARD_ACTION] = True
        return mask

    # ------------------------------------------------------------------
    def _compute_reward(self, was_reward_pick: bool) -> float:
        assert self.run is not None
        combat = self.run.combat

        if was_reward_pick:
            # This step was a reward-pick/skip decision, which just started
            # a brand new fight -- resync the HP-swing trackers to that
            # fresh state instead of comparing against the just-finished
            # fight's final numbers (which would otherwise read as a huge,
            # meaningless HP swing at the fight boundary).
            self._prev_enemy_hp_total = sum(e.hp for e in combat.enemies)
            self._prev_player_hp = combat.player.hp
            return 0.0

        enemy_hp_total = sum(e.hp for e in combat.enemies)
        player_hp = combat.player.hp
        # Small dense shaping term: reward net HP swing in the player's
        # favor each step, dominated by the terminal +-1/+2 below.
        shaping = 0.01 * ((self._prev_enemy_hp_total - enemy_hp_total) - (self._prev_player_hp - player_hp))
        self._prev_enemy_hp_total = enemy_hp_total
        self._prev_player_hp = player_hp

        reward = shaping
        if combat.outcome == "win":
            reward += 1.0
            if self.run.outcome == "run_won":
                reward += 2.0  # extra bonus for clearing the whole run
        elif combat.outcome == "loss":
            reward -= 1.0
        return reward

    def _build_observation(self) -> np.ndarray:
        assert self.run is not None
        combat = self.run.combat
        p = combat.player
        feats: list[float] = [
            p.hp / max(p.max_hp, 1),
            min(p.block / 30.0, 3.0),
            min(p.strength / 10.0, 3.0),
            min(p.dexterity / 10.0, 3.0),
            min(p.vulnerable / 5.0, 3.0),
            min(p.weak / 5.0, 3.0),
            min(p.frail / 5.0, 3.0),
            p.energy / max(p.max_energy, 1),
            len(p.hand) / MAX_HAND_SIZE,
            len(p.draw_pile) / 20.0,
            1.0 if self.run.phase == "reward_pick" else 0.0,
            self.run.fight_index / max(self.run.fights_per_run - 1, 1),
        ]
        feats.extend(1.0 if power_id in p.powers else 0.0 for power_id in POWER_IDS_IN_OBS)

        for slot in range(MAX_HAND_SIZE):
            if slot < len(p.hand):
                ci = p.hand[slot]
                card = self.card_defs[ci.card_id]
                feats.extend(
                    [
                        1.0,
                        card.cost_for(ci.is_upgraded) / 3.0,
                        1.0 if ci.is_upgraded else 0.0,
                        1.0 if card.type == "Attack" else 0.0,
                        1.0 if card.type == "Skill" else 0.0,
                        1.0 if card.target == "single_enemy" else 0.0,
                        1.0 if card.target == "all_enemies" else 0.0,
                    ]
                )
            else:
                feats.extend([0.0] * HAND_CARD_FEATURES)

        for slot in range(MAX_ENEMIES):
            if slot < len(combat.enemies):
                e = combat.enemies[slot]
                feats.extend(
                    [
                        1.0 if e.alive else 0.0,
                        e.hp / max(e.max_hp, 1),
                        min(e.block / 30.0, 3.0),
                        min(e.strength / 10.0, 3.0),
                        min(e.vulnerable / 5.0, 3.0),
                        min(e.weak / 5.0, 3.0),
                    ]
                )
            else:
                feats.extend([0.0] * ENEMY_FEATURES)

        choices = self.run.pending_reward_choices or []
        for slot in range(REWARD_CHOICES):
            if slot < len(choices):
                card = self.card_defs[choices[slot]]
                feats.extend(
                    [
                        1.0,
                        card.cost / 3.0,
                        1.0 if card.type == "Attack" else 0.0,
                        1.0 if card.type == "Skill" else 0.0,
                    ]
                )
            else:
                feats.extend([0.0] * REWARD_CARD_FEATURES)

        return np.asarray(feats, dtype=np.float32)
