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
            action = random_action(state, rng) if policy == "random" else heuristic_action(state)
            before = normalize_state(state.raw)
            result = client.step(action); state = result.state
            transitions.append(Transition(config.seed, steps, state.raw, {"phase": before.phase, "global": before.global_features}, [a.command() for a in state.candidates], action.command(), 1.0 if result.terminated and state.raw.get("victory") is True else -1.0 if result.terminated else 0.0, result.terminated, str(state.raw.get("victory")) if result.terminated else None, client.version))
            steps += 1
        return EpisodeResult(config.character, config.seed, state.raw.get("victory") if state.phase == "game_over" else None, steps), transitions
    except Exception as exc:
        return EpisodeResult(config.character, config.seed, None, steps, type(exc).__name__), transitions
