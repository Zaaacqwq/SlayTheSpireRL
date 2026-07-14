"""Recurrent masked PPO over real-engine episodes.

Episodes come from curriculum stages (atomic ``start_combat`` resets or full
runs). Rewards follow the roadmap: terminal +1/-1, plus optional
``0.2 x potential-based`` floor-progress shaping for full-run stages, with a
terminal-only ablation switch. Recurrent hidden states are stored detached and
replayed as inputs during updates (no backpropagation through time).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Mapping, Sequence

import torch

from .agent import PolicyAgent, entities_global
from .curriculum import CurriculumStage, episode_config
from .engine import CombatConfig, EngineClient
from .entities import EntityVocab, candidate_entity_slots, encode_entity_batch
from .features import encode_candidates
from .losses import generalized_advantage_estimate, ppo_clipped_loss
from .observation import normalize_state
from .protocol import ActionCandidate, DecisionState

# Phases that belong to an ongoing combat; anything else after a combat reset
# means the fight is over (rewards, map, ...) and the episode is a win.
_COMBAT_PHASES = frozenset({"combat_play", "card_select", "bundle_select"})


@dataclass(frozen=True)
class PPOConfig:
    gamma: float = 0.999
    gae_lambda: float = 0.95
    clip: float = 0.2
    learning_rate: float = 3e-4
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    update_epochs: int = 4
    minibatch_size: int = 256
    episodes_per_iteration: int = 32
    max_episode_steps: int = 600
    floor_shaping: bool = True  # terminal-only ablation sets this False
    max_grad_norm: float = 0.5


@dataclass
class StoredStep:
    raw_state: Mapping[str, Any]
    candidates: tuple[ActionCandidate, ...]
    index: int
    logp: float
    value: float
    reward: float
    hidden: tuple[float, ...] | None


@dataclass
class EpisodeRecord:
    seed: str
    steps: list[StoredStep]
    outcome: bool | None
    truncated: bool
    error: str | None = None
    final_floor: float = 0.0
    advantages: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)


def _floor(state: DecisionState) -> float:
    context = state.raw.get("context") or {}
    return float(context.get("floor", state.raw.get("floor", 0)) or 0)


def _act(state: DecisionState) -> int:
    context = state.raw.get("context") or {}
    return int(context.get("act", state.raw.get("act", 0)) or 0)


LiveCallback = Callable[[Mapping[str, Any]], None]


def _emit_live(callback: LiveCallback | None, payload: Mapping[str, Any]) -> None:
    """Live observability must never be able to poison an episode."""
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        pass


def _state_summary(state: DecisionState) -> dict[str, Any]:
    raw = state.raw
    context = raw.get("context") or {}
    player = raw.get("player") or {}
    return {
        "phase": state.phase,
        "act": _act(state),
        "floor": _floor(state),
        "round": context.get("round", raw.get("round")),
        "hp": player.get("hp", raw.get("hp")),
        "max_hp": player.get("max_hp", raw.get("max_hp")),
        "energy": player.get("energy", raw.get("energy")),
    }


def _selected_label(state: DecisionState, action: ActionCandidate) -> str | None:
    """Resolve the model/entity label targeted by a compact protocol action."""
    args = action.args
    sources = {
        "card_index": ("hand", "cards", "options"),
        "potion_index": ("potions",),
        "relic_index": ("relics",),
        "option_index": ("options",),
        "bundle_index": ("bundles", "options"),
    }
    for key, collections in sources.items():
        if key not in args:
            continue
        for collection in collections:
            for item in state.raw.get(collection, []) or []:
                if item.get("index") == args[key]:
                    return str(
                        item.get("name") or item.get("title") or item.get("id")
                        or item.get("model_id") or item.get("text_key") or args[key]
                    )
    if action.action == "select_map_node":
        return f"({args.get('col', '?')}, {args.get('row', '?')})"
    return None


def run_episode(
    client: EngineClient,
    stage: CurriculumStage,
    seed: str,
    agent: PolicyAgent,
    config: PPOConfig,
    *,
    character: str = "Ironclad",
    ascension: int = 0,
    inference_lock: threading.Lock | None = None,
    greedy: bool = False,
    live_callback: LiveCallback | None = None,
) -> EpisodeRecord:
    episode = episode_config(stage, seed, character=character, ascension=ascension)
    steps: list[StoredStep] = []
    state: DecisionState | None = None
    _emit_live(live_callback, {
        "type": "episode_start", "status": "starting", "episode_id": seed,
        "seed": seed, "step": 0,
    })
    try:
        if isinstance(episode, CombatConfig):
            state = client.reset_combat(episode)
        else:
            state = client.reset(episode)
        hidden: tuple[float, ...] | None = None
        previous_floor = _floor(state)
        while True:
            outcome, done = _terminal_outcome(stage, state)
            if done:
                if steps:
                    steps[-1].reward += 1.0 if outcome else -1.0
                _emit_live(live_callback, {
                    "type": "episode_end", "status": "finished", "episode_id": seed,
                    "seed": seed, "step": len(steps), "outcome": outcome,
                    **_state_summary(state),
                })
                return EpisodeRecord(seed, steps, outcome, truncated=False, final_floor=_floor(state))
            if len(steps) >= config.max_episode_steps:
                _emit_live(live_callback, {
                    "type": "episode_end", "status": "truncated", "episode_id": seed,
                    "seed": seed, "step": len(steps), "outcome": None,
                    **_state_summary(state),
                })
                return EpisodeRecord(seed, steps, None, truncated=True, final_floor=_floor(state))
            if inference_lock:
                with inference_lock:
                    step = agent.act(state.raw, state.candidates, hidden, greedy=greedy)
            else:
                step = agent.act(state.raw, state.candidates, hidden, greedy=greedy)
            action = state.candidates[step.index]
            result = client.step(action)
            reward = 0.0
            if config.floor_shaping and not stage.is_combat:
                new_floor = _floor(result.state)
                reward += 0.2 * (config.gamma * new_floor - previous_floor)
                previous_floor = new_floor
            steps.append(StoredStep(
                dict(state.raw), tuple(state.candidates), step.index,
                step.logp, step.value, reward, hidden,
            ))
            _emit_live(live_callback, {
                "type": "action", "status": "running", "episode_id": seed,
                "seed": seed, "step": len(steps), **_state_summary(state),
                "action": action.command(), "selected_label": _selected_label(state, action),
                "target": action.args.get("target_index"), "reward": reward,
                "value": step.value, "logp": step.logp,
            })
            hidden = step.hidden
            state = result.state
    except Exception as exc:  # engine faults poison the worker's episode only
        summary = _state_summary(state) if state is not None else {}
        _emit_live(live_callback, {
            "type": "episode_error", "status": "error", "episode_id": seed,
            "seed": seed, "step": len(steps), "error": type(exc).__name__, **summary,
        })
        return EpisodeRecord(seed, steps, None, truncated=False, error=type(exc).__name__,
                             final_floor=_floor(state) if state is not None else 0.0)


def _terminal_outcome(stage: CurriculumStage, state: DecisionState) -> tuple[bool | None, bool]:
    if state.phase == "game_over":
        return bool(state.raw.get("victory")), True
    if stage.is_combat and state.phase not in _COMBAT_PHASES:
        return True, True  # survived the fight; rewards screen reached
    if stage.max_act is not None and _act(state) > stage.max_act:
        return True, True
    return None, False


def finalize_episode(record: EpisodeRecord, config: PPOConfig) -> None:
    """Attach GAE advantages/returns; truncated episodes bootstrap the last value."""
    if not record.steps:
        return
    rewards = torch.tensor([s.reward for s in record.steps], dtype=torch.float32)
    values = torch.tensor([s.value for s in record.steps], dtype=torch.float32)
    dones = torch.zeros_like(rewards)
    if not record.truncated:
        dones[-1] = 1.0
    next_value = values[-1] if record.truncated else torch.tensor(0.0)
    advantages, returns = generalized_advantage_estimate(
        rewards, values, dones, config.gamma, config.gae_lambda, next_value=next_value,
    )
    record.advantages = advantages.tolist()
    record.returns = returns.tolist()


def encode_update_batch(steps: Sequence[StoredStep], vocab: EntityVocab, hidden_size: int) -> dict[str, torch.Tensor]:
    observations = [normalize_state(s.raw_state) for s in steps]
    entities = encode_entity_batch(observations, vocab)
    width = max(len(s.candidates) for s in steps)
    candidate_rows, masks, slot_rows = [], [], []
    for s, observation in zip(steps, observations):
        encoded = encode_candidates(s.candidates)
        padding = width - encoded.shape[0]
        candidate_rows.append(torch.nn.functional.pad(encoded, (0, 0, 0, padding)))
        masks.append(torch.tensor([True] * encoded.shape[0] + [False] * padding))
        slots = candidate_entity_slots(observation, s.candidates)
        slot_rows.append(torch.tensor(slots + [-1] * padding, dtype=torch.long))
    hiddens = torch.stack([
        torch.zeros(hidden_size) if s.hidden is None
        else torch.tensor(s.hidden, dtype=torch.float32)
        for s in steps
    ])
    return {
        "global": torch.stack([entities_global(o)[0] for o in observations]),
        "entities": entities,
        "candidates": torch.stack(candidate_rows),
        "mask": torch.stack(masks),
        "actions": torch.tensor([s.index for s in steps], dtype=torch.long),
        "old_logp": torch.tensor([s.logp for s in steps], dtype=torch.float32),
        "hidden": hiddens,
        "slots": torch.stack(slot_rows),
    }


def ppo_update_epoch(
    model, optimizer, records: Sequence[EpisodeRecord], vocab: EntityVocab, config: PPOConfig,
    *, generator: torch.Generator | None = None,
) -> dict[str, float]:
    steps: list[StoredStep] = []
    advantages: list[float] = []
    returns: list[float] = []
    for record in records:
        steps.extend(record.steps)
        advantages.extend(record.advantages)
        returns.extend(record.returns)
    if not steps:
        return {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
    advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
    if advantage_tensor.numel() > 1 and float(advantage_tensor.std()) > 1e-8:
        advantage_tensor = (advantage_tensor - advantage_tensor.mean()) / (advantage_tensor.std() + 1e-8)
    return_tensor = torch.tensor(returns, dtype=torch.float32)

    device = next(model.parameters()).device
    advantage_tensor = advantage_tensor.to(device)
    return_tensor = return_tensor.to(device)
    order = torch.randperm(len(steps), generator=generator)
    totals = {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0}
    batches = 0
    for start in range(0, len(steps), config.minibatch_size):
        chosen = order[start:start + config.minibatch_size].tolist()
        batch = encode_update_batch([steps[i] for i in chosen], vocab, model.hidden_size)
        batch = {
            key: ({k: v.to(device) for k, v in value.items()}
                  if isinstance(value, dict) else value.to(device))
            for key, value in batch.items()
        }
        logits, values, _ = model(
            batch["global"], batch["entities"], batch["candidates"], batch["mask"],
            candidate_slots=batch["slots"], hidden=batch["hidden"],
        )
        log_probs = torch.log_softmax(logits, dim=-1)
        new_logp = log_probs.gather(-1, batch["actions"].unsqueeze(-1)).squeeze(-1)
        policy_loss = ppo_clipped_loss(
            batch["old_logp"], new_logp, advantage_tensor[chosen], config.clip,
        )
        value_loss = torch.nn.functional.mse_loss(values, return_tensor[chosen])
        probs = log_probs.exp()
        entropy = -(probs * log_probs.masked_fill(~batch["mask"], 0.0)).sum(-1).mean()
        loss = policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
        optimizer.step()
        totals["loss"] += float(loss.detach())
        totals["policy_loss"] += float(policy_loss.detach())
        totals["value_loss"] += float(value_loss.detach())
        totals["entropy"] += float(entropy.detach())
        batches += 1
    return {k: v / max(batches, 1) for k, v in totals.items()}
