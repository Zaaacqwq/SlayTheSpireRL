from __future__ import annotations

import random
from .protocol import ActionCandidate, DecisionState


def random_action(state: DecisionState, rng: random.Random) -> ActionCandidate:
    if not state.candidates: raise RuntimeError("no legal candidates")
    return rng.choice(state.candidates)


def heuristic_action(state: DecisionState) -> ActionCandidate:
    priorities = {"play_card": 0, "use_potion": 1, "select_card_reward": 2, "choose_option": 3, "select_map_node": 4, "end_turn": 9}
    return min(state.candidates, key=lambda a: priorities.get(a.action, 5))
