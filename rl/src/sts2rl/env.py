from __future__ import annotations

from typing import Any
from .engine import EngineClient, RunConfig
from .observation import NormalizedObservation, normalize_state
from .protocol import ActionCandidate, DecisionState, StepResult


class STS2Env:
    """Gymnasium-shaped environment without a hard gymnasium dependency."""
    def __init__(self, client: EngineClient, config: RunConfig):
        self.client, self.config = client, config
        self.state: DecisionState | None = None
        self.observation: NormalizedObservation | None = None
        self.episode_steps = 0

    def reset(self, *, seed: str | None = None) -> tuple[NormalizedObservation, dict[str, Any]]:
        if seed is not None:
            self.config = RunConfig(self.config.character, seed, self.config.ascension, self.config.lang)
        self.state = self.client.reset(self.config)
        self.observation = normalize_state(self.state.raw)
        self.episode_steps = 0
        return self.observation, {"state_hash": self.state.state_hash, "candidates": self.state.candidates}

    def step(self, action: ActionCandidate) -> tuple[NormalizedObservation, float, bool, bool, dict[str, Any]]:
        if self.state is None:
            raise RuntimeError("reset must be called before step")
        if action not in self.state.candidates:
            raise ValueError("action is not legal in current state")
        result: StepResult = self.client.step(action)
        self.state, self.observation = result.state, normalize_state(result.state.raw)
        self.episode_steps += 1
        terminated = result.terminated
        outcome = result.state.raw.get("victory") if terminated else None
        reward = 1.0 if outcome is True else -1.0 if terminated else 0.0
        return self.observation, reward, terminated, False, {"state_hash": self.state.state_hash, "candidates": self.state.candidates}
