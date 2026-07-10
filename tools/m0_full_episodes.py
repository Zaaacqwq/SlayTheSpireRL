from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import random
import time
from sts2rl.engine import EngineClient, RunConfig

ROOT = os.path.join(os.path.dirname(__file__), "..", "external", "sts2-cli")
COMMAND = ["dotnet", "run", "--no-build", "--project", "src/Sts2Headless/Sts2Headless.csproj"]
CHARACTERS = ["Ironclad", "Silent", "Defect", "Necrobinder", "Regent"]


def episode(character: str, index: int, max_steps: int = 1000) -> dict[str, object]:
    started = time.perf_counter()
    steps = 0
    seed = f"m0-full-{character}-{index}"
    try:
        with EngineClient(COMMAND, cwd=ROOT, timeout=10, env={"STS2_GAME_DIR": os.environ["STS2_GAME_DIR"]}) as client:
            state = client.reset(RunConfig(character, seed))
            rng = random.Random(f"{character}:{index}")
            while steps < max_steps and state.phase != "game_over":
                state = client.step(rng.choice(state.candidates)).state
                steps += 1
            outcome = state.raw.get("victory", state.raw.get("outcome", state.phase)) if state.phase == "game_over" else "step_limit"
        return {"character": character, "index": index, "seed": seed, "status": "ok", "outcome": outcome, "steps": steps, "seconds": round(time.perf_counter() - started, 2)}
    except Exception as exc:
        return {"character": character, "index": index, "seed": seed, "status": "error", "error": type(exc).__name__, "detail": str(exc)[:500], "steps": steps, "seconds": round(time.perf_counter() - started, 2)}


def main() -> None:
    if not os.environ.get("STS2_GAME_DIR"):
        raise SystemExit("STS2_GAME_DIR is required")
    jobs = [(character, index) for character in CHARACTERS for index in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(episode, character, index) for character, index in jobs]
        for future in as_completed(futures):
            print(json.dumps(future.result(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
