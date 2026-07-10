from __future__ import annotations
import json, os, random, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rl', 'src'))
from sts2rl.engine import EngineClient, RunConfig

ROOT = os.path.join(os.path.dirname(__file__), '..', 'external', 'sts2-cli')
CMD = ['dotnet', 'run', '--no-build', '--project', 'src/Sts2Headless/Sts2Headless.csproj']

FAILED = [
    ('Defect', 140),
    ('Necrobinder', 32),
    ('Regent', 29),
    ('Regent', 76),
    ('Regent', 74),
]

def run(c, i):
    seed = f'm1-a0-{c}-{i}'
    steps = 0
    start = time.perf_counter()
    try:
        with EngineClient(CMD, cwd=ROOT, timeout=10, env={'STS2_GAME_DIR': os.environ['STS2_GAME_DIR']}) as e:
            s = e.reset(RunConfig(c, seed))
            rng = random.Random(f'{c}:{i}')
            while steps < 2000 and s.phase != 'game_over':
                s = e.step(rng.choice(s.candidates)).state
                steps += 1
            return {'character': c, 'index': i, 'seed': seed, 'outcome': s.raw.get('victory') if s.phase == 'game_over' else None, 'steps': steps, 'error': None, 'seconds': round(time.perf_counter() - start, 2)}
    except Exception as x:
        return {'character': c, 'index': i, 'seed': seed, 'outcome': None, 'steps': steps, 'error': f'{type(x).__name__}: {x}', 'seconds': round(time.perf_counter() - start, 2)}

if __name__ == '__main__':
    if not os.environ.get('STS2_GAME_DIR'):
        raise SystemExit('STS2_GAME_DIR required')
    results = []
    for c, i in FAILED:
        r = run(c, i)
        print(json.dumps(r))
        results.append(r)
    os.makedirs('rl/runs', exist_ok=True)
    with open('rl/runs/m1_regress_failed_seeds.json', 'w') as f:
        json.dump(results, f, indent=2)
