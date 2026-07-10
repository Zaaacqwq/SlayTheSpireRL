from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
from typing import Any, Mapping


SUPPORTED_PROTOCOL_VERSION = "0.2.0"
DECISIONS = frozenset({
    "map_select", "combat_play", "card_reward", "rest_site", "event_choice",
    "shop", "game_over", "bundle_select", "card_select",
})


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionCandidate:
    action: str
    args: Mapping[str, Any]

    def command(self) -> dict[str, Any]:
        value: dict[str, Any] = {"cmd": "action", "action": self.action}
        if self.args:
            value["args"] = dict(self.args)
        return value


@dataclass(frozen=True)
class DecisionState:
    raw: Mapping[str, Any]
    candidates: tuple[ActionCandidate, ...]

    @property
    def phase(self) -> str:
        return str(self.raw.get("decision", self.raw.get("type", "unknown")))

    @property
    def state_hash(self) -> str:
        return canonical_state_hash(self.raw)


@dataclass(frozen=True)
class StepResult:
    state: DecisionState
    terminated: bool
    error: str | None = None


def canonical_state_hash(state: Mapping[str, Any]) -> str:
    """Stable replay hash, excluding known non-semantic/logging fields."""
    ignored = {"timestamp", "elapsed_ms", "log_path"}

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in sorted(value.items()) if k not in ignored}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    payload = json.dumps(clean(dict(state)), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_state(raw: Mapping[str, Any]) -> DecisionState:
    if raw.get("type") == "error":
        raise ProtocolError(str(raw.get("message", "engine error")))
    phase = str(raw.get("decision", raw.get("type", "")))
    if phase not in DECISIONS:
        raise ProtocolError(f"unsupported decision point: {phase!r}")
    return DecisionState(dict(raw), legal_actions(raw))


def legal_actions(state: Mapping[str, Any]) -> tuple[ActionCandidate, ...]:
    """Translate the upstream 0.2.0 state into explicit legal candidates.

    Upstream does not expose one canonical legal-actions array, so this adapter is
    intentionally strict. Unknown decision shapes fail instead of being dropped.
    """
    phase = state.get("decision", state.get("type"))
    out: list[ActionCandidate] = []
    if phase == "game_over":
        return ()
    if phase == "combat_play":
        enemies = [e for e in state.get("enemies", []) if e.get("hp", 1) > 0]
        for card in state.get("hand", []):
            if not card.get("can_play", False):
                continue
            base = {"card_index": card["index"]}
            if card.get("target_type") == "AnyEnemy":
                out.extend(ActionCandidate("play_card", {**base, "target_index": e["index"]}) for e in enemies)
            else:
                out.append(ActionCandidate("play_card", base))
        for potion in state.get("potions", []):
            if potion.get("can_use", False):
                out.append(ActionCandidate("use_potion", {"potion_index": potion["index"]}))
        out.append(ActionCandidate("end_turn", {}))
    elif phase == "map_select":
        nodes = state.get("available_nodes", state.get("choices", state.get("options", [])))
        for node in nodes:
            args = {k: node[k] for k in ("col", "row") if k in node}
            if len(args) != 2:
                raise ProtocolError("map node lacks col/row")
            out.append(ActionCandidate("select_map_node", args))
    elif phase == "card_reward":
        for card in state.get("cards", state.get("options", [])):
            out.append(ActionCandidate("select_card_reward", {"card_index": card["index"]}))
        out.append(ActionCandidate("skip_card_reward", {}))
    elif phase in {"event_choice", "rest_site"}:
        for option in state.get("options", []):
            if not option.get("is_locked", False) and option.get("is_enabled", True):
                out.append(ActionCandidate("choose_option", {"option_index": option["index"]}))
    elif phase == "bundle_select":
        for bundle in state.get("bundles", state.get("options", [])):
            out.append(ActionCandidate("select_bundle", {"bundle_index": bundle["index"]}))
    elif phase == "card_select":
        cards = state.get("cards", state.get("options", state.get("choices", [])))
        min_select = int(state.get("min_select", 0))
        max_select = int(state.get("max_select", 1))
        if min_select == 0:
            out.append(ActionCandidate("skip_select", {}))
        # The upstream protocol accepts comma-separated indices for multi-select.
        # Bound combinations to the declared range; unknown larger ranges fail closed.
        if cards and 0 < min_select <= max_select <= 4:
            indices = [str(card["index"]) for card in cards]
            for size in range(min_select, max_select + 1):
                out.extend(ActionCandidate("select_cards", {"indices": ",".join(combo)}) for combo in itertools.combinations(indices, size))
    elif phase == "shop":
        for key, action, arg in (("cards", "buy_card", "card_index"), ("relics", "buy_relic", "relic_index"), ("potions", "buy_potion", "potion_index")):
            for item in state.get(key, []):
                if item.get("affordable", item.get("can_buy", False)):
                    out.append(ActionCandidate(action, {arg: item["index"]}))
        if state.get("can_remove_card", False):
            out.append(ActionCandidate("remove_card", {}))
        out.append(ActionCandidate("leave_room", {}))
    if not out:
        raise ProtocolError(f"no legal actions derived for {phase!r}")
    return tuple(out)
