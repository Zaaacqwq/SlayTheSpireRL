"""Protocol-0.2.0 full-run adapter for the vendored zhiyue ``RunManager``.

``SimCombatBackend`` serves single combats; this backend serves whole runs (map
-> combat -> reward -> shop/rest/event -> boss -> next act), the surface the real
``EngineClient`` exposes via ``reset(RunConfig)`` / ``step`` that the combat-only
simulator lacks. It wraps ``sts2_env.run.run_manager.RunManager`` and reuses
``SimCombatBackend`` for the combat-phase ``raw`` projection and stable ids.

Each decision builds our ``raw`` state and candidates directly, plus a
candidate -> RunManager-action map so ``step`` can translate back. No-decision
transitions (treasure pickups) are auto-advanced internally.
"""

from __future__ import annotations

from typing import Any, Mapping

from sts2rl.engine import CombatConfig, RunConfig
from sts2rl.protocol import ActionCandidate, DecisionState, StepResult, parse_state

from .backend import SimCombatBackend


def _cand_key(action: str, args: Mapping[str, Any]) -> tuple:
    def hashable(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(hashable(v) for v in value)
        return value
    return (action, tuple(sorted((k, hashable(v)) for k, v in (args or {}).items())))


class ZhiyueRunBackend:
    """EngineClient-compatible, whole-run in-process backend."""

    def __init__(self) -> None:
        # SimCombatBackend already puts the vendored sts2_env on sys.path, imports
        # potions/powers, loads the vocab, and exposes the combat raw projection
        # and stable-id helpers we reuse for the COMBAT phase.
        self._combat = SimCombatBackend()
        from sts2_env.core.enums import PotionTargetType, TargetType  # noqa: E402
        from sts2_env.run.run_manager import RunManager  # noqa: E402

        self._RunManager = RunManager
        self._PotionTargetType = PotionTargetType
        self._TargetType = TargetType
        self._mgr: Any | None = None
        self._cand_map: dict[tuple, dict] = {}
        self._act_floor: int = 0
        # "combat" delegates to the inner SimCombatBackend (combat stages);
        # "run" drives the RunManager (act1/full-run stages). One backend serves
        # both so zhiyue-mode training can advance from combat rungs into runs.
        self._mode: str | None = None
        self._boss_signatures = self._build_boss_signatures()

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "ZhiyueRunBackend":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._mgr = None
        self._cand_map = {}
        self._combat.close()

    def list_models(self, kind: str) -> list[dict[str, Any]]:
        return self._combat.list_models(kind)

    def reset_combat(self, config: CombatConfig) -> DecisionState:
        self._mode = "combat"
        self._mgr = None
        return self._combat.reset_combat(config)

    # -- run driving ---------------------------------------------------------

    def reset(self, config: RunConfig) -> DecisionState:
        self._mode = "run"
        seed_int = self._combat._deterministic_hash_code(config.seed)  # noqa: SLF001
        character = SimCombatBackend._character_id(config.character)
        self._mgr = self._RunManager(
            seed=seed_int, character_id=character, ascension_level=config.ascension,
        )
        self._act_floor = 0
        self._advance_to_decision()
        return self._decision_state()

    def step(self, action: ActionCandidate) -> StepResult:
        if self._mode == "combat":
            return self._combat.step(action)
        if self._mgr is None:
            raise RuntimeError("reset must be called before step")
        run_action = self._cand_map.get(_cand_key(action.action, action.args))
        if run_action is None:
            raise RuntimeError(f"run backend has no mapping for candidate: {action}")
        if run_action.get("action") == "_choice_sequence":
            self._apply_choice_sequence(run_action["indices"])
        else:
            if run_action.get("action") == "move":
                _, row = run_action["coord"]
                self._act_floor = max(self._act_floor, int(row) + 1)
            self._mgr.take_action(run_action)
        self._advance_to_decision()
        state = self._decision_state()
        return StepResult(state=state, terminated=state.phase == "game_over")

    def _apply_choice_sequence(self, indices: list[int]) -> None:
        """Resolve a card-selection pending choice: toggle each option, then
        confirm if the choice is multi-select and still open."""
        mgr = self._mgr
        assert mgr is not None
        for idx in indices:
            mgr.take_action({"action": "choose", "index": int(idx)})
            if mgr.is_over:
                return
        if any(a.get("action") == "confirm_choice" for a in mgr.get_available_actions()):
            mgr.take_action({"action": "confirm_choice"})

    def _advance_to_decision(self) -> None:
        """Auto-resolve phases with no genuine choice (treasure pickups)."""
        mgr = self._mgr
        guard = 0
        while mgr is not None and not mgr.is_over:
            guard += 1
            if guard > 64:
                raise RuntimeError("run backend stuck auto-advancing")
            phase = mgr.phase
            if phase == self._RunManager.PHASE_TREASURE:
                mgr.take_action({"action": "collect"})
                continue
            actions = mgr.get_available_actions()
            # A lone forced no-op (only "collect"/"leave") carries no decision.
            if len(actions) == 1 and actions[0].get("action") in {"collect"}:
                mgr.take_action(actions[0])
                continue
            break

    # -- state projection ----------------------------------------------------

    def _decision_state(self) -> DecisionState:
        mgr = self._mgr
        assert mgr is not None
        if mgr.is_over:
            self._cand_map = {}
            raw = {
                "type": "decision", "decision": "game_over",
                "victory": bool(mgr.player_won),
                "outcome": "victory" if mgr.player_won else "defeat",
                "player": self._run_player_row(), "enemies": [], "hand": [],
                "context": self._context(),
            }
            return DecisionState(raw, ())

        phase = mgr.phase
        RM = self._RunManager
        if phase == RM.PHASE_COMBAT:
            return self._combat_state()
        if mgr.run_state.pending_choice is not None:
            return self._pending_choice_state(mgr.get_available_actions())
        if phase == RM.PHASE_MAP_CHOICE:
            return self._map_state(mgr.get_available_actions())
        if phase == RM.PHASE_CARD_REWARD:
            return self._card_reward_state(mgr.get_available_actions())
        if phase == RM.PHASE_BOSS_RELIC:
            return self._boss_relic_state(mgr.get_available_actions())
        if phase == RM.PHASE_SHOP:
            return self._shop_state(mgr.get_available_actions())
        if phase == RM.PHASE_REST_SITE:
            return self._rest_state(mgr.get_available_actions())
        if phase == RM.PHASE_EVENT:
            return self._event_state(mgr.get_available_actions())
        raise RuntimeError(f"unhandled run phase: {phase!r}")

    # -- shared raw pieces ---------------------------------------------------

    def _context(self) -> dict[str, Any]:
        mgr = self._mgr
        assert mgr is not None
        return {
            "act": int(mgr.run_state.current_act_index) + 1,
            "floor": int(self._act_floor),
        }

    def _run_player_row(self) -> dict[str, Any]:
        player = self._mgr.run_state.player  # type: ignore[union-attr]
        sid = self._combat._stable_id  # noqa: SLF001
        potions = []
        for index, potion in enumerate(player.potions):
            if potion is None:
                continue
            potions.append({
                "index": index,
                "id": sid("potion", SimCombatBackend._upper_snake(potion.potion_id)),
                "target_type": SimCombatBackend._pascal(potion.target_type.name),
            })
        deck = [
            {"id": sid("card", card.card_id.name), "index": i, "upgraded": bool(card.upgraded)}
            for i, card in enumerate(player.deck)
        ]
        return {
            "hp": int(player.current_hp), "max_hp": int(player.max_hp),
            "block": 0, "gold": int(player.gold),
            "deck_size": len(player.deck), "deck": deck,
            "relics": [{"id": sid("relic", str(rid))} for rid in player.relics],
            "potions": potions,
        }

    def _finish(self, raw: dict[str, Any], candidates: tuple[ActionCandidate, ...],
                cand_map: dict[tuple, dict]) -> DecisionState:
        raw.setdefault("context", self._context())
        raw.setdefault("player", self._run_player_row())
        self._cand_map = cand_map
        return DecisionState(raw, candidates)

    # -- COMBAT --------------------------------------------------------------

    def _combat_state(self) -> DecisionState:
        mgr = self._mgr
        assert mgr is not None
        # Reuse SimCombatBackend's combat raw projection by pointing it at the
        # live CombatState (also refreshes its _visible_enemy_indices mapping).
        self._combat.combat = mgr.get_combat_state()
        raw = self._combat._raw_state()  # noqa: SLF001
        raw.setdefault("context", {}).update(self._context())
        room_type = self._room_type()
        raw["context"]["room_type"] = room_type
        if room_type == "Boss":
            encounter = self._boss_encounter_id()
            if encounter is not None:
                raw["context"]["boss"] = {"id": encounter}
        state = parse_state(raw)

        actions = mgr.get_available_actions()
        player_id = next((a.get("player_id") for a in actions if "player_id" in a), None)
        visible = self._combat._visible_enemy_indices  # noqa: SLF001
        cand_map: dict[tuple, dict] = {}
        for cand in state.candidates:
            args = cand.args
            if cand.action == "end_turn":
                run = {"action": "end_turn"}
            elif cand.action == "play_card":
                run = {"action": "play_card", "player_id": player_id,
                       "hand_index": int(args["card_index"])}
                if "target_index" in args:
                    run["target_index"] = visible[int(args["target_index"])]
                else:
                    run["target_index"] = None
            elif cand.action == "use_potion":
                run = {"action": "use_potion", "player_id": player_id,
                       "slot_index": int(args["potion_index"])}
                if "target_index" in args:
                    run["target_index"] = visible[int(args["target_index"])]
            elif cand.action in ("select_cards", "skip_select"):
                run = self._combat_choice_action(cand)
            else:
                raise RuntimeError(f"unmapped combat candidate: {cand.action}")
            cand_map[_cand_key(cand.action, args)] = run
        self._cand_map = cand_map
        return state

    def _combat_choice_action(self, cand: ActionCandidate) -> dict:
        # combat pending choice: our card_select -> run choose/confirm. Encoded as
        # a marker; step resolves multi-toggle sequences via _apply_choice.
        if cand.action == "skip_select":
            return {"action": "confirm_choice"}
        indices = [int(v) for v in str(cand.args.get("indices", "")).split(",") if v != ""]
        return {"action": "_choice_sequence", "indices": indices}

    def _room_type(self) -> str:
        room = getattr(self._mgr, "current_room", None)
        rtype = getattr(room, "room_type", None) or getattr(self._mgr, "_current_room_type", None)
        name = getattr(rtype, "name", "")
        return {"BOSS": "Boss", "ELITE": "Elite"}.get(name, "Monster")

    def _build_boss_signatures(self) -> dict[tuple[str, ...], str]:
        """Identify RunManager boss rolls from their initial monster roster.

        ``RunManager`` does not retain the setup function it sampled, but boss
        replay needs the exact simulator encounter id.  Probe each registered
        boss once before a run starts and retain its deterministic roster.
        """
        signatures: dict[tuple[str, ...], str] = {}
        for row in self._combat.list_models("encounter"):
            if row.get("category") != "boss":
                continue
            encounter = str(row["id"])
            self._combat.reset_combat(CombatConfig(
                character="Ironclad", seed=f"boss-signature:{encounter}",
                encounter=encounter, hp=80, max_hp=80,
            ))
            signature = tuple(
                str(enemy.monster_id) for enemy in self._combat.combat.enemies
            )
            if signature:
                signatures[signature] = encounter
        self._combat.close()
        return signatures

    def _boss_encounter_id(self) -> str | None:
        combat = self._mgr.get_combat_state()  # type: ignore[union-attr]
        signature = tuple(str(enemy.monster_id) for enemy in combat.enemies)
        return self._boss_signatures.get(signature)

    # -- MAP -----------------------------------------------------------------

    def _map_state(self, actions: list[dict]) -> DecisionState:
        nodes, candidates, cand_map = [], [], {}
        for a in actions:
            if a.get("action") != "move":
                continue
            col, row = a["coord"]
            # Nodes go in ``choices`` (a known field) so obs normalization turns
            # each into a ``choice`` entity the select_map_node pointer binds to.
            nodes.append({"col": int(col), "row": int(row),
                          "type": a.get("point_type", "UNKNOWN"),
                          "id": f"ROOM.{a.get('point_type', 'UNKNOWN')}"})
            args = {"col": int(col), "row": int(row)}
            candidates.append(ActionCandidate("select_map_node", args))
            cand_map[_cand_key("select_map_node", args)] = a
        raw = {"type": "decision", "decision": "map_select", "choices": nodes}
        return self._finish(raw, tuple(candidates), cand_map)

    # -- CARD REWARD ---------------------------------------------------------

    def _card_reward_state(self, actions: list[dict]) -> DecisionState:
        sid = self._combat._stable_id  # noqa: SLF001
        cards, candidates, cand_map = [], [], {}
        can_skip = False
        pos = 0
        for a in actions:
            act = a.get("action")
            if act in ("skip", "skip_potion", "skip_relic"):
                can_skip = True
                cand = ActionCandidate("skip_card_reward", {})
                candidates.append(cand)
                cand_map[_cand_key("skip_card_reward", {})] = a
                continue
            if act == "pick_card":
                entry = {"id": sid("card", a["card_id"]), "index": pos,
                         "upgraded": bool(a.get("upgraded", False))}
            elif act == "pick_potion":
                entry = {"id": sid("potion", SimCombatBackend._upper_snake(a["potion_id"])),
                         "index": pos}
            elif act in ("pick_relic_reward",):
                entry = {"id": sid("relic", str(a["relic_id"])), "index": pos}
            elif act == "pick_card_bundle":
                entry = {"id": "BUNDLE", "index": pos}
            else:
                # reroll / sacrifice etc.: not exposed as candidates in Phase 1.
                continue
            cards.append(entry)
            args = {"card_index": pos}
            candidates.append(ActionCandidate("select_card_reward", args))
            cand_map[_cand_key("select_card_reward", args)] = a
            pos += 1
        raw = {"type": "decision", "decision": "card_reward", "cards": cards,
               "can_skip": can_skip}
        if not candidates:  # nothing pickable and not skippable: force-skip if any action
            if actions:
                cand = ActionCandidate("skip_card_reward", {})
                candidates.append(cand)
                cand_map[_cand_key("skip_card_reward", {})] = actions[0]
        return self._finish(raw, tuple(candidates), cand_map)

    def _boss_relic_state(self, actions: list[dict]) -> DecisionState:
        sid = self._combat._stable_id  # noqa: SLF001
        cards, candidates, cand_map = [], [], {}
        for pos, a in enumerate(actions):
            cards.append({"id": sid("relic", str(a.get("relic_id", "UNK"))), "index": pos})
            args = {"card_index": pos}
            candidates.append(ActionCandidate("select_card_reward", args))
            cand_map[_cand_key("select_card_reward", args)] = a
        raw = {"type": "decision", "decision": "card_reward", "cards": cards,
               "can_skip": False, "from_event": True}
        return self._finish(raw, tuple(candidates), cand_map)

    # -- SHOP ----------------------------------------------------------------

    def _shop_state(self, actions: list[dict]) -> DecisionState:
        sid = self._combat._stable_id  # noqa: SLF001
        cards, relics, potions = [], [], []
        candidates, cand_map = [], {}
        can_remove = False
        counters = {"buy_card": 0, "buy_relic": 0, "buy_potion": 0}
        for a in actions:
            act = a.get("action")
            if act == "buy_card":
                i = counters["buy_card"]; counters["buy_card"] += 1
                cards.append({"id": sid("card", a["card_id"]), "index": i,
                              "affordable": True, "price": a.get("price")})
                args = {"card_index": i}
                candidates.append(ActionCandidate("buy_card", args))
                cand_map[_cand_key("buy_card", args)] = a
            elif act == "buy_relic":
                i = counters["buy_relic"]; counters["buy_relic"] += 1
                relics.append({"id": sid("relic", str(a["relic_id"])), "index": i,
                               "affordable": True, "price": a.get("price")})
                args = {"relic_index": i}
                candidates.append(ActionCandidate("buy_relic", args))
                cand_map[_cand_key("buy_relic", args)] = a
            elif act == "buy_potion":
                i = counters["buy_potion"]; counters["buy_potion"] += 1
                potions.append({"id": sid("potion", SimCombatBackend._upper_snake(a["potion_id"])),
                                "index": i, "affordable": True, "price": a.get("price")})
                args = {"potion_index": i}
                candidates.append(ActionCandidate("buy_potion", args))
                cand_map[_cand_key("buy_potion", args)] = a
            elif act == "remove_card":
                can_remove = True
                candidates.append(ActionCandidate("remove_card", {}))
                cand_map[_cand_key("remove_card", {})] = a
            elif act == "use_potion":
                pass  # foul-potion use in shop: skip exposing in Phase 1
            elif act == "leave_shop":
                candidates.append(ActionCandidate("leave_room", {}))
                cand_map[_cand_key("leave_room", {})] = a
        raw = {"type": "decision", "decision": "shop", "cards": cards, "relics": relics,
               "potions": potions, "can_remove_card": can_remove}
        return self._finish(raw, tuple(candidates), cand_map)

    # -- REST ----------------------------------------------------------------

    def _rest_state(self, actions: list[dict]) -> DecisionState:
        options, candidates, cand_map = [], [], {}
        for i, a in enumerate(actions):
            options.append({"index": i, "id": str(a.get("option_id", "REST")),
                            "name": str(a.get("option_id", "REST")),
                            "is_enabled": bool(a.get("enabled", True))})
            args = {"option_index": i}
            candidates.append(ActionCandidate("choose_option", args))
            cand_map[_cand_key("choose_option", args)] = a
        raw = {"type": "decision", "decision": "rest_site", "options": options}
        return self._finish(raw, tuple(candidates), cand_map)

    # -- EVENT ---------------------------------------------------------------

    def _event_state(self, actions: list[dict]) -> DecisionState:
        if actions and actions[0].get("action") in ("choose", "confirm_choice"):
            return self._pending_choice_state(actions)
        options, candidates, cand_map = [], [], {}
        for i, a in enumerate(actions):
            options.append({"index": i, "id": str(a.get("option_id", "opt")),
                            "text_key": str(a.get("option_id", "opt")),
                            "label": str(a.get("label", "")),
                            "is_enabled": bool(a.get("enabled", True))})
            args = {"option_index": i}
            candidates.append(ActionCandidate("choose_option", args))
            cand_map[_cand_key("choose_option", args)] = a
        raw = {"type": "decision", "decision": "event_choice", "options": options,
               "event_id": "EVENT"}
        return self._finish(raw, tuple(candidates), cand_map)

    # -- pending card choices (run/event) -----------------------------------

    def _pending_choice_state(self, actions: list[dict]) -> DecisionState:
        cards, candidates, cand_map = [], [], {}
        can_confirm = any(a.get("action") == "confirm_choice" for a in actions)
        choose_actions = [a for a in actions if a.get("action") == "choose"]
        sid = self._combat._stable_id  # noqa: SLF001
        for a in choose_actions:
            idx = int(a["index"])
            cards.append({"id": sid("card", str(a.get("card_id", "UNK"))), "index": idx})
        # Single-select is the common case; expose each option as a select_cards(one).
        for a in choose_actions:
            idx = int(a["index"])
            args = {"indices": str(idx)}
            candidates.append(ActionCandidate("select_cards", args))
            cand_map[_cand_key("select_cards", args)] = {
                "action": "_choice_sequence", "indices": [idx]}
        if can_confirm:
            candidates.append(ActionCandidate("skip_select", {}))
            cand_map[_cand_key("skip_select", {})] = {"action": "confirm_choice"}
        raw = {"type": "decision", "decision": "card_select", "cards": cards,
               "min_select": 0 if can_confirm else 1, "max_select": 1}
        return self._finish(raw, tuple(candidates), cand_map)
