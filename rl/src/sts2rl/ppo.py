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
from .entities import EntityVocab, MAX_CANDIDATE_BINDINGS, candidate_entity_bindings, encode_entity_batch
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
    # NOTE(v7-cont): reverted 0.003 -> 0.01 to reproduce m2_v7_clean_init0's
    # single-act1 training (0.003 was tuned for sparse multi-act stages, commit
    # 0ae4fea). Restore to 0.003 before resuming any acts_12 / full-run / sim run.
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    update_epochs: int = 4
    minibatch_size: int = 256
    episodes_per_iteration: int = 32
    max_episode_steps: int = 600
    floor_shaping: bool = True  # terminal-only ablation sets this False
    # Reward for clearing an act boss (the act index advances). Floor shaping is
    # potential-based (net zero), so on a multi-act stage "reach the act1 boss"
    # and "die on floor 3" both score -1: no signal keeps the policy from
    # entropy-collapsing. This milestone restores a dense per-act progress reward
    # for *beating* a boss (not just reaching it, so it can't relearn walk-and-die).
    # Set to 1.0 to match the +1 the act1 stage paid for clearing act1 — 0.4 only
    # slowed the collapse (entropy still crept 0.75->0.82) instead of stopping it.
    # NOTE(v7-cont): reverted 1.0 -> 0.0 to reproduce m2_v7_clean_init0, which
    # trained before this milestone existed (act1 win was +1 terminal only; the
    # bonus fires on the act1->act2 step and would ~double the win reward, an
    # off-distribution jump for the resumed checkpoint). Restore to 1.0 before
    # resuming any multi-act stage where boss-clear collapse is the concern.
    act_clear_bonus: float = 0.0
    # Potential-based HP shaping (0 disables). Phi(s) = coef * hp/max_hp, rewarded
    # as gamma*Phi(s') - Phi(s) with Phi forced to 0 at every terminal — the same
    # policy-invariant construction as floor_shaping, so it cannot recreate the
    # "walk to the boss and die" exploit a flat damage penalty would. Its job is
    # credit assignment: chip damage many floors before a death is a long-horizon
    # signal the sparse -1 (discounted over hundreds of steps at gamma 0.999)
    # barely reaches. Motivated by the acts_12 attrition funnel — half of act-1
    # clearers die crossing act 2, entering the fatal fight at a median 41% HP.
    hp_shaping_coef: float = 0.0
    max_grad_norm: float = 0.5
    target_kl: float | None = None
    hard_kl_stop: float | None = None


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
    final_floor: float = 0.0  # CUMULATIVE across acts (act2 floor 3 = 20), not per-act
    final_act: int = 1
    advantages: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    noop_steps: int = 0


def _action_key(action: ActionCandidate) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """Hashable identity for an action. ``command()`` returns an unhashable dict,
    so build a stable key from the action name plus its (index-valued) args."""
    return (action.action, tuple(sorted((action.args or {}).items())))


class NoopGuard:
    """Breaks engine no-op loops. Some actions fail inside the engine, have their
    exception swallowed, and return a byte-identical decision state — notably shop
    ``buy_potion`` into a full potion belt. A greedy policy then re-selects the
    same dead action forever until step-limit truncation. This remembers, per
    decision state, which actions proved inert there and stops offering them, so
    the policy is pushed onto a progressing action (a shop always still offers
    ``leave_room``). Shared by the training and evaluation rollout loops."""

    def __init__(self) -> None:
        self._banned: dict[str, set[tuple[str, tuple[tuple[str, Any], ...]]]] = {}
        self.noop_steps = 0

    def offer(self, state: DecisionState) -> tuple[ActionCandidate, ...]:
        """The candidates worth presenting to the policy: the full set minus any
        proven-inert here. Never returns empty — falls back to the full set."""
        banned = self._banned.get(state.state_hash)
        if not banned:
            return state.candidates
        effective = tuple(c for c in state.candidates if _action_key(c) not in banned)
        return effective or state.candidates

    def record(self, before_hash: str, after_hash: str, action: ActionCandidate) -> bool:
        """Note the transition; if it left the state byte-identical, ban the action
        in that state so it is not offered again. Returns True on a no-op."""
        if before_hash == after_hash:
            self._banned.setdefault(before_hash, set()).add(_action_key(action))
            self.noop_steps += 1
            return True
        return False


def _floor(state: DecisionState) -> float:
    context = state.raw.get("context") or {}
    return float(context.get("floor", state.raw.get("floor", 0)) or 0)


def _act(state: DecisionState) -> int:
    context = state.raw.get("context") or {}
    return int(context.get("act", state.raw.get("act", 0)) or 0)


# The engine numbers floors per act (each act restarts at 1, boss ~17), so the
# raw floor caps at ~17 whether the run died at the act1 boss or cleared act2.
# For depth telemetry we accumulate a whole act's span when the act advances, so
# "floor" reads as true progress: act1 1-17, act2 18-34, act3 35+.
ACT_FLOOR_SPAN = 17


def _cumulative_floor(state: DecisionState) -> float:
    return (_act(state) - 1) * ACT_FLOOR_SPAN + _floor(state)


def _hp_fraction(state: DecisionState, previous: float) -> float:
    """Player HP as a fraction of max, the potential for HP shaping. Non-combat
    screens (some map/shop/event states) can omit HP; those return ``previous``
    so the potential stays flat across them instead of injecting a spurious swing
    to zero and back."""
    raw = state.raw
    player = raw.get("player") or {}
    hp = player.get("hp", raw.get("hp"))
    max_hp = player.get("max_hp", raw.get("max_hp"))
    if not isinstance(hp, (int, float)) or not isinstance(max_hp, (int, float)) or max_hp <= 0:
        return previous
    return max(0.0, min(1.0, hp / max_hp))


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
    # Telemetry, not reward: the deepest floor reached, not the floor of the terminal
    # state. The state after an act completion carries no floor, so a win used to be
    # recorded as floor 0 — dragging avg_floor *down* for every episode actually won.
    # Defined before the try so a failing reset cannot leave it unbound.
    deepest_floor = 0.0
    deepest_act = 1
    noop_guard = NoopGuard()
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
        previous_floor = _floor(state)  # per-act, for potential-based shaping (unchanged)
        previous_hp = _hp_fraction(state, 1.0)  # runs start at full HP
        deepest_floor = _cumulative_floor(state)
        while True:
            deepest_act = max(deepest_act, _act(state))
            deepest_floor = max(deepest_floor, _cumulative_floor(state))
            outcome, done = _terminal_outcome(stage, state)
            if done:
                if steps:
                    steps[-1].reward += 1.0 if outcome else -1.0
                _emit_live(live_callback, {
                    "type": "episode_end", "status": "finished", "episode_id": seed,
                    "seed": seed, "step": len(steps), "outcome": outcome,
                    **_state_summary(state),
                })
                return EpisodeRecord(seed, steps, outcome, truncated=False,
                                     final_floor=deepest_floor, final_act=deepest_act,
                                     noop_steps=noop_guard.noop_steps)
            if len(steps) >= config.max_episode_steps:
                _emit_live(live_callback, {
                    "type": "episode_end", "status": "truncated", "episode_id": seed,
                    "seed": seed, "step": len(steps), "outcome": None,
                    **_state_summary(state),
                })
                return EpisodeRecord(seed, steps, None, truncated=True,
                                     final_floor=deepest_floor, final_act=deepest_act,
                                     noop_steps=noop_guard.noop_steps)
            current_hash = state.state_hash
            # The policy only ever sees — and the trajectory only ever records — the
            # candidates actually presented, so PPO's re-scored log-probs stay
            # consistent with the sampled action's candidate set.
            effective = noop_guard.offer(state)
            if inference_lock:
                with inference_lock:
                    step = agent.act(state.raw, effective, hidden, greedy=greedy)
            else:
                step = agent.act(state.raw, effective, hidden, greedy=greedy)
            action = effective[step.index]
            result = client.step(action)
            noop_guard.record(current_hash, result.state.state_hash, action)
            reward = 0.0
            # Both potential-based shapers zero their potential at any terminal, so
            # this "is the next state terminal?" test is shared by both.
            _, next_done = _terminal_outcome(stage, result.state)
            if config.floor_shaping and not stage.is_combat:
                # Potential-based shaping is policy-invariant only when EVERY terminal
                # state has potential zero. The engine drops `floor` from the state that
                # follows an act completion, so a win already had Phi = 0 — but a death
                # lands on `game_over`, which still carries `floor`, leaving
                # Phi = 0.2*floor. That asymmetry handed each deep death a ~+2.9 shaped
                # bonus that no win could earn, and made dying at the boss (mean return
                # +1.76) strictly better than beating it (+0.65). Every one of nine deep
                # deaths outscored every win. The policy was faithfully learning the
                # objective we gave it: walk to the boss, then die.
                new_floor = 0.0 if next_done else _floor(result.state)
                reward += 0.2 * (config.gamma * new_floor - previous_floor)
                previous_floor = new_floor
            if config.hp_shaping_coef and not stage.is_combat:
                # Same policy-invariant construction as the floor shaper: Phi = 0 at
                # terminals (a death lands on hp 0 anyway; a win must be forced to 0
                # like the floor above so no win-with-HP transition is penalised).
                new_hp = 0.0 if next_done else _hp_fraction(result.state, previous_hp)
                reward += config.hp_shaping_coef * (config.gamma * new_hp - previous_hp)
                previous_hp = new_hp
            # Milestone: beating an act boss advances the act index. A direct
            # (non-potential) reward so clearing act N is strictly better than
            # dying in it — the signal the policy-invariant shaping cannot give.
            if not stage.is_combat and _act(result.state) > _act(state):
                reward += config.act_clear_bonus
            steps.append(StoredStep(
                dict(state.raw), tuple(effective), step.index,
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
        error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        _emit_live(live_callback, {
            "type": "episode_error", "status": "error", "episode_id": seed,
            "seed": seed, "step": len(steps), "error": error, **summary,
        })
        return EpisodeRecord(seed, steps, None, truncated=False, error=error,
                             final_floor=deepest_floor, final_act=deepest_act,
                                     noop_steps=noop_guard.noop_steps)


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
        slots = candidate_entity_bindings(observation, s.candidates)
        slot_rows.append(torch.tensor(
            slots + [[-1] * MAX_CANDIDATE_BINDINGS] * padding, dtype=torch.long,
        ))
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


def _length_aware_batches(
    steps: Sequence[StoredStep], minibatch_size: int,
    generator: torch.Generator | None = None,
) -> list[list[int]]:
    """Shuffle once, then batch similarly-sized observations together.

    Full map states carry roughly an order of magnitude more entity tokens than
    ordinary combat states. Random mixing pads every sample to the map width and
    wastes quadratic Transformer attention. Batch and row order remain random;
    only padding-compatible samples are packed together.
    """
    if minibatch_size <= 0:
        raise ValueError("minibatch_size must be positive")
    if not steps:
        return []
    shuffled = torch.randperm(len(steps), generator=generator).tolist()
    shuffled.sort(key=lambda index: (
        len(normalize_state(steps[index].raw_state).entities),
        len(steps[index].candidates),
    ))
    batches = [
        shuffled[start:start + minibatch_size]
        for start in range(0, len(shuffled), minibatch_size)
    ]
    result: list[list[int]] = []
    for batch_index in torch.randperm(len(batches), generator=generator).tolist():
        batch = batches[batch_index]
        within = torch.randperm(len(batch), generator=generator).tolist()
        result.append([batch[index] for index in within])
    return result


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
        return {
            "loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
            "approx_kl": 0.0, "clip_fraction": 0.0, "grad_norm": 0.0,
            "grad_norm_clipped": 0.0, "explained_variance": 0.0,
            "minibatches": 0.0,
        }
    advantage_tensor = torch.tensor(advantages, dtype=torch.float32)
    if advantage_tensor.numel() > 1 and float(advantage_tensor.std()) > 1e-8:
        advantage_tensor = (advantage_tensor - advantage_tensor.mean()) / (advantage_tensor.std() + 1e-8)
    return_tensor = torch.tensor(returns, dtype=torch.float32)

    device = next(model.parameters()).device
    advantage_tensor = advantage_tensor.to(device)
    return_tensor = return_tensor.to(device)
    totals = {
        "loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
        "approx_kl": 0.0, "clip_fraction": 0.0, "grad_norm": 0.0,
        "grad_norm_clipped": 0.0, "explained_variance": 0.0,
    }
    batches = 0
    for chosen in _length_aware_batches(steps, config.minibatch_size, generator):
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
        log_ratio = new_logp - batch["old_logp"]
        ratio = log_ratio.exp()
        minibatch_kl = float(((ratio - 1.0) - log_ratio).mean().detach())
        # An epoch-level check fires too late when a large rollout produces many
        # minibatches.  Stop before applying the first update that is already
        # beyond the hard limit, while always allowing one minibatch so an epoch
        # cannot become a no-op.
        if config.hard_kl_stop and batches > 0 and minibatch_kl > config.hard_kl_stop:
            break
        policy_loss = ppo_clipped_loss(
            batch["old_logp"], new_logp, advantage_tensor[chosen], config.clip,
        )
        value_loss = torch.nn.functional.mse_loss(values, return_tensor[chosen])
        probs = log_probs.exp()
        entropy = -(probs * log_probs.masked_fill(~batch["mask"], 0.0)).sum(-1).mean()
        loss = policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy
        optimizer.zero_grad()
        loss.backward()
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
        clipped_sq = sum(
            float(parameter.grad.detach().norm(2)) ** 2
            for parameter in model.parameters() if parameter.grad is not None
        )
        optimizer.step()
        totals["loss"] += float(loss.detach())
        totals["policy_loss"] += float(policy_loss.detach())
        totals["value_loss"] += float(value_loss.detach())
        totals["entropy"] += float(entropy.detach())
        totals["approx_kl"] += minibatch_kl
        totals["clip_fraction"] += float(((ratio - 1.0).abs() > config.clip).float().mean().detach())
        totals["grad_norm"] += float(grad_norm.detach())
        totals["grad_norm_clipped"] += clipped_sq ** 0.5
        target_returns = return_tensor[chosen]
        variance = torch.var(target_returns, unbiased=False)
        explained = (
            1.0 - torch.var(target_returns - values.detach(), unbiased=False) / variance
            if float(variance) > 1e-8 else torch.tensor(0.0, device=device)
        )
        totals["explained_variance"] += float(explained.detach())
        batches += 1
    result = {k: v / max(batches, 1) for k, v in totals.items()}
    result["minibatches"] = float(batches)
    return result
