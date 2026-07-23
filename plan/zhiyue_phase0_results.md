# Approach A — Phase 0 + Phase 1 结果：zhiyue 快模拟器后端

> 记录 Phase 0（战斗 spike，go/no-go）与 Phase 1（整轮后端）的实施与实测。Phase 0 = GO，Phase 1 = 完成。（原方案文档 `approach_a_zhiyue_sim_backend.md` 已归档进 [`timeline.md`](timeline.md) 的「Approach A」条目后删除。）

## 结论：**GO** ✅

端到端在真实训练管线上跑通、无保真告警、且能学。env 吞吐 ~54x，端到端每轮 wall-clock 采样已不再是瓶颈。可以进入 Phase 1（整轮 `ZhiyueRunBackend`）。

## 实施摘要（与原 plan 的差异）

原 plan 假设要**从头写** `ZhiyueCombatBackend`。实施中发现 **`sim/combat-backend` 分支已有一个完整、且带真游戏 parity 测试的战斗后端** `SimCombatBackend`——它本就包 zhiyue 的 `CombatState`（vendored @ `1b7e7ce` + 本地 parity 修正，见 `vendor/sts2-rl-agent/UPSTREAM.md`）。原 plan 误以为那是「Codex 的 8.7 万行差分 sim」。

经与用户确认，采用**混合方案**：复用这套已验证的适配器 + vocab + parity 测试，但把 `sts2_env` **源码放到 gitignore 的 `vendor/`（不入库，research-only）**，满足 license 红线。

### 关键文件
- `rl/src/sts2rl/sim/backend.py` — `SimCombatBackend`（protocol-0.2.0 适配器；从 `sim/combat-backend` 移植，`VENDOR_ROOT` 改指 `vendor/sts2-rl-agent`）。
- `rl/src/sts2rl/sim/__init__.py` — 导出。
- `tools/m2_train.py` — 后端工厂 `make_combat_backend`（`--combat-backend {real,sim,zhiyue}`）+ `--eval-backend`；sim/zhiyue 时用 sim 自带 catalog、跳过 real probe、不再强制 `STS2_GAME_DIR`/DLL（**sim-only 可在无真游戏的机器上端到端训练**）。
- `requirements-sim.txt` + `scripts/setup_sim.ps1` — 依赖与 vendor 重建（clone pinned `1b7e7ce` + 本地 parity 修正）。
- `.gitignore` — 新增 `vendor/`。
- 测试：`rl/tests/sim/`（往返 + parity 回归 + diff_harness）+ 新增 `rl/tests/sim/test_model_integration.py`（模型侧端到端 seam）。

## 实测数据

### 1) 提速（env 吞吐，单核）
`scratchpad/bench_sim_throughput.py`，随机策略，act1 weak/normal 轮转：

| 指标 | zhiyue sim（单核） | real 基线（plan 记录，12 workers） |
|---|---|---|
| 战斗/秒 | **164.6** | — |
| env 步/秒 | **3815** | ~70 |

→ **~54x** env 吞吐（且这还是单核 vs 12 核 real）。对齐 plan 记录的「单核 146 战斗/秒、3658 步/秒」。

### 2) 端到端每轮 wall-clock（真实训练管线，`m2_train`）
`--combat-backend zhiyue --stage normal_combat --workers 6 --device cuda`，48 episodes/iter：

- **~5.4 s/iter**（含采样 + 4 个 epoch 的 PPO 更新，on GPU）。
- 一轮 ~800 env steps。real 同规模仅采样即需 ~11 s+（800/70），**采样在 sim 下已不是瓶颈**——满足 plan 风险条目「收益只在喂饱 GPU 时兑现」的前提。

### 3) 能学（学习校验）
`normal_combat`，从随机初始化：

3-iter smoke：0.354 → 0.396 → 0.417。

40-iter run（`rl/runs/phase0_zhiyue_40`，6 workers / cuda / 48 ep）：

| 阶段 | train_win_rate |
|---|---|
| 前 3 iter | 0.46 / 0.21 / 0.38（随机水平，波动大） |
| iter 8 | 0.646 |
| iter 23 | 0.729 |
| last-10 均值 | **0.727** |
| 峰值 | **0.875** |

→ 从随机 ~35% 稳定上行到 ~73%（峰 0.875），中位 **6.1 s/iter**。学习明确。

`reward_health` 全程正常（`win_return ≈ +0.98`，`loss_return ≈ -0.98`，`inverted=false`）。

### 4) 保真
- `visibility.violations = []`，`nonfinite_features = 0`，`unknown_state_fields = {}`，`unknown_entities = {}`、`unknown_dynamic_entities = {}` —— **sim `raw` schema 与我们 obs 管线零 warning 对齐**。
- 动态 `EntityVocab` 干净收编 sim 的 card/enemy/power id（`test_model_integration.py` 断言）。
- **真游戏 parity**：`rl/tests/sim/` 21 passed（怪血/伤害/意图/阵容逐位对齐，覆盖 Byrdonis/Vantom/Waterfall Giant/Nibbit/Cubex 等）。

## 判据核对
- [x] 真提速（env ≥10x）：~54x。
- [x] 端到端采样不再是瓶颈（GPU-bound update 占主导）。
- [x] 能学（win 从 ~35% 上行）。
- [x] 动态 vocab 干净、保真无告警。

---

# Phase 1 — 整轮后端 `ZhiyueRunBackend`：**完成** ✅

包 `vendor/sts2-rl-agent/sts2_env/run/run_manager.py` 的 `RunManager`，实现整轮 `reset(RunConfig)/step`。这是原 plan 的「价值所在」，`SimCombatBackend` 只做战斗。

## 实现
- **新文件** `rl/src/sts2rl/sim/run_backend.py`（`ZhiyueRunBackend`）：
  - 阶段映射 RunManager `PHASE_*` → 我们的 `DECISIONS`：`MAP_CHOICE→map_select`、`COMBAT→combat_play`（复用 `SimCombatBackend._raw_state` + `get_combat_state()`）、`CARD_REWARD→card_reward`、`BOSS_RELIC→card_reward`、`SHOP→shop`、`REST_SITE→rest_site`、`EVENT→event_choice`、run/event/combat 的 pending choice → `card_select`；`TREASURE` 无决策，内部 auto-advance。
  - `get_available_actions()` 的 dict ↔ 我们的 `ActionCandidate`（每决策建 candidate→run-action 反查表，`step` 用它翻译回去）。
  - **双模式**：`reset_combat` 委托内部 `SimCombatBackend`，`reset` 驱动 `RunManager`——一个后端同时服务 combat 与 run 阶段，训练可从 combat rung 推进进 run。
- **接线** `tools/m2_train.py`：`make_combat_backend("zhiyue")` 返回 `ZhiyueRunBackend`；combat-only 的 run-stage guard 只对 `sim` 生效，`zhiyue` 放行 run 阶段。
- **修 obs 保真**：map 节点放 `choices`（成为可被 `select_map_node` pointer 绑定的 `choice` 实体）；`prompt` 加入 `observation._KNOWN_FIELDS`。→ run 全程 `normalize_state` **零 warning**。

## 实测
- **随机/启发式 driver**：8+30 runs 跑通全部 act1 阶段（map/combat/card_reward/event/card_select/shop/rest_site），无崩溃，obs warnings **none**，act/floor 单调，`game_over.victory` 正确。
- **god-mode（加血）**：15/15 runs 清 act1 进入 act2 —— 证明 boss 战 + boss-relic + act 转换 + `_act>max_act` 终止路径正确。
- **端到端 `m2_train --combat-backend zhiyue --stage act1`**：`errors:0`、`violations:[]`、`nonfinite:0`，全阶段动作齐全，depth telemetry 合理（median_floor 4），reward 不反转，~8-9 s/iter（16 ep, cuda）。
- 测试：`rl/tests/sim/test_run_backend.py`（3 tests）+ 全套 **83 passed**。

## 已知限制（Phase 2 处理）
- **静态 boss loadouts 不兼容 sim**：`m2_boss_loadouts.json` 是真引擎 harvest，卡名（如 `RAGE`）与 sim `CardId`（`RAGE_CARD`）不一致 → `reset_combat` KeyError。zhiyue run 训练须用 `--boss-mix 0` 或 `--on-policy-boss-replay`（sim 自采样 loadout，卡名为 sim 原生，兼容）。
- **固定 vocab 的 UNK**：`m2_train` 加载真引擎 `m2_vocab.json`，sim-only id（`CARD.GRAPPLE`、`ROOM.*`、`POTION.*` 作为 reward 实体）落 UNK（记于 telemetry，非 violation）。sim-only 训练应改用**从 sim states 构建/动态扩展的 vocab**（`EntityVocab.from_states`），零映射表收编 sim id。
- boss_relic/potion/relic reward 目前统一走 `select_card_reward`（entity kind=card，id 前缀区分）；reroll/sacrifice 未暴露。act1 训练足够；deployment/更全内容再细化。

## 下一步（Phase 2）
用快后端跑之前跑不动的量级实验冲破 72%：sim-native vocab + `--on-policy-boss-replay`，放大 batch 喂饱 GPU，大模型稳定训练 / 大数据 / hierarchical。

---

# Phase 2 — native vocab、on-policy replay 与放大 batch 基线

## 工程门槛：完成

- `m2_train --vocab-source auto` 在 `sim/zhiyue` 下会在建模前做确定性
  simulator discovery：遍历 Act 1 encounter，驱动 16 条高 HP 整轮，并把
  Card/Power/Relic/Potion 与 rest-site option registry 投影成真实 protocol state，
  再通过 `EntityVocab.from_states` 收编。新 id append 到固定 vocab 尾部，旧 checkpoint
  索引不变；结果持久化到 run 的 `entity_vocab.json`，resume 时 append-only refresh。
- sim-native 模式把任何 unknown entity 升级为训练前 contract failure。该检查实测拦住了
  稀有 `LIFT` rest option；补 registry discovery 后 held-out 与训练 batch 均为零 UNK。
- zhiyue 默认启用 on-policy boss replay；整轮 boss state 现在携带实际 encounter id，
  protocol 别名卡牌（例如 `FLAME_BARRIER` → simulator `FLAME_BARRIER_CARD`）和 potion
  casing 通过 simulator enum/registry 反查，无手写内容映射表。
- 当前 buffer **73/73** 条 loadout 可 `reset_combat`；后续训练扩到 256 条 rolling window。
- 修正 combat-only replay 的 potion 合法性：以 simulator `can_use_potion` 设置 raw
  `can_use`，不再把 Fairy in a Bottle 等被动/不可用 potion 提供给 policy。
- 移植大 batch 必需的 per-minibatch hard-KL guard；epoch 内超过上限会在应用该
  minibatch 更新前停止，并记录实际 `minibatches`。
- 低频 episode error 现在在 history 中区分 main/replay 并保存异常 message，不再只留计数。

## 验证

- 新 native vocab：size **2997**（fixed 2605 + simulator-native 392），独立 held-out
  god-mode Act 1 probe 零 unknown。
- 端到端 smoke：Act 1，16 episodes，`errors=0`、unknown `{}`、violations `[]`、
  nonfinite `0`。
- replay buffer reset audit：73/73 成功（修复前分别抓到单词 potion `FORTIFIER`
  与 embedded-capital `FAIRY_INA_BOTTLE`）。
- 最终测试：`rl/tests` **131 passed**。唯一 pytest warning 来自故意让 inference server
  抛异常、验证 worker 不挂死的既有测试。

## Phase 2 放大 batch 基线（尚未突破 72%）

硬件：RTX 5080；模型 h128 / 2 layers / 4 heads。先从 Phase 0 combat checkpoint
训练 mixed combat：192 episodes/iteration、minibatch 512、lr 1e-4、8/24 workers。

- mixed combat 5 轮后 frozen dev = **80%**，通过 gate。
- Act 1 使用 96 episodes/iteration、8 workers、minibatch 512、on-policy boss replay；
  20 个训练 iteration 平均 **71.1 s/iter**（49.8–89.8 s），比 real v7 同量级约
  150 s/iter 快约 2.1x，且能快速反复跑 gate。
- boss buffer 第 2 个 Act 1 iteration 达到 ready；之后将 boss mix 从 15% 提到 25%。
- frozen 100-seed dev：

| iteration | win | reached boss | boss conversion | errors |
|---:|---:|---:|---:|---:|
| 49 | 3% | 36% | 8% | 0 |
| 54 | 10% | 59% | 17% | 0 |
| 59 | **18%** | **63%** | **29%** | 0 |
| 64 | 14% | 51% | 27% | 0 |

真实引擎 v7 checkpoint 直接迁移到 zhiyue 仅 **16%** dev（reached boss 47%、
conversion 34%），证明 sim-to-real gap 很大；不可把 real 72% 和 sim 曲线直接横比。

## 结论与下一实验

Phase 2 的数据工程路径已成立：native vocab 零 UNK、on-policy replay 真正闭环、
大 batch 稳定且所有适配错误已被 contract/test 覆盖。但 vanilla reward + 25% boss
replay 在当前短跑中最高仅 18%，未突破 72%。当前瓶颈是 boss 信用分配而非采样或
协议；这直接促成了下面的专门 `boss_combat` curriculum ablation。

### 专门 boss curriculum ablation

在上述结论后，训练器进一步支持：resume 时若 native buffer 已达到阈值，就把它作为
第一类 `boss_combat` rung 加入 ladder；checkpoint 按 stage 名称恢复，避免新增 rung
导致旧 `stage_index` 错位。

用 iteration 64 Act 1 checkpoint、256 条 rolling loadout、192 boss fights/iteration
跑 10 轮：

- train boss win：5.7% → 15.6%；全程 errors 0。
- frozen 100-fight boss dev：iteration 69 = 12%，iteration 74 = 13%。
- 约 24–47 s/iteration；数据量已足够，泛化提升仍很小。

因此 boss replay 的问题不是“样本太少”：专门 rung 把每轮 24 个 replay 放大到 192
仍只得到 13% dev。下一实验应优先做 h256 + hard-KL 的容量对照，或独立 ablation
dense combat/elite/boss reward；继续提高 replay fraction 的信息价值很低。

---

# Phase 2 续：容量对照 + sim→real 迁移验证（进行中）

## 策略校正：sim 不是死路，而是把瓶颈从「采样」定位到了「容量/信用分配」
喂饱采样后性能没自动上去（act1 18%、boss replay 放大 8× 仍 13%）→ **证伪了「采样饥饿是主因」**。真瓶颈是模型容量 + boss 信用分配，这在 real 上同样存在且更难做实验。sim 的价值正是能几十分钟迭代这些。**唯一能判 sim 死活的是「sim→real 迁移」：sim 上找到的方法迁回 real 是否也涨。目前只知 real→sim 反向掉到 16%，正向未验。**

## 实验 A：容量对照 h256 vs h128（进行中，`rl/runs/p2_cap_h256`）
- 只变 `--hidden 256`（vs Codex h128），其余对齐：layers 2 / heads 4 / lr 1e-4 / minibatch 512 / episodes 96 / workers 8 / boss-mix 0.25 / sim-native vocab / on-policy replay。
- 从 `mixed_combat` 爬到 `act1`（`--max-stage act1`）。h128 baseline = Codex 的 act1 dev ~18%。
- 初步：mixed_combat 正常收敛（iter 6-9 win 0.44→0.57，~10 s/iter）。**判据：act1 dev 是否明显超 18% → 容量是瓶颈则继续 scale；若同样卡 ~18% → 换信用分配/reward 方向。** 结论需数十 iter（几小时），看 `history.jsonl` 的 act1 `dev_win_rate`。

## 实验 B：sim→real 迁移验证工具（就绪）
`tools/transfer_eval.py`：对**同一 checkpoint** 在 sim 与 real 各评估 act1 dev，直接量化 gap。本机无真游戏，只能跑 sim 侧；real 侧须在装了游戏的机器跑。
```bash
# sim 侧（本机可跑，得 sim dev 基线）
python tools/transfer_eval.py --run-dir rl/runs/p2_cap_h256 --backend sim  --episodes 50
# real 侧（有游戏的机器：设 STS2_GAME_DIR、build DLL 后）
python tools/transfer_eval.py --run-dir rl/runs/p2_cap_h256 --backend real --episodes 50
```
读法：sim `cleared_rate` ≈ real `cleared_rate` → sim 是有效加速器，sim 路线成立；sim ≫ real → sim 上的收益不迁移，才是 sim 死路的证据。**这是整条 sim 路线的 go/no-go。**（脚本已用现有 checkpoint 验证可跑：step-74 ckpt sim act1 dev 10%。）

## 下一步
1. 等 `p2_cap_h256` 到 act1 收敛，读 act1 dev 对照 h128 的 18%。
2. 取 h256（或任一）sim-trained checkpoint，在有游戏的机器跑 `transfer_eval --backend real`，定 sim 路线生死。
3. 若容量是瓶颈：继续 scale + dense boss reward ablation；若迁移失败：回 real 硬扛容量/信用分配。
