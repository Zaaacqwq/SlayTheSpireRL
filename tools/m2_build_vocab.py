"""M2 P2: build the entity-id vocabulary from the engine's canonical catalogs.

``list_models`` enumerates ModelDb directly, so the vocabulary covers all
content — not just what sampled trajectories happened to visit. Keys use the
prefixed ModelId form that states serialize (``CARD.STRIKE_IRONCLAD``).
Non-catalog decision identities are harvested from deterministic real-engine
sweeps. Any content missed by that bounded sweep remains fail-fast during the
visibility audit instead of silently sharing ``UNK``.
"""
from __future__ import annotations

import json
import os
import argparse
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.entities import (
    ENTITY_KINDS, EntityVocab, _VOCAB_KIND_ALIASES, entity_key,
    extend_vocab_entries,
)
from sts2rl.observation import normalize_state

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"
EVENT_LOCALIZATION_PATH = CLI_ROOT / "localization_eng" / "events.json"

# entity kind -> (list_models kind, state id prefix)
CATALOGS: dict[str, tuple[str, str]] = {
    "card": ("card", "CARD."),
    "enemy": ("monster", "MONSTER."),
    "relic": ("relic", "RELIC."),
    "potion": ("potion", "POTION."),
    "power": ("power", "POWER."),
    # events already come in full ModelId form (EVENT.X / ancients)
    "event": ("event", ""),
    "orb": ("orb", "ORB."),
    "enchantment": ("enchantment", "ENCHANTMENT."),
    "affliction": ("affliction", "AFFLICTION."),
}
OPTION_SWEEP_EPISODES = 40

# Stable keys observed by strict preflight before automatic failure-artifact
# persistence was added. Keep the reviewed provenance in source rather than
# editing the generated vocabulary JSON by hand.
REVIEWED_STABLE_KEYS: dict[str, set[str]] = {
    "option": {
        "SLIPPERY_BRIDGE.pages.INITIAL.options.OVERCOME",
        "SLIPPERY_BRIDGE.pages.INITIAL.options.HOLD_ON_0",
        "SLIPPERY_BRIDGE.pages.HOLD_ON_0.options.HOLD_ON_1",
        "SLIPPERY_BRIDGE.pages.HOLD_ON_1.options.HOLD_ON_2",
        "THE_FUTURE_OF_POTIONS.pages.INITIAL.options.POTION",
        "DigRestSiteOption",
        "LiftRestSiteOption",
    },
}


def localized_option_keys(localization: dict[str, object]) -> set[str]:
    """Return every canonical event option text key from localization data."""
    suffix = ".title"
    return {
        key[:-len(suffix)] for key in localization
        if ".options." in key and key.endswith(suffix)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-run", type=Path, action="append", default=[],
        help="also harvest stable dynamic ids from a strict smoke run's Parquet episodes",
    )
    args = parser.parse_args()
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")
    if not DLL.exists():
        raise SystemExit(f"build first; missing {DLL}")
    dotnet = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet")
    if not dotnet:
        raise SystemExit("dotnet not found")

    with EngineClient([dotnet, str(DLL)], cwd=CLI_ROOT, timeout=30,
                      env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]}) as engine:
        # ModelDb.AllPowers reflects over mod types, which STS2 only finishes
        # loading during the first RunState creation — warm up with one reset.
        engine.reset(RunConfig("Ironclad", "m2-vocab-warmup"))

        discovered: dict[str, set[str]] = {
            kind: set() for kind in ENTITY_KINDS if kind not in _VOCAB_KIND_ALIASES
        }
        for kind, keys in REVIEWED_STABLE_KEYS.items():
            discovered[kind].update(keys)
        # Event text keys are protocol identities and the checked-in English
        # localization is their complete catalog. This covers deep/looping
        # state machines (notably every Slippery Bridge HOLD_ON page) that a
        # finite random sweep cannot guarantee it will visit.
        event_localization = json.loads(EVENT_LOCALIZATION_PATH.read_text(encoding="utf-8"))
        discovered["option"].update(localized_option_keys(event_localization))

        def discover(raw: dict) -> None:
            for entity in normalize_state(raw).entities:
                kind = _VOCAB_KIND_ALIASES.get(
                    str(entity.get("entity_type")), str(entity.get("entity_type")),
                )
                key = entity_key(entity)
                if kind in discovered and key != "UNK":
                    discovered[kind].add(key)

        # Map choices carry only a room `type`; collect the closed set of room
        # types from real generated maps so routing semantics are embeddable.
        room_types: set[str] = set()
        for index in range(5):
            state = engine.reset(RunConfig("Ironclad", f"m2-vocab-map-{index}"))
            discover(state.raw)
            full_map = engine._request({"cmd": "get_map"})
            stack = [full_map]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    value = node.get("type")
                    if isinstance(value, str) and value not in {"map", "full_map"}:
                        room_types.add(value)
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)

        # Event/rest options have no ModelDb catalog; their stable keys
        # (text_key / option type name) are harvested from a bounded random
        # sweep. Rare unseen options map to UNK at the encoder boundary, then
        # the strict training audit aborts before PPO so they can be reviewed.
        import random
        rng = random.Random(0)
        for index in range(OPTION_SWEEP_EPISODES):
            state = engine.reset(RunConfig("Ironclad", f"m2-vocab-sweep-{index}"))
            for _ in range(200):
                if state.phase == "game_over":
                    break
                discover(state.raw)
                state = engine.step(rng.choice(state.candidates)).state

        catalog_ids: dict[str, set[str]] = {}
        for kind, (catalog, prefix) in CATALOGS.items():
            catalog_rows = engine.list_models(catalog)
            catalog_ids[kind] = {prefix + str(row["id"]) for row in catalog_rows}
            discovered[kind].update(catalog_ids[kind])
            if kind == "card":
                for row in catalog_rows:
                    discovered["card_stat"].update(
                        f"CARD_STAT.{name}" for name in row.get("dynamic_vars", [])
                    )

        # Boss ids are encounter ids (THE_KIN_BOSS, etc.), not monster ids.
        discovered["boss"].update(
            str(row["id"]) for row in engine.list_models("encounter")
            if row.get("category") == "boss"
        )
        discovered["choice"].update(room_types)
        discovered["choice"].add("BUNDLE")
        discovered["map_node"].update(f"ROOM.{room_type}" for room_type in room_types)
        discovered["map_edge"].add("MAP_EDGE")
        discovered["card_target"].add("CARD_TARGET.DAMAGE")
        # A stable RandomCardId may reference any catalogued card even if the
        # bounded event sweep does not happen to roll it.
        discovered["event_var"].update(
            f"EVENT_VAR.RandomCardId.{card_id}"
            for card_id in catalog_ids["card"]
        )

        # Event option keys and card DynamicVar names do not have canonical
        # ModelDb catalogs. Strict smoke artifacts extend the deterministic base
        # sweep; future unseen keys still abort before PPO and can be reviewed.
        if args.artifact_run:
            import pyarrow.parquet as pq

            def without_nulls(value):
                if isinstance(value, dict):
                    return {key: without_nulls(item) for key, item in value.items()
                            if item is not None}
                if isinstance(value, list):
                    return [without_nulls(item) for item in value]
                return value

            for run_dir in args.artifact_run:
                for artifact in (run_dir / "episodes").glob("*/*.parquet"):
                    for row in pq.read_table(artifact, columns=["state"]).to_pylist():
                        discover(without_nulls(row["state"]))

        # The single id embedding is shared by every kind. Preserve every
        # existing index across vocabulary growth; sorting a regenerated option
        # list would otherwise shift power/event/etc. ids and silently corrupt a
        # resumed checkpoint. A clean build remains deterministic from empty.
        existing = EntityVocab.load(VOCAB_PATH).entries if VOCAB_PATH.exists() else {}
        entries = extend_vocab_entries(existing, discovered)

    vocab = EntityVocab(entries)
    vocab.save(VOCAB_PATH)
    counts = {kind: len(v) for kind, v in entries.items() if v}
    print(json.dumps({"path": str(VOCAB_PATH), "size": vocab.size, "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
