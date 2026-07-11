"""M1 real-engine reset determinism and episode-pollution check."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rl" / "src"))
from sts2rl.engine import EngineClient, RunConfig


ROOT = Path(__file__).resolve().parent.parent / "external" / "sts2-cli"
DLL = ROOT / "src" / "Sts2Headless" / "bin" / "Debug" / "net9.0" / "Sts2Headless.dll"
DOTNET = os.environ.get("DOTNET_HOST_PATH") or shutil.which("dotnet")
if not DOTNET:
    raise SystemExit("dotnet not found (set DOTNET_HOST_PATH)")
CMD = [DOTNET, str(DLL)]


def client() -> EngineClient:
    return EngineClient(
        CMD,
        cwd=ROOT,
        timeout=10,
        env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]},
    )


if __name__ == "__main__":
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR required")

    anchor = RunConfig("Ironclad", "m1-isolation-anchor")
    other = RunConfig("Silent", "m1-isolation-interleaved")
    with client() as first:
        hash_a = first.reset(anchor).state_hash
        hash_repeat = first.reset(anchor).state_hash
        first.reset(other)
        hash_after_other = first.reset(anchor).state_hash
    with client() as second:
        hash_other_worker = second.reset(anchor).state_hash

    result = {
        "anchor": hash_a,
        "same_worker_repeat": hash_repeat,
        "same_worker_after_other_episode": hash_after_other,
        "other_worker": hash_other_worker,
        "passed": len({hash_a, hash_repeat, hash_after_other, hash_other_worker}) == 1,
    }
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit("seed isolation / episode pollution check failed")
