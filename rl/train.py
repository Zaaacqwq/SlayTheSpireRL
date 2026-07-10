"""MaskablePPO training entry point for the Phase B run-continuity env
(sim/run.py + env.py, Stage 2.5 onward -- one episode is a multi-fight
mini-run, not a single isolated combat).

Smoke-test scope only (per plan): verifies the training loop runs end to
end and win rate trends upward over a modest step budget -- not tuned for
convergence. Run from the rl/ directory:

    uv run python train.py [--timesteps N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sb3_contrib import MaskablePPO
from stable_baselines3.common.callbacks import BaseCallback

from env import STS2CombatEnv

# Fixed seed for reproducibility, derived from the user-specified string
# "LFLFWUJ8KS" (our own simulator's RNG seed; unrelated to real STS2 save
# run seeds -- v1 has no map, so there's nothing else that string could
# apply to). See plan/plan.md 2026-07-09 entry.
SEED_STRING = "LFLFWUJ8KS"
SEED = int(hashlib.sha256(SEED_STRING.encode("utf-8")).hexdigest(), 16) % (2**32)

MODELS_DIR = Path(__file__).resolve().parent / "models"


class WinRateCallback(BaseCallback):
    """Logs rolling run-clear rate + mean episode reward over the last
    `window` completed runs. Unlike the old single-combat env, clearing a
    5-fight run under a fully random policy is genuinely hard (~23% in a
    fuzz test), so win rate here is a meaningful learning signal on its
    own, not just a saturating-to-100% side effect."""

    def __init__(self, window: int = 100, log_every: int = 2000, verbose: int = 0):
        super().__init__(verbose)
        self.window = window
        self.log_every = log_every
        self._recent_outcomes: list[str] = []
        self._recent_rewards: list[float] = []
        self._ep_reward_accum = 0.0
        self._last_log_step = 0
        # Full history of logged points, for post-training visualization
        # (rl/models/training_history.json) -- not used by training itself.
        self.history: list[dict] = []

    def _on_step(self) -> bool:
        rewards = self.locals.get("rewards", [])
        infos = self.locals.get("infos", [])
        dones = self.locals.get("dones", [])
        for reward, info, done in zip(rewards, infos, dones):
            self._ep_reward_accum += float(reward)
            if done and info.get("outcome") in ("run_won", "run_lost"):
                self._recent_outcomes.append(info["outcome"])
                self._recent_rewards.append(self._ep_reward_accum)
                self._ep_reward_accum = 0.0
                if len(self._recent_outcomes) > self.window:
                    self._recent_outcomes.pop(0)
                    self._recent_rewards.pop(0)

        if self.num_timesteps - self._last_log_step >= self.log_every and self._recent_outcomes:
            win_rate = sum(1 for o in self._recent_outcomes if o == "run_won") / len(
                self._recent_outcomes
            )
            mean_reward = sum(self._recent_rewards) / len(self._recent_rewards)
            print(
                f"[step {self.num_timesteps}] rolling over last {len(self._recent_outcomes)} "
                f"episodes: win rate {win_rate:.2%}, mean episode reward {mean_reward:.3f}"
            )
            self.history.append(
                {"step": self.num_timesteps, "win_rate": win_rate, "mean_reward": mean_reward}
            )
            self._last_log_step = self.num_timesteps
        return True


def evaluate(model: MaskablePPO, n_episodes: int = 100, seed: int = SEED) -> tuple[float, float]:
    """Returns (win_rate, mean_episode_reward)."""
    env = STS2CombatEnv(seed=seed + 1)  # different seed stream than training
    wins = 0
    total_reward = 0.0
    for ep in range(n_episodes):
        obs, info = env.reset()
        done = False
        ep_reward = 0.0
        while not done:
            mask = env.action_masks()
            action, _ = model.predict(obs, action_masks=mask, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            ep_reward += reward
            done = terminated or truncated
        total_reward += ep_reward
        if info.get("outcome") == "run_won":
            wins += 1
    return wins / n_episodes, total_reward / n_episodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=30_000)
    args = parser.parse_args()

    env = STS2CombatEnv(seed=SEED)

    print(f"seed string {SEED_STRING!r} -> derived int seed {SEED}")
    print("evaluating untrained (random-init) policy...")
    model = MaskablePPO("MlpPolicy", env, verbose=0, seed=SEED)
    win_rate_before, reward_before = evaluate(model, n_episodes=100)
    print(f"before training: win rate {win_rate_before:.2%}, mean episode reward {reward_before:.3f}")

    callback = WinRateCallback(window=100, log_every=2000)
    model.learn(total_timesteps=args.timesteps, callback=callback)

    win_rate_after, reward_after = evaluate(model, n_episodes=100)
    print(
        f"after training ({args.timesteps} steps): win rate {win_rate_after:.2%}, "
        f"mean episode reward {reward_after:.3f}"
    )

    MODELS_DIR.mkdir(exist_ok=True)
    model.save(str(MODELS_DIR / "ppo_v1_smoke_test"))
    print(f"saved model to {MODELS_DIR / 'ppo_v1_smoke_test.zip'}")

    history = [{"step": 0, "win_rate": win_rate_before, "mean_reward": reward_before}] + callback.history
    history.append({"step": args.timesteps, "win_rate": win_rate_after, "mean_reward": reward_after})
    (MODELS_DIR / "training_history.json").write_text(
        json.dumps({"seed_string": SEED_STRING, "total_timesteps": args.timesteps, "history": history}, indent=2),
        encoding="utf-8",
    )
    print(f"saved training history to {MODELS_DIR / 'training_history.json'}")


if __name__ == "__main__":
    main()
