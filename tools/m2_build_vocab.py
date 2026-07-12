"""M2 P2: build the entity-id vocabulary from the engine's canonical catalogs.

``list_models`` enumerates ModelDb directly, so the vocabulary covers all
content — not just what sampled trajectories happened to visit. Keys use the
prefixed ModelId form that states serialize (``CARD.STRIKE_IRONCLAD``).
Choices/options stay out: they are localized text without stable ids and map
to ``UNK`` by design.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "rl" / "src"))
from sts2rl.engine import EngineClient, RunConfig
from sts2rl.entities import ENTITY_KINDS, EntityVocab

CLI_ROOT = REPO_ROOT / "external" / "sts2-cli"
DLL = CLI_ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
VOCAB_PATH = REPO_ROOT / "rl" / "schema" / "m2_vocab.json"

# entity kind -> (list_models kind, state id prefix)
CATALOGS: dict[str, tuple[str, str]] = {
    "card": ("card", "CARD."),
    "enemy": ("monster", "MONSTER."),
    "relic": ("relic", "RELIC."),
    "potion": ("potion", "POTION."),
    "power": ("power", "POWER."),
}


def main() -> int:
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
        entries: dict[str, dict[str, int]] = {kind: {} for kind in ENTITY_KINDS}
        next_index = 1
        for kind in ENTITY_KINDS:
            if kind not in CATALOGS:
                continue
            catalog, prefix = CATALOGS[kind]
            ids = sorted({str(row["id"]) for row in engine.list_models(catalog)})
            for model_id in ids:
                entries[kind][prefix + model_id] = next_index
                next_index += 1

    vocab = EntityVocab(entries)
    vocab.save(VOCAB_PATH)
    counts = {kind: len(v) for kind, v in entries.items() if v}
    print(json.dumps({"path": str(VOCAB_PATH), "size": vocab.size, "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
