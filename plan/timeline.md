# RL v2 时间线

按时间顺序的完整历史。`rl_v2_roadmap.md` 是权威路线与验收门槛，`rl_v2_current_stage.md` 是当前阶段的工作文档，**本文件是历史账本**——每个里程碑、每个阶段、每一轮返工，做了什么、证据是什么、结果如何。

## 三套编号，别混

```
M0 ─ M1 ─ [M2] ─ M3 ─ M4 ─ M5 ─ M6          里程碑（roadmap 权威）
            │
            └─ P0 ─ P1 ─ P2 ─ P3 ─ [P4] ─ P5    M2 内部的施工阶段
                                    │
                                    └─ v1…v6      P4 训练中的返工轮次
```

- **M**（里程碑）：项目级，线性推进，写在 roadmap。
- **P**（阶段）：某个 M 内部的施工分解，**只走一遍**。
- **v**（版本）：**P4 训练撞墙 → 发现盲点 → 改代码 → 从头重训**的循环计数。不在任何设计文档里，是训练过程中自然长出来的。

**关键教训见文末「P 阶段的返工分布」——v1–v5 五轮返工全部打在 P2（表征）上，而真正的 bug 一直待在被标记为「✅ 完成」的 P3（奖励函数）里。**

---

# M0 — 清理、引擎验证与基准

**2026-07-10 · 已完成**

## 目标

归档 RL v1；清空旧 RL 代码；固定并构建 CLI；冻结 schema；验证真实引擎可被程序化驱动。

## 关键事件

- 创建 `archive/rl-v1-final`（tag `rl-v1-final-2026-07-10`），删除旧 `rl/`、模型、日志、**自建模拟器**；`mod/`、`mcp/`、游戏安装未动。
- 将官方 `sts2-cli` 以 submodule 固定；`sts2.dll` 复制 + IL patch + .NET build（0 errors）。
- 建立协议类型、动态合法动作、稳定状态 hash、固定 seed split、超时检测、持久进程客户端。
- 建立计划同步 CI（`tools/check_rl_plan_sync.py`）与不依赖游戏 DLL 的单测。

## 验收证据

- **吞吐**：1/4/8/16 worker steady benchmark = **20.48 / 73.14 / 122.81 / 140.20 decision steps/s**；8 workers 过 100 门槛，benchmark errors **0**。
- **五角色完整 episode**：Ironclad / Silent / Defect / Necrobinder / Regent 各 20 局，**全部 `game_over`**，非法动作与 timeout **0**。
- **确定性**：同 seed 两次 `reset` 状态 hash 一致（`685e709d…`）。
- **自动恢复**：手动 kill 持久 CLI 后，下一次 `reset` 自动重启成功。
- schema 观察清单落盘 `rl/schema/m0_observed_schema.json`（event / map / combat / card reward / shop / bundle / card select / game over / rest site）。

## 遗留给 M1

- 「任意指定牌组/敌人/遗物/HP/seed 的原子 curriculum reset」协议不存在（上游只有 `start_run`/`load_save`/`set_player`/`enter_room`/`set_draw_order`）。
- `card_select` 多选组合、药水目标、商店移除选牌的动作覆盖未确认。

---

# M1 — 通用环境与训练基础设施

**2026-07-10 → 2026-07-11 · 已完成**

> 完整原始记录见 git：`git show bbc48db:plan/rl_v2_current_stage.md`
> （**注意**：`rl_v2_current_stage.md` 里引用的 `8d6bf01` 在本分支历史中不存在，是坏引用；正确锚点是 `bbc48db`。）

## 目标

进程池、normalization、Gymnasium 环境、Transformer/GRU pointer policy、BC、PPO/GAE、trajectory、checkpoint/resume、统一评估。

## 关键事件

### 引擎侧：nested card-select 超时的根治

- **根因**（2026-07-10）：`RunSimulator.DetectDecisionPoint()` 在每个战斗动作里**二次调用** `WaitForActionExecutor()`。当第二层嵌套选牌（Necrobinder Snap、Regent Begone）在第一层效果提交前 resolve，游戏的 `ActionExecutor.IsRunning` 会**永久卡在 true**。等待循环写成「自旋 1000 次 `Thread.Sleep(1)`」，作者意图约 1s——但 **Windows 默认定时器精度把 `Sleep(1)` 舍入到约 15.6ms**，实际每次自旋约 15.6s，两次共约 31s，超过 10s 客户端超时。
- 用 Harmony 反射对 `Thread.Sleep` 做 burst 累加诊断 + 时间戳复现，捕获到完整调用栈与精确 31s 耗时后确认。
- **修复**：自旋改为基于 `Stopwatch` 的真实 1000ms 上限；进一步根治为 `HeadlessCardSelector.ResolvePending` 的 clear-before-complete、`SingleThreadDispatcher.Post` 只入 FIFO 不内联重入、命令完成不再依赖 `ActionExecutor.IsRunning`。
- 引擎侧改动只进 fork 分支 `rl-v2-protocol-state-machine`。

### fail-closed 加固

- quiescence / `RunInline` / dispatcher callback / `Send` 超时或 fault → **永久 poison 当前进程**、取消未开始的 FIFO callback、返回 fatal error，由 Python kill/restart。
- fail-closed 首轮 200 局把此前**被吞掉的** fault 显式化为 7 个 `EngineFatal`。

### 训练侧：两个会静默毒化数据的 bug

- **`collect_episode` 的轨迹语义 bug**：原实现在 `step()` **之后**记录，`state`/`legal_actions` 存的是动作执行**后**的新状态，而 `action` 选自**旧**状态 → BC 的目标索引未定义。守护测试 `test_transition_records_the_state_the_action_was_chosen_from`。
- **`rl/pyproject.toml` 的 `dependencies = []`**：`torch`/`pyarrow` 从未被安装，**训练侧代码从未被执行过**。

## 验收证据

- **1,000 局 A0**：6 persistent workers、timeout 10s、五角色各 200 局 → **1000/1000 `game_over`**，EngineTimeout **0**、ProtocolError **0**、非终止 **0**，273–280s，孤儿进程 **0**。
- **可复现性**：独立重跑逐 seed 的 steps/outcome **1000/1000 完全一致**。
- **seed 隔离**：同 worker 重复 reset、跨角色 reset、另一 worker reset 的 anchor hash 全为 `01d53fd1…`。
- **跨进程 resume**：采集 1,206 个决策 → BC 更新 → step 10 落 checkpoint → **在全新进程中** resume 续训到 step 20，loss 序列与不中断训练**完全相同**，参数 hash 同为 `f3b148ca…`。
  （**进程内 resume 只能证明 `load_state_dict` 没报错，不能证明 checkpoint 足以在进程消失后重建训练。**）
- **可学习性**：真实 batch 过拟合 1.4236 → 0.0538（随机策略数据的 BC loss 本就不会降，其最优解是候选上的均匀分布）。

## 有意的动作空间偏离（客户端屏蔽，非根治）

- **事件内 card reward 不暴露 `skip_card_reward`**：引擎侧 `can_skip` 仍为 `true`，屏蔽在 `protocol.py::legal_actions`。代价是 agent 永远无法跳过事件奖励。M2 P0 复核后决定保留。

---

# M2 — Ironclad

**2026-07-11 → 进行中**

## 验收门槛（roadmap 权威）

5 个独立初始化 × 隔离的 1,000 test seeds：**A0 平均通关率 ≥40%**，95% bootstrap CI 下界超过启发式基线，非法动作 0、timeout <1%，完成 reward ablation（shaped vs terminal-only）。

---

## P0 — 基线与前置复核 · ✅

**2026-07-11**

- **seed split 冻结**：`rl/seeds/m2_ironclad_seed_split.json`，命名空间 `m2-a0-ironclad-<0..9746>`，1,000 test seeds（hash `d4d636c2…`）+ 500 development seeds（hash `abb4c775…`）。**test seeds 在 P5 前不参与任何选择。**
- **基线实测**（前 200 个 dev seeds）：random **0/200** 通关、heuristic **0/200** 通关，95% bootstrap CI 均为 [0, 0]。
  → **结论：启发式基线在完整 A0 上通关率为 0，「CI 下界超过启发式」等价于「下界 > 0」，验收实际由 ≥40% 门槛主导。**
- **阻塞发现**：固定 commit `7fe0006` **从未以提交状态通过验证**。M2 首次基线 200 局全部 `ProtocolError`——`HeadlessPresentation.Install()` 的 Harmony 补丁引用了 GodotStubs 中不存在的 `AudioServer`，**100% StartRun 失败**。M1 验收所用二进制早于最终源码状态。
  → **教训：固定 submodule commit 前必须从干净源码重建并跑回归。**

---

## P1 — curriculum reset 协议扩展 · ✅

**2026-07-11**

- 新命令 **`start_combat`**：原子 `start_run` → `set_player`（hp/max_hp/gold/deck/relics/potions）→ `enter_room`（指定 encounter），任一步失败 fail-closed。
- 新命令 **`list_models`**：枚举 encounter（含 act/act_id/category）、card、monster、relic、potion、event、power、character 的规范 id。
- 修复三个引擎缺陷：`SetPlayer` 写 relics backing list 导致下一场战斗 NRE；headless 从不运行 `ModManager.Initialize` 导致 `AllPowers` 永远抛错；**跨 episode 污染**——胜利后不领 card_reward 直接 reset，遗留 `_pendingRewards` 使下一局直接打开上一局的奖励界面（**训练数据被污染成秒胜**）。

---

## P2 — 表征与模型 · ✅（但被 P4 反复打脸，见版本谱系）

**2026-07-11**

- `sts2rl.entities`：每类实体 id 词表 + UNK=0 + 告警队列、`phase_id`、padding/mask。
- `sts2rl.model.EntityRecurrentPolicy`：实体 Transformer + phase embedding + 候选 pointer + GRU 历史 + value head。
- `tools/m2_build_vocab.py` 从 `list_models` 构建 `rl/schema/m2_vocab.json`（最终 1,435 项）。
- 状态序列化补充 enemy/relic/potion/power 的稳定 ModelId（`MONSTER.X` / `RELIC.X` / …），词表不再依赖本地化名字。

---

## P3 — 训练环路 · ✅ ⚠️ **（真正的 bug 藏在这里，直到 v6 才被发现）**

**2026-07-11**

- `sts2rl/curriculum.py`：四阶段梯子，encounter 按 seed 确定性采样。
- `sts2rl/ppo.py`：Recurrent Masked PPO + GAE、advantage 归一化、entropy、梯度裁剪。
- **奖励**：终局 ±1 + `0.2 × potential-based` 楼层进度。`--terminal-only` 为 ablation。
- `tools/m2_train.py`：多 worker 持久引擎并行采样、dev 评估晋级门槛、TensorBoard、checkpoint/resume、错误率 >5% fail-closed。
- `tools/m2_final_eval.py`：P5 验收管线（5 checkpoint × 1,000 test seeds、bootstrap CI、门槛检查）。

**⚠️ 此阶段被标记为「完成」后，整整五个训练版本（v1–v5）里没有任何人、任何测试、任何图表回头检查过它。**

---

## P4 — curriculum 训练 · 🔄 进行中

**2026-07-11 → 现在**

四阶段梯子：`normal_combat → mixed_combat → boss_combat → act1 → full_a0`。

### 版本谱系

每一轮都是「训练撞墙 → 诊断出策略看不见/被教错某样东西 → 改代码 → checkpoint 不兼容 → 从头重训」。

#### v1 — 初版

**改的是 P2** · act1 dev **恒为 0**

#### v2 — 选路是瞎的

**改的是 P2** · act1 仍 ~0，卡在 10 层

`select_map_node` 的候选只带 col/row，而候选特征**没有对应槽位**——**所有地图节点编码完全相同**，Rest 与 Monster 不可分，选路是纯盲选。修复：候选特征加 col/row 槽（`CANDIDATE_FEATURE_DIM` 16→18）、实体数值加 col/row（11→13）、词表新增 7 种房间类型。

#### v3 — 指针看不见所指的东西

**改的是 P2** · **首批真实胜利**（dev 3/50）

`select_card_reward` / `play_card` / `use_potion` 的候选只带索引标量，pointer 无法「看到」所指实体 → 卡奖励选择≈随机 → 牌组不成长 → 中段墙。修复：`candidate_entity_slots` 把候选映射到实体行，pointer head **gather 所指实体的 Transformer 输出**。

#### v4 — 事件是瞎的

**改的是 P2 + P1（引擎）** · act1 dev **最佳 12%**，avg_floor ~14 · **← 长期基线**

事件本体无标识，选项只有本地化文本。引擎侧 `event_choice` 增加稳定 `event_id`，选项带 `text_key`；observation 合成 event 伪实体。

同期加入 **boss 桥接阶段**（收割 40 个真实中期 loadout × Act1 boss），以及保升级的 `SetPlayer`（`BASH+` 语法）。

#### v5 — 牌组是瞎的

**改的是 P2** · 探针证实生效，但**胜率没超过 v4**

策略看不见自己的牌组 → 选牌奖励≈随机。把牌组作为 `deck_card` 实体加入观测 + 组牌协同探针。

**探针结论**（act1-only checkpoint 序列）：`--strip-deck` 盲参照下三个牌组内容探针 delta **恰好为 0.0000**，因此 deck-visible 侧的 delta **因果上完全归因于牌组内容**。`perfected_strike` 在 ckpt_459 达 **+0.540**（协同牌组下选取概率 0.83 vs 对照 0.29）。**牌组感知是在 act1 里学出来的。**

**但 act1 胜率仍从 0.074 单调跌到连续三次 0/50，avg_floor 却一路爬到 14.2。**
当时的诊断（**后被证明是错的**）：灾难性遗忘 + boss 采样池错误。

#### v6 — 奖励函数把目标教反了 · **← 当前**

**改的是 P3（第一次！）+ 协议层 + 课程** · **boss 转化 0/31 → 5/13**

**2026-07-14。这不是又一个表征版本——它是第一个回头检查 P3 的版本。**

| bug | 影响 | commit |
|---|---|---|
| **🔴 奖励反转** | potential-based shaping 要求**所有终局势能 Φ=0**。但赢下一幕后的状态**不带 floor**（Φ=0，歪打正着），死亡的 `game_over` **带 floor**（Φ=0.2×17=3.4）。这个不对称给每个深度死亡白送约 **+2.9** 的 shaping。**实测：死在 boss 面前 +1.76，赢下 boss +0.65——9 个深度死亡局的回报全部高于 2 个胜局，无一例外。策略在忠实执行我们教的目标：走到 boss 面前，然后去死。** | `e7f8f77` |
| **🔴 药水不可达** | `legal_actions` 读顶层 `potions`，引擎只发 `player.potions`，且不发 `can_use`。**`use_potion` 候选从未被产出过一次。** v1–v5 带着中位 3 瓶药水打 boss，一瓶都开不了——而观测层**看得见**它们。 | `6d33d1a` |
| 课程污染 | Act 1 遭遇池 **48%** 是真实局里永远不会出现的敌人（Underdocks 区域），三个阶段的晋级门槛全在污染池上校准 | `bdedd83` |
| 哨兵数值炸梯度 | Waterfall Giant 的 999,999,999 血 → 前向被 LayerNorm 掩盖、**反向 softmax 梯度溢出成 NaN** | `87d6b61` |
| 推理线程死亡挂死训练 | 一天挂死两次（NaN 一次、驱动更新一次），12 个引擎空转 | `6163131` |
| **训练期诊断 + Diagnostics 视图** | 赢/输回报对比、动作占比、boss 漏斗——**这些 bug 能活五个版本，就是因为没人看** | `9048707` `93bcd8b` |

**结果**（`m2_v6_resume2`，365 个 act1 迭代 / 14,965 局 / 36 次 dev 评估）：

| | v5 | **v6** |
|---|---|---|
| boss_combat dev | 0.19（320 迭代平台，**从未过 0.30 门槛**）| **0.36（10 迭代即过）** |
| **act1 boss 转化率** | **0/31 = 0%** | **均值 19.7%，最高 38%（36 次评估）** |
| **act1 dev 最佳** | **0.14** | **0.22** |
| act1 `train_win_rate` 趋势 | 0.004 → 0.002（**跌**）| **0.0024 → 0.0634（涨 26 倍）** |
| act1 `avg_floor` 趋势 | 靠**错误**奖励爬到 14.2 | **10.07 → 12.21（靠正确奖励）** |

**漏斗把话说完了**（最佳评估，iter 179）：到达 boss **64%** × 转化 **34%** = 胜率 **0.22**。

**v5 的到达率是 62%——和 v6 几乎一样。策略一直知道怎么走到 boss 面前。全部差距在转化率上：0% vs 34%。**

**那堵「boss 墙」——v4 为它造了桥接阶段、收割 40 个 loadout、改引擎支持升级卡语法；v5 为它烧掉 62% 的训练预算（15,408 局）；我今天上午还为它写了防遗忘的 `--boss-mix`、诊断了「桥接→整局转化缺口」——根本不存在。策略被奖励函数教着走到 boss 面前去死，手里三瓶救命药还开不了。**

### 事故：两条并行 run 抢 GPU，我几乎改掉一个正确的奖励函数

`rl/runs/` 下同时存在两条从同一个 `ckpt_00049` 恢复的 act1 训练：`m2_v6`（我的）与 **`m2_v6_resume2`（另一个会话或手动命令起的）**。配置完全相同，仅 run 名不同，**并行争抢同一块 GPU**——凌晨的 CUDA `unknown error` 与性能异常很可能即源于此。

我只监控了自己那条。它因抢占严重欠训（63 迭代 vs 343 迭代），数据显示 `avg_floor` **9.71 → 8.07 崩塌**、胜率归零。**我据此论证「potential-based 修复抹掉了深度信号」，并已开始重写 shaping。**

真实的 run 显示完全相反（见上表）。**奖励修复是对的。**

**教训**：
- **开训前必须确认没有其他 trainer 在跑。** 多条 run 抢 GPU 不污染数据，但会**制造出「策略在退化」的假象**。
- **在一条欠训的 run 上读趋势，比读噪声更危险**——噪声看起来就是噪声，而欠训的数据会给你一个**连贯、自信、方向明确、但完全错误的故事**。我当时甚至没有怀疑，因为它「解释」得太顺了。

---

## P5 — 最终验收 · ❌ 未开始

管线已验证可用（`tools/m2_final_eval.py`，用 dev seeds 端到端跑通）。正式验收需 5 个初始化 × 1,000 test seeds。

---

# P 阶段的返工分布 —— 本项目最大的教训

```
P0 基线      ✅ ──────────────────────────────  0 次返工
P1 协议      ✅ ────── v4 ────────────────────  1 次
P2 表征      ✅ ── v1 v2 v3 v4 v5 ────────────  5 次  ← 所有注意力都在这
P3 奖励环路  ✅ ────────────────── v6 ────────  1 次  ← bug 一直在这
P4 训练      🔄
P5 验收      ❌
```

**v1 到 v5，五轮返工，全部打在 P2（表征）上。四个盲点都是真的，也都修对了——但它们只是让策略更高效地走向一个错误的终点。**

**P3 在 2026-07-11 被标记为「✅ 完成」之后，再没有人回头看过它。** 于是奖励函数把「死在 boss 面前」定价得比「赢下 boss」更高，安安稳稳地跑了五个版本。

**这两个 bug 都不难找。** `reward_health` 和 `action_mix` 加起来不到 30 行，数据早就躺在 episode 记录里。**它们能活这么久，是因为没有人问过「赢是不是比输值钱」这个最基本的问题。**

**制度性补救**（已实施）：`sts2rl.telemetry` 每个迭代自动计算并落盘——赢/输回报对比（违反即向 stderr 尖叫）、各类动作占比（恒为 0 = 策略够不到它）、boss 漏斗（`avg_floor` 会掩盖真相）。Diagnostics 视图把它们放在无法忽略的位置。

**给未来的规则：一个 P 阶段被标记为「完成」，不代表它被验证过。表征修得再好，也只是让策略更高效地走向一个错误的目标——先确认目标是对的。**
