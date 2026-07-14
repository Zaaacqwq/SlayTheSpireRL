"""Contract tests for the data path that feeds the policy: trajectories must be
usable as behavior-cloning supervision, which requires the recorded action to be
resolvable against the candidates it was actually chosen from."""
from __future__ import annotations

import torch

from sts2rl.engine import RunConfig
from sts2rl.evaluator import collect_episode
from sts2rl.features import CANDIDATE_FEATURE_DIM, GLOBAL_FEATURE_DIM, encode_batch, encode_candidates
from sts2rl.model import CandidatePolicy
from sts2rl.protocol import ActionCandidate, DecisionState, StepResult


def _decision(phase: str, floor: int) -> DecisionState:
    raw = {"decision": phase, "context": {"floor": floor, "act": 1}, "player": {"hp": 70, "max_hp": 80}}
    if phase == "combat_play":
        raw["hand"] = [{"index": 0, "id": "Strike", "cost": 1, "is_playable": True}]
        raw["enemies"] = [{"index": 0, "is_alive": True}]
    return DecisionState(raw, legal_candidates(phase))


def legal_candidates(phase: str) -> tuple[ActionCandidate, ...]:
    if phase == "combat_play":
        return (ActionCandidate("play_card", {"card_index": 0}), ActionCandidate("end_turn", {}))
    return (ActionCandidate("choose_option", {"option_index": 0}),)


class FakeClient:
    """Two decisions, then game_over."""
    version = "0.2.0"

    def __init__(self) -> None:
        self.floor = 0

    def reset(self, config: RunConfig) -> DecisionState:
        self.floor = 0
        return _decision("event_choice", 0)

    def step(self, action: ActionCandidate) -> StepResult:
        self.floor += 1
        if self.floor == 1:
            return StepResult(_decision("combat_play", 1), terminated=False)
        return StepResult(DecisionState({"decision": "game_over", "victory": False, "context": {}, "player": {}}, ()), terminated=True)


def test_transition_records_the_state_the_action_was_chosen_from():
    _, transitions = collect_episode(FakeClient(), RunConfig("Ironclad", "seed-1"))
    assert transitions, "expected at least one transition"
    for transition in transitions:
        assert transition.action in transition.legal_actions, (
            "recorded action must be one of the candidates offered at that decision; "
            "otherwise the behavior-cloning target index is undefined"
        )
        assert transition.state["decision"] == transition.normalized["phase"], (
            "state and normalized observation must describe the same decision point"
        )


def test_terminal_reward_lands_on_the_final_transition():
    result, transitions = collect_episode(FakeClient(), RunConfig("Ironclad", "seed-1"))
    assert result.steps == len(transitions)
    assert transitions[-1].terminated and transitions[-1].reward == -1.0
    assert not any(t.terminated for t in transitions[:-1])


def test_encode_candidates_is_fixed_width_and_masks_padding():
    candidates = legal_candidates("combat_play")
    encoded = encode_candidates(candidates)
    assert encoded.shape == (2, CANDIDATE_FEATURE_DIM)

    observation = _decision("combat_play", 1)
    batch = encode_batch([(observation.raw, candidates, 1), (_decision("event_choice", 0).raw, legal_candidates("event_choice"), 0)])
    assert batch["global"].shape == (2, GLOBAL_FEATURE_DIM)
    assert batch["candidates"].shape == (2, 2, CANDIDATE_FEATURE_DIM)
    assert batch["mask"].tolist() == [[True, True], [True, False]], "shorter candidate lists must be padded and masked"
    assert batch["targets"].tolist() == [1, 0]


def test_encoded_batch_drives_the_policy():
    batch = encode_batch([(_decision("combat_play", 1).raw, legal_candidates("combat_play"), 0)])
    model = CandidatePolicy()
    logits, value = model(batch["global"], batch["candidates"], batch["mask"])
    assert logits.shape == (1, 2) and value.shape == (1,)
    assert torch.isfinite(logits).all()


def test_map_candidates_are_distinguishable():
    from sts2rl.features import encode_candidate
    from sts2rl.protocol import ActionCandidate
    a = encode_candidate(ActionCandidate("select_map_node", {"col": 0, "row": 1}))
    b = encode_candidate(ActionCandidate("select_map_node", {"col": 2, "row": 1}))
    assert a != b


def test_potion_and_relic_candidates_are_distinguishable():
    from sts2rl.features import encode_candidate
    from sts2rl.protocol import ActionCandidate
    p0 = encode_candidate(ActionCandidate("use_potion", {"potion_index": 0}))
    p1 = encode_candidate(ActionCandidate("use_potion", {"potion_index": 1}))
    assert p0 != p1


def test_multi_card_selections_are_distinguishable():
    from sts2rl.features import encode_candidate
    a = encode_candidate(ActionCandidate("select_cards", {"indices": "0,1"}))
    b = encode_candidate(ActionCandidate("select_cards", {"indices": "0,2"}))
    assert a != b


def test_shop_actions_have_distinct_action_features():
    from sts2rl.features import encode_candidate
    actions = [
        ActionCandidate("buy_card", {"card_index": 0}),
        ActionCandidate("buy_relic", {"relic_index": 0}),
        ActionCandidate("buy_potion", {"potion_index": 0}),
    ]
    assert len({encode_candidate(action) for action in actions}) == len(actions)


def test_map_choice_entities_embed_room_type():
    from sts2rl.entities import entity_key
    assert entity_key({"col": 0, "row": 1, "type": "Rest", "entity_type": "choice", "id": "UNK"}) == "Rest"
