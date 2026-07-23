from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from sts2rl.ppo import EpisodeRecord, StoredStep
from sts2rl.protocol import ActionCandidate
from sts2rl.telemetry import (
    action_mix,
    depth_profile,
    episode_return,
    offered_actions,
    reward_health,
)

GAMMA = 1.0


def _record(seed, rewards, actions, outcome, final_floor=0.0, error=None, final_act=1):
    steps = []
    for reward, action in zip(rewards, actions):
        candidates = (ActionCandidate(action, {}), ActionCandidate("end_turn", {}))
        steps.append(StoredStep({}, candidates, 0, -0.1, 0.0, reward, None))
    return EpisodeRecord(seed, steps, outcome, truncated=False, error=error,
                         final_floor=final_floor, final_act=final_act)


def test_reward_health_flags_the_inversion_that_shipped_to_v5():
    # the real numbers: a win returned +0.65 while a deep death returned +1.76
    win = _record("w", [0.65], ["play_card"], True, final_floor=17)
    loss = _record("l", [1.76], ["play_card"], False, final_floor=17)
    health = reward_health([win, loss], GAMMA)
    assert health["inverted"] is True
    assert health["win_return"] == 0.65 and health["loss_return"] == 1.76


def test_reward_health_is_quiet_when_winning_pays_more():
    win = _record("w", [0.65], ["play_card"], True)
    loss = _record("l", [-1.05], ["play_card"], False)
    assert reward_health([win, loss], GAMMA)["inverted"] is False


def test_reward_health_cannot_conclude_from_one_sided_data():
    only_losses = [_record("l", [-1.0], ["play_card"], False)]
    health = reward_health(only_losses, GAMMA)
    assert health["inverted"] is False and health["win_return"] is None


def test_action_mix_exposes_an_action_the_policy_can_never_take():
    # potions were offered as candidates but chosen zero times... except they were
    # never even offered, which is the distinction offered_actions() draws
    records = [_record("a", [0, 0, 0], ["play_card", "play_card", "end_turn"], True)]
    mix = action_mix(records)
    assert mix == {"play_card": 0.6667, "end_turn": 0.3333}
    assert "use_potion" not in mix
    # and the policy was never shown one either — a bug, not a preference
    assert "use_potion" not in offered_actions(records)


def test_depth_profile_separates_reaching_the_boss_from_beating_it():
    # v5's real shape: most runs reach the boss door, none convert
    records = [_record(f"d{i}", [0], ["play_card"], False, final_floor=17) for i in range(8)]
    records += [_record(f"s{i}", [0], ["play_card"], False, final_floor=6) for i in range(2)]
    profile = depth_profile(records)
    assert profile["reached_boss"] == 8
    assert profile["reached_boss_rate"] == 0.8
    assert profile["boss_conversion"] == 0.0   # avg_floor alone would hide this


def test_depth_profile_counts_a_win_as_having_reached_the_boss():
    # a win records the deepest floor, but even so it must never be missed
    win = _record("w", [1.0], ["play_card"], True, final_floor=17)
    death = _record("d", [-1.0], ["play_card"], False, final_floor=17)
    profile = depth_profile([win, death])
    assert profile["reached_boss"] == 2
    assert profile["boss_conversion"] == 0.5


def test_depth_profile_splits_the_funnel_per_act_on_a_multi_act_stage():
    # acts_12 shape: everyone clears act1 and reaches the act2 boss, few clear act2.
    # cumulative floor: act1 boss ~17, act2 boss ~34; a win reports final_act = 3.
    records = []
    # 2 cleared act1 then died in act2 short of its boss (floor 25, act2)
    records += [_record(f"a{i}", [0], ["play_card"], False, final_floor=25, final_act=2)
                for i in range(2)]
    # 6 reached the act2 boss door (floor 34) and died there (still act2)
    records += [_record(f"b{i}", [0], ["play_card"], False, final_floor=34, final_act=2)
                for i in range(6)]
    # 2 cleared act2 (won): deepest floor 34, final_act reported as 3
    records += [_record(f"w{i}", [1.0], ["play_card"], True, final_floor=34, final_act=3)
                for i in range(2)]
    profile = depth_profile(records, acts=2)
    # act1 is nearly solved: all 10 crossed the act1 door and all 10 cleared act1
    assert profile["reached_act1_rate"] == 1.0
    assert profile["cleared_act1_rate"] == 1.0
    # act2 is the wall: 8 reached its boss door (6 died there + 2 won), only 2 cleared
    assert profile["reached_act2_rate"] == 0.8
    assert profile["cleared_act2_rate"] == 0.2
    # a stage told it spans 2 acts must not fabricate an act3 series
    assert "reached_act3_rate" not in profile


def test_depth_profile_single_act_funnel_mirrors_the_legacy_pair():
    # act1-only stage: act1 keys must equal the flat reached/conversion so old runs read the same
    win = _record("w", [1.0], ["play_card"], True, final_floor=17, final_act=2)
    death = _record("d", [-1.0], ["play_card"], False, final_floor=17, final_act=1)
    profile = depth_profile([win, death], acts=1)
    assert profile["reached_act1_rate"] == profile["reached_boss_rate"] == 1.0
    assert profile["cleared_act1_rate"] == 0.5  # == boss_conversion here (1 win / 2 reached)
    assert "reached_act2_rate" not in profile


def test_episode_return_discounts():
    record = _record("r", [1.0, 1.0], ["play_card", "end_turn"], True)
    assert episode_return(record, 0.5) == pytest.approx(1.5)
