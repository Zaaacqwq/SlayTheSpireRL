# Slay the Spire 2 — Reinforcement Learning (RL v2)

Training reinforcement-learning agents to play **Slay the Spire 2** on the *real*
game engine — no reimplemented rules — and a bridge for driving the actual
graphical game through the Model Context Protocol (MCP).

The project has two halves that meet at the finish line:

- **Train** — a custom recurrent, action-masked PPO agent learns on a headless
  build of the real engine (`external/sts2-cli`), stage by stage up a curriculum
  (combat → Act 1 → multi-act → full run). Capability is measured only by
  win rate on held-out seeds.
- **Play** — a C# game mod (`mod/`) plus an MCP server (`mcp/`) expose the live
  graphical game to an agent, so a trained policy (or a person, or Claude) can
  play through the real UI. This is the deployment target (milestone M6).

The authoritative roadmap, stage status, and acceptance gates live in
[`plan/`](plan/) (`rl_v2_roadmap.md`). Status: **M0 & M1 complete; M2 (Ironclad)
in progress**; M3–M6 not started.

---

## Repository layout

| Path | What it is |
|------|------------|
| `rl/` | The `sts2rl` training library: engine client, observation/feature encoding, transformer + GRU pointer policy, curriculum, PPO/GAE, checkpointing, telemetry. Plus `rl/schema` (frozen vocab/loadouts), `rl/seeds` (train/dev/test seed split), and `rl/tests`. |
| `tools/` | Command-line entry points: training, monitoring, evaluation, data prep. Main trainer `m2_train.py`; supervised by `train_watchdog.py`; started/paused via `train_control.py`; live dashboard `rl_dashboard.py`. |
| `dashboard/` | Vite + React + TypeScript training dashboard (source). Built to `dashboard/dist` (gitignored) and served by `tools/rl_dashboard.py`. |
| `external/sts2-cli` | Git submodule: the headless engine (pinned commit). Built to a `Sts2Headless.dll` the trainer talks to over a per-worker subprocess protocol. |
| `mcp/` | MCP server exposing the live graphical game as tools (`get_game_state`, `combat_play_card`, `map_choose_node`, …). |
| `mod/` | C# mod (`STS2_MCP`) loaded into the graphical game; the in-game half of the MCP bridge. |
| `plan/` | Authoritative RL roadmap, current-stage tracker, and append-only timeline (Chinese). CI enforces they stay in sync — see `tools/check_rl_plan_sync.py`. |
| `docs/` | Contributor and protocol docs (`CONTRIBUTING.md`, `HOW_TO_CONNECT.md`, `RL_V2_CLI_STATE_MACHINE.md`, …). |
| `scripts/` | Environment setup (`setup_sim.ps1`). |
| `tests/` | Repo-level tests for the tooling (`m2_train`, `rl_dashboard`, `train_watchdog`, vocab build). Library unit tests live under `rl/tests/`. |
| `vendor/` | Vendored research-only combat simulator (gitignored; recreate with `scripts/setup_sim.ps1`). Imported by `rl/src/sts2rl/sim`. |

Generated / machine-local (all gitignored): `rl/runs/` (checkpoints, history,
episodes), `rl/.venv/`, `dashboard/dist`, `dashboard/node_modules`, build output
under `mod/`, `__pycache__`, `.pytest_cache`.

---

## Quick start (training)

Prerequisites: **Python ≥ 3.10**, a **.NET SDK** (for the engine), a local install
of **Slay the Spire 2**, and its data directory.

```bash
# 1. Clone with the engine submodule
git clone --recurse-submodules <repo>
cd SlayTheSpireRL

# 2. Build the headless engine (see external/sts2-cli/README)
#    -> produces src/Sts2Headless/bin/Debug/net9.0/Sts2Headless.dll

# 3. Python env for the trainer
python -m venv rl/.venv
rl/.venv/Scripts/pip install -e "rl[train,dev]"   # torch, pyarrow, pytest

# 4. Point the engine at the game data
export STS2_GAME_DIR=".../Slay the Spire 2/data_sts2_windows_x86_64"
```

Run training under the watchdog (auto-restarts on crash, resumes the newest intact
checkpoint), then open the dashboard:

```bash
# start / pause / status for a run in rl/runs/<name>/ (reads its config.json)
python tools/train_control.py start  --run-name <name>
python tools/train_control.py status --run-name <name>
python tools/train_control.py pause  --run-name <name>

# live dashboard at http://127.0.0.1:8000
python tools/rl_dashboard.py
```

A run directory (`rl/runs/<name>/`) holds `config.json` (its full CLI recipe),
`history.jsonl` (per-iteration metrics), rolling `resume.pt` + milestone
`ckpt_NNNNN.pt`, and recorded `episodes/`. The watchdog validates a checkpoint
before resuming and skips a corrupt one, so an unclean shutdown cannot wedge a run.

## Quick start (play bridge)

The `mod/` (built with `mod/build.ps1` / `tools/deploy_mod.ps1`) loads into the
graphical game and speaks to the `mcp/` server, which presents the game as MCP
tools. See [`docs/HOW_TO_CONNECT.md`](docs/HOW_TO_CONNECT.md) and
[`mcp/README.md`](mcp/README.md).

---

## How training is set up

- **Backend** — the real engine only. `EngineClient.reset(RunConfig) →
  DecisionState`, `step(ActionCandidate) → StepResult`, one persistent subprocess
  per worker, restarted on timeout. The `external/sts2-cli` submodule is pinned to
  a specific commit.
- **Policy** — mutable entity encoding + phase embedding → GRU history →
  transformer → dynamic-candidate pointer head + value head. Unknown state fields
  fall into `UNK` with a warning that production training escalates to fail-fast.
- **Algorithm** — custom recurrent masked PPO (γ 0.999, GAE 0.95, clip 0.2,
  AdamW), hidden states stored detached and replayed (no BPTT).
- **Reward** — terminal +1 win / −1 loss, plus policy-invariant potential-based
  floor-progress shaping for run stages; a per-act-clear bonus keeps multi-act
  stages from collapsing. Optional (experiment) potential-based HP shaping and
  small rest/shop nudges — see the branch layout below.
- **Seeds** — SHA-256 split into train / development / test; the frozen test set
  never influences selection.

## Branch layout

Current lines of work (feature branches; `main` is the stable base):

- `feat/act2-training` — the clean bug-fixed baseline: sim backend as a
  dependency, per-act boss-funnel telemetry, and `train_control` start/pause.
- `feat/hp-shaping` — layered on the baseline: **potential-based HP shaping** with
  a concave/saturating potential, plus small `--smith-bonus` / `--remove-bonus`
  nudges. An isolated reward experiment (all flags default off), so it can be
  A/B'd against the baseline and dropped without touching it.

---

## Tools index

Training & ops: `m2_train.py` (trainer), `train_watchdog.py` (supervisor),
`train_control.py` (start/pause/status, shared by CLI and dashboard),
`rl_dashboard.py` (live web dashboard), `render_eval_dashboard.py`.

Evaluation & analysis: `m2_final_eval.py`, `act1_audit.py`,
`m2_probe_deckbuild.py`.

Data prep: `m2_build_vocab.py`, `m2_harvest_loadouts.py`,
`m2_record_episodes.py`, `p0_baseline_hash.py`.

Play / deploy: `live_play.py`, `deploy_mod.ps1`.

CI: `check_rl_plan_sync.py` enforces the `plan/` synchronization rules.

Earlier-milestone scripts (`m0_*`, `m1_*`, `m2_baseline.py`, `*_queue.sh`) are kept
for reference from the M0/M1 validation work and are no longer part of the M2 loop.

---

## License

See [`LICENSE`](LICENSE). The engine (`external/sts2-cli`) is a pinned fork based on
its own upstream MIT license; game assets and the `sts2.dll` are **not** included
or redistributed — you supply your own copy of the game.
