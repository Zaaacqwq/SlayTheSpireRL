"""M1 A0 evaluator with live progress + incremental on-disk logging.

Same semantics as tools/m1_evaluate_1000.py (fresh persistent process per
episode, random legal policy, run to game_over), but:

  * appends every finished episode to rl/runs/<tag>.jsonl as it completes,
    so progress is inspectable while the run is in flight (the old script
    buffered all 1000 results in memory and only wrote at the very end,
    making "which episode are we on?" unanswerable);
  * prints a live one-line progress readout (done/total, ok/err, rate, ETA);
  * writes the same final rl/runs/<tag>.json summary at the end.

Usage:
  python tools/m1_evaluate_progress.py                 # full 1000 (200 x 5 chars)
  python tools/m1_evaluate_progress.py --per-char 20   # quick 100-episode smoke
  python tools/m1_evaluate_progress.py --workers 8 --tag m1_a0_smoke
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, json, os, random, sys, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'rl', 'src'))
from sts2rl.engine import EngineClient, RunConfig

ROOT = os.path.join(os.path.dirname(__file__), '..', 'external', 'sts2-cli')
CMD = ['dotnet', 'run', '--no-build', '--project', 'src/Sts2Headless/Sts2Headless.csproj']
CHARS = ['Ironclad', 'Silent', 'Defect', 'Necrobinder', 'Regent']


def run(job, timeout):
    c, i = job
    seed = f'm1-a0-{c}-{i}'
    steps = 0
    start = time.perf_counter()
    try:
        with EngineClient(CMD, cwd=ROOT, timeout=timeout, env={'STS2_GAME_DIR': os.environ['STS2_GAME_DIR']}) as e:
            s = e.reset(RunConfig(c, seed))
            rng = random.Random(f'{c}:{i}')
            while steps < 2000 and s.phase != 'game_over':
                s = e.step(rng.choice(s.candidates)).state
                steps += 1
            outcome = s.raw.get('victory') if s.phase == 'game_over' else None
            return {'character': c, 'index': i, 'seed': seed, 'outcome': outcome,
                    'steps': steps, 'error': None, 'seconds': round(time.perf_counter() - start, 2)}
    except Exception as x:
        return {'character': c, 'index': i, 'seed': seed, 'outcome': None,
                'steps': steps, 'error': type(x).__name__, 'seconds': round(time.perf_counter() - start, 2)}


def fmt_dur(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f'{h:d}:{m:02d}:{s:02d}' if h else f'{m:d}:{s:02d}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-char', type=int, default=200, help='episodes per character (default 200 -> 1000 total)')
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--timeout', type=float, default=10.0, help='per-decision engine timeout seconds')
    ap.add_argument('--tag', default='m1_a0_1000', help='output basename under rl/runs/')
    args = ap.parse_args()

    if not os.environ.get('STS2_GAME_DIR'):
        raise SystemExit('STS2_GAME_DIR required')

    jobs = [(c, i) for c in CHARS for i in range(args.per_char)]
    total = len(jobs)
    os.makedirs('rl/runs', exist_ok=True)
    jsonl_path = os.path.join('rl', 'runs', f'{args.tag}.jsonl')
    json_path = os.path.join('rl', 'runs', f'{args.tag}.json')
    # Truncate any prior progress log for this tag.
    open(jsonl_path, 'w').close()

    out = []
    lock = threading.Lock()
    start = time.perf_counter()
    ok = err = 0

    print(f'[start] {total} episodes, {args.workers} workers, timeout={args.timeout}s, tag={args.tag}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run, j, args.timeout) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            with lock:
                out.append(r)
                if r['error'] is None:
                    ok += 1
                else:
                    err += 1
                done = len(out)
                with open(jsonl_path, 'a') as jf:
                    jf.write(json.dumps(r) + '\n')
                elapsed = time.perf_counter() - start
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate if rate else 0
                flag = '' if r['error'] is None else f"  <-- {r['character']}-{r['index']} {r['error']}"
                print(f"\r[{done:4d}/{total}] ok={ok} err={err} | {rate:4.1f} ep/s | "
                      f"elapsed {fmt_dur(elapsed)} eta {fmt_dur(eta)}{flag}", end='', flush=True)
                if flag:
                    print('', flush=True)  # keep error lines in the scrollback

    print('', flush=True)
    with open(json_path, 'w') as jf:
        json.dump(out, jf, indent=2)

    print(f'\n=== {args.tag} done in {fmt_dur(time.perf_counter() - start)} ===', flush=True)
    for c in CHARS:
        rows = [x for x in out if x['character'] == c]
        wins = sum(x['outcome'] is True for x in rows)
        errs = sum(x['error'] is not None for x in rows)
        print(f'{c:12s} n={len(rows):3d}  wins={wins:3d}  errors={errs}', flush=True)
    print(f'TOTAL {len(out)}  errors={err}', flush=True)
    if err:
        print('\nErrors:', flush=True)
        for x in out:
            if x['error']:
                print(f"  {x['seed']:24s} {x['error']:16s} steps={x['steps']} {x['seconds']}s", flush=True)


if __name__ == '__main__':
    main()
