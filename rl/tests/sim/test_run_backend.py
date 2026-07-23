"""ZhiyueRunBackend: whole-run driving over the vendored RunManager.

Asserts the run adapter drives every act1 phase, keeps our obs schema clean
(no unknown-field warnings), advances act/floor, terminates at game_over, and
that the dual-mode backend still serves combat stages via delegation.
"""

from __future__ import annotations

import random

from sts2rl.curriculum import boss_loadout_from_state
from sts2rl.engine import CombatConfig, RunConfig
from sts2rl.observation import normalize_state
from sts2rl.protocol import DECISIONS
from sts2rl.sim import ZhiyueRunBackend


def _heuristic(state, rng):
    non_end = [c for c in state.candidates if c.action != "end_turn"]
    if state.phase == "combat_play" and non_end:
        attacks = [c for c in non_end if "target_index" in c.args]
        return rng.choice(attacks or non_end)
    return rng.choice(state.candidates)


def test_run_backend_drives_all_act1_phases_cleanly() -> None:
    rng = random.Random(0)
    seen_phases: set[str] = set()
    reached_game_over = 0
    with ZhiyueRunBackend() as backend:
        for run in range(8):
            state = backend.reset(RunConfig("Ironclad", f"rb-{run}"))
            assert state.phase == "map_select"
            prev_cumulative = -1.0
            steps = 0
            while state.phase != "game_over" and steps < 3000:
                seen_phases.add(state.phase)
                assert state.phase in DECISIONS
                assert state.candidates, f"no candidates at {state.phase}"
                # obs schema stays within known fields (no telemetry warnings).
                assert not normalize_state(state.raw).warnings
                ctx = state.raw.get("context") or {}
                cumulative = (int(ctx.get("act", 1)) - 1) * 100 + float(ctx.get("floor", 0))
                assert cumulative >= prev_cumulative  # act/floor never regress
                prev_cumulative = cumulative
                state = backend.step(_heuristic(state, rng)).state
                steps += 1
            if state.phase == "game_over":
                reached_game_over += 1
                assert isinstance(state.raw.get("victory"), bool)
    assert reached_game_over >= 1
    # The main act1 decision points are all exercised by 8 heuristic runs.
    assert {"map_select", "combat_play", "card_reward"} <= seen_phases


def test_run_backend_reaches_act2_with_enough_hp() -> None:
    """A survivable player clears act1 into act2 — exercising the boss fight,
    boss-relic pick, and act transition — which fires our max_act clear."""
    rng = random.Random(1)
    reached_act2 = False
    boss_loadout = None
    with ZhiyueRunBackend() as backend:
        for run in range(6):
            state = backend.reset(RunConfig("Ironclad", f"rb-god-{run}"))
            backend._mgr.run_state.player.max_hp = 9999  # noqa: SLF001
            backend._mgr.run_state.player.current_hp = 9999  # noqa: SLF001
            state = backend._decision_state()  # noqa: SLF001
            steps = 0
            while state.phase != "game_over" and steps < 4000:
                boss_loadout = boss_loadout or boss_loadout_from_state(state.raw)
                if int((state.raw.get("context") or {}).get("act", 1)) >= 2:
                    reached_act2 = True
                    break
                state = backend.step(_heuristic(state, rng)).state
                steps += 1
            if reached_act2:
                break
        assert boss_loadout is not None
        replay = backend.reset_combat(CombatConfig(
            character="Ironclad", seed="rb-boss-replay",
            encounter=boss_loadout.encounter or "",
            deck=boss_loadout.deck, relics=boss_loadout.relics,
            potions=boss_loadout.potions,
            hp=boss_loadout.hp, max_hp=boss_loadout.max_hp,
        ))
        assert replay.phase == "combat_play"
        assert replay.candidates
    assert reached_act2


def test_run_backend_dual_mode_serves_combat() -> None:
    """The same backend serves combat stages by delegating to SimCombatBackend."""
    with ZhiyueRunBackend() as backend:
        state = backend.reset_combat(
            CombatConfig(character="IRONCLAD", seed="rb-combat",
                         encounter="SHRINKER_BEETLE_WEAK", hp=80, max_hp=80)
        )
        assert state.phase == "combat_play"
        assert state.candidates
        result = backend.step(next(c for c in state.candidates if c.action == "end_turn"))
        assert result.state.phase in ("combat_play", "game_over")
