"""P0 baseline: deterministic rollout state-hash sequences.

Captures, for a fixed set of seeds, the sequence of canonical state hashes produced
by a seeded-random rollout. Later phases (P2+) must reproduce these byte-for-byte to
prove the single-thread driver did not change engine semantics or determinism.

Usage: STS2_GAME_DIR=... python3 tools/p0_baseline_hash.py [out.json]
"""
from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'rl' / 'src'))
from sts2rl.engine import EngineClient, RunConfig

ROOT = REPO_ROOT / 'external' / 'sts2-cli'
DOTNET = os.environ.get('DOTNET_HOST_PATH') or shutil.which('dotnet')
if not DOTNET:
    raise SystemExit('dotnet not found (set DOTNET_HOST_PATH)')
CMD = [DOTNET, str(ROOT / 'src' / 'Sts2Headless' / 'bin' / 'Debug' / 'net9.0' / 'Sts2Headless.dll')]

# Seeds chosen to be fast + deterministic (avoid the known non-terminating seeds).
SEEDS = [('Ironclad', 1), ('Silent', 3), ('Defect', 7), ('Regent', 76), ('Necrobinder', 5)]
STEPS = 40


def _canon(c) -> str:
    return json.dumps({'action': c.action, 'args': dict(c.args)}, sort_keys=True)


def pick(candidates):
    # Deterministic argmin over a canonical key — consumes ZERO randomness. A
    # random.choice policy is NOT process-stable here: the engine emits candidate
    # lists whose length/order vary across processes (.NET per-process string-hash
    # randomization), and random.choice draws a variable number of RNG bits by
    # length, so stochastic rollouts diverge. argmin isolates pure engine
    # determinism and gives P2 a bit-identical target.
    return min(candidates, key=_canon)


def rollout(character: str, index: int) -> dict:
    seed = f'm1-a0-{character}-{index}'
    hashes: list[str] = []
    with EngineClient(CMD, cwd=ROOT, timeout=10,
                      env={'STS2_GAME_DIR': os.environ['STS2_GAME_DIR']}) as e:
        s = e.reset(RunConfig(character, seed))
        hashes.append(s.state_hash)
        for _ in range(STEPS):
            if s.phase == 'game_over':
                break
            s = e.step(pick(s.candidates)).state
            hashes.append(s.state_hash)
    return {'character': character, 'index': index, 'seed': seed,
            'steps': len(hashes) - 1, 'hashes': hashes}


if __name__ == '__main__':
    if not os.environ.get('STS2_GAME_DIR'):
        raise SystemExit('STS2_GAME_DIR required')
    out = sys.argv[1] if len(sys.argv) > 1 else 'rl/schema/p0_baseline_hash.json'
    results = [rollout(c, i) for c, i in SEEDS]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(results, f, indent=2)
    for r in results:
        print(f"{r['seed']}: {r['steps']} steps, final={r['hashes'][-1][:16]}")
    print(f"saved {out}")
