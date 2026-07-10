from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import random
import time
from sts2rl.engine import EngineClient, RunConfig


ROOT = os.path.join(os.path.dirname(__file__), "..", "external", "sts2-cli")
COMMAND = ["dotnet", "run", "--no-build", "--project", "src/Sts2Headless/Sts2Headless.csproj"]


def worker(index: int, steps: int = 50) -> tuple[int, int, str | None, float]:
    started = time.perf_counter()
    count = 0
    try:
        with EngineClient(COMMAND, cwd=ROOT, timeout=10, env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]}) as client:
            state = client.reset(RunConfig("Ironclad", f"m0-bench-{index}"))
            rng = random.Random(index)
            while count < steps and state.phase != "game_over":
                state = client.step(rng.choice(state.candidates)).state
                count += 1
        return count, count, None, time.perf_counter() - started
    except Exception as exc:
        return count, count, type(exc).__name__, time.perf_counter() - started


def run(workers: int) -> dict[str, object]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))
    elapsed = time.perf_counter() - started
    return {"workers": workers, "steps": sum(x[0] for x in results), "seconds": round(elapsed, 3), "steps_per_second": round(sum(x[0] for x in results) / elapsed, 2), "errors": [x[2] for x in results if x[2]]}


if __name__ == "__main__":
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR is required")
    for n in (1, 4, 8, 16):
        print(run(n), flush=True)
