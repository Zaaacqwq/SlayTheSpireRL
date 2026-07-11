from __future__ import annotations

from dataclasses import dataclass
import random
from .engine import EngineClient, RunConfig
from .policy import random_action, heuristic_action
from .trajectory import Transition
from .observation import normalize_state


@dataclass
class EpisodeResult:
    character: str
    seed: str
    outcome: bool | None
    steps: int
    error: str | None = None


def collect_episode(client: EngineClient, config: RunConfig, *, policy="random", max_steps: int = 2000, seed: int = 0) -> tuple[EpisodeResult, list[Transition]]:
    state = client.reset(config); transitions: list[Transition] = []; rng = random.Random(seed); steps = 0
    try:
        while steps < max_steps and state.phase != "game_over":
            # Everything describing the decision must be captured from the state the
            # action was chosen in. Recording the post-step state alongside the action
            # leaves the behavior-cloning target index undefined, because the action is
            # not generally a member of the next state's candidate list.
            decision, offered = state.raw, state.candidates
            observed = normalize_state(decision)
            action = random_action(state, rng) if policy == "random" else heuristic_action(state)
            result = client.step(action); state = result.state
            outcome = state.raw.get("victory") if result.terminated else None
            transitions.append(Transition(config.seed, steps, decision, {"phase": observed.phase, "global": observed.global_features}, [a.command() for a in offered], action.command(), 1.0 if outcome is True else -1.0 if result.terminated else 0.0, result.terminated, str(outcome) if result.terminated else None, client.version))
            steps += 1
        return EpisodeResult(config.character, config.seed, state.raw.get("victory") if state.phase == "game_over" else None, steps), transitions
    except Exception as exc:
        return EpisodeResult(config.character, config.seed, None, steps, type(exc).__name__), transitions
