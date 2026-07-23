# Approach A：接入 zhiyue headless 模拟器作为快速训练后端

> 交接文档，供新 session 实施。承接 `v8_bigmodel_instability.md`（v8 稳定但学不上去，平台根因之一是采样饥饿）。

## Context（为什么做）

v7 的 act1 通关率 ~72% 是 **vanilla PPO 在真游戏慢采样下的天花板**。诊断显示平台的一个真实根因是**采样饥饿**：我们用真 STS2 CLI 采样，实测仅 ~70 步/秒（12 workers），而 on-policy PPO 要啃 act1 的长横跨/稀疏信号需要海量数据。

`zhiyue/sts2-rl-agent`（GitHub，本地 clone 在 `C:\Users\ZacPC\AppData\Local\Temp\sts2-rl-agent-codex`）有一个**纯 Python headless 模拟器**——本机实测**单核 146 战斗/秒、3658 步/秒（≈ 我们 12 核的 ~50x；多核可达 ~1500 战斗/秒）**，而且它有**整轮 `run_env`（地图/商店/事件/奖励）**，正是我们 sim 后端缺、且平台墙所在的部分。

**目标**：把它的快模拟器接成我们训练管线的一个**环境后端**，让每个实验从「几十小时」→「几十分钟」，从而能跑之前跑不动的量级式实验（大模型稳定训练 / 大数据 / hierarchical）去突破 72%。**保留我们所有领先的东西**（entity 模型 / 指针动作空间 / curriculum / reward 设计）——我们整轮 72% vs 它 0–45%（它 README 顶上的 92% 是单场战斗胜率，不是整轮通关）。

## 分支

- **base：`main @ 22a7e35`（干净 v7 线，用户已确认）**。不带今晚的 reward/KL 实验代码——那些已保存在 **`v8-training-wip`** 分支（含 reward 重写、per-minibatch KL 修复、per-act 遥测、dashboard 修复、train_control、plan 文档），需要时可 cherry-pick（尤其 per-minibatch KL 修复对 sim 上的大 batch 训练有用）。
- **feature branch：从 `main` 新建 `feat/zhiyue-sim-backend`**（注意：本 session 已有一个基于 `v8-training-wip` 的同名分支，**作废/删除重建于 main**）。

## 现有可复用的架构（别重写）

- **环境后端接口 = EngineClient 的「面」**（`rl/src/sts2rl/engine.py`、`rl/src/sts2rl/protocol.py`）：
  - `reset(RunConfig)→DecisionState`、`reset_combat(CombatConfig)→DecisionState`
  - `step(ActionCandidate)→StepResult(state, terminated, error)`
  - `list_models(kind)`、`close()`、context manager
- **后端工厂模式已存在**于 `sim/combat-backend` 分支：`make_combat_backend(kind)` 返回 `SimCombatBackend()`（仅战斗）或真 `EngineClient`；`--combat-backend {real,sim}` argparse。**port 这个小工厂（~50 行 in `tools/m2_train.py`）**，**不搬** Codex 的 8.7 万行差分 sim。
- `DecisionState`(raw dict + candidates)、`ActionCandidate`(action str + args)、**动态 `EntityVocab`**（`rl/src/sts2rl/entities.py`，自动把 sim 的 id 当新 token 学入——sim-only 训练零映射表）。
- 关键锚点（阅读）：`ppo.py:run_episode`（唯一驱动 client 的地方，调 `reset/reset_combat/step`）；`protocol.py:DECISIONS`（合法 phase 集）。

## 设计：两个新后端 + 工厂扩展

### 1. 后端工厂（小，port）
`tools/m2_train.py`：`--combat-backend {real,sim,zhiyue}`；`make_combat_backend("zhiyue")` 返回 zhiyue 后端。

### 2. `ZhiyueCombatBackend`（中）— 新文件 `rl/src/sts2rl/zhiyue_backend.py`
包 zhiyue 的 `sts2_env.core.combat.CombatState`；实现 `reset_combat/step/list_models/close`。
- **状态适配**：`CombatState`（结构化对象，131 维扁平向量只是它的投影）→ 我们的 `raw`（`decision`/`player{hp,max_hp,block,energy,powers}`/`hand`/`enemies{hp,intent,powers}`/`context{room_type,floor,act}`）→ `DecisionState` + candidates（由 `sts2_env/gym_env/action_space.py:get_action_mask` 生成合法 `ActionCandidate`）。
- **动作适配**：我们的 `ActionCandidate`(`play_card`/`end_turn`/`use_potion` + 目标) ↔ 它的 action int（复用 `action_to_card_and_target` / `action_to_potion_and_target` 反向）。

### 3. `ZhiyueRunBackend`（大，**价值所在**）
包 `sts2_env/gym_env/run_env.py` 的 `RunManager`；实现 `reset(RunConfig)/step`——这是真 EngineClient / Codex sim 都不提供的**整轮**能力。
- **阶段映射**（它的 `RunManager.PHASE_*` → 我们的 `DECISIONS`）：`PHASE_COMBAT→combat_play`、`PHASE_MAP_CHOICE→map_select`、`PHASE_CARD_REWARD→card_reward`、`PHASE_SHOP→shop`、`PHASE_REST_SITE→rest_site`、`PHASE_EVENT→event_choice`、`PHASE_BOSS_RELIC→bundle/relic select`。
- 每阶段 candidates 由它的 `_ActionLayout`/mask（run_env.py 顶部有 slice 布局注释）生成 → 我们的 `ActionCandidate`（`select_map_node`/`select_card_reward`/`choose_option`/`buy_*` 等）。

### 明确不做
不搬 Codex 差分 sim；**不改**我们的模型/obs/动作空间/curriculum/reward；不做 real-transfer 的 580 卡映射表（sim-only 训练不需要，属后续 deployment 关注）。

## 依赖引入（license 友好）

zhiyue **无正式开源 license**（README 仅写 "for research and educational purposes"）→ **不把它的源码 commit 进我们仓库**。
- 方式：pip install 它的 `sts2_env`——git submodule 到 `vendor/sts2-rl-agent`（pin commit）或从 GitHub URL 装到 rl venv；写进 `requirements-sim.txt` + `scripts/setup_sim.ps1`。仅研究用。
- 核心依赖仅 `gymnasium>=1.0` + `numpy>=1.26`（已有 numpy）。

## 实施顺序（先 spike 验证收益，再全量）

**Phase 0 — 战斗 spike（~1 天，决定是否继续）**
1. port 后端工厂 + 写 `ZhiyueCombatBackend`（战斗）。
2. 单测：我们的模型能 `step` 它的 sim、obs 合法、动作往返一致。
3. 跑一小段 combat 训练，**实测**：端到端每轮 wall-clock/提速、能否学（win 从 ~30% 上行）、动态 vocab 干净。
   - **判据：真提速（目标端到端 ≥10x）+ 能学 → 继续；否则止步重估**（回答「PPO-update 天花板」问题）。

**Phase 1 — 整轮后端**：写 `ZhiyueRunBackend`（run_env 各阶段适配），接 act1 curriculum，跑通整轮 act1。

**Phase 2 — 量级实验**：用快后端跑之前跑不动的实验（大模型稳定训练 / 大数据 / hierarchical / 更好信用分配）冲破 72%。

> 2026-07-19 进度：native vocab、on-policy boss replay、per-minibatch KL guard
> 与 96-episode Act 1 基线已落地；100-seed dev 最高 18%，尚未突破 72%。瓶颈已从
> 采样/协议收敛到 boss 信用分配。实测、修复与下一实验见
> `plan/zhiyue_phase0_results.md` 的 Phase 2 章节。

## 关键文件

- **新增**：`rl/src/sts2rl/zhiyue_backend.py`（适配层，核心）；`rl/tests/test_zhiyue_backend.py`；`requirements-sim.txt` + `scripts/setup_sim.ps1`；`vendor/`（submodule，不入库源码）。
- **改**：`tools/m2_train.py`（`--combat-backend zhiyue` + 工厂 + 整轮后端接线；port 自 `sim/combat-backend`）。
- **参考（不改）**：`rl/src/sts2rl/engine.py`、`protocol.py`（接口契约）；`sim/combat-backend:tools/m2_train.py` 的 `make_combat_backend`（port 参考）。
- **它的（read）**：`sts2_env/core/combat.py`、`sts2_env/gym_env/{run_env,combat_env,action_space,observation}.py`、`sts2_env/core/enums.py`。

## 验证（端到端）

1. **单测**：backend 往返（state→DecisionState→action→apply）+ obs 合法（无 NaN、候选非空）+ 动作映射双向一致。
2. **提速实测**：同一 act1 combat 配置，`real` vs `zhiyue`，比每轮 wall-clock + steps/sec（Phase 0 判据）。
3. **学习校验**：zhiyue 后端跑 combat，win 从随机 ~30% 明显上升（对齐它 92% 的量级）。
4. **保真抽查**：几个已知战斗，对比 sim 与真游戏的关键结算（伤害/格挡/意图/能量）。
5. **整轮**：`ZhiyueRunBackend` 跑 act1，reached/cleared 曲线合理，reward_health 不反转（`m2_train` PPO 前 fail-fast）。

## 风险与红线

- **Sim-to-real gap**：它是重写的游戏逻辑（随机战斗胜率 29.9%），机制/数值未必和真游戏一致。研究/破平台够用；要部署得走它的 bridge（`bridge_mod/` + `sts2_env/bridge/`）验证迁移。
- **PPO-update 天花板**：我们旧 sim 后端只快 ~2x（PPO 更新 GPU-bound，`plan/sim_multiprocess_design.md`）。快 env 的收益**只在放大 batch/喂饱 GPU 时兑现**——Phase 0 必须实测端到端提速，否则不投 Phase 1。
- **License**：research-only，不再分发它的源码（submodule/pip，不入库）。
- **保真/覆盖**：它 act1 的遭遇/机制覆盖（`sts2_env/encounters/act1.py`）vs 我们训练所需（elites/boss/map/events）需抽查。
