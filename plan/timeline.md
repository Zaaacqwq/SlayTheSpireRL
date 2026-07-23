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

#### v7 — 大模型不是可见性修复 · 主动停止并审计

**改的是模型容量 + P4 吞吐** · `hidden=256 / layers=4 / heads=8` · iteration 123 主动停止

旧 `m2_v7_h256` 从零训练，参数量约为 v6 的 5.7 倍。normal/mixed/boss 很快晋级，Act 1 dev 最佳 8%，随后 avg_floor 从 11.54 降到 6.04。run 于 Windows 15.625ms 等待问题修复时主动停止；性能修复语义不变，不能解释策略退化。

停止后首次对模型输入本身做全轨迹可见性审计（704 artifact、58,301 决策），发现 shop 完全不可购买/删卡、card-select 多选候选碰撞、bundle 内容全为 `UNK`、目标敌人无指针、power amount/intent/boss/完整地图/卡牌改造/目标实际伤害不可见，且 unknown/offered 告警未接入训练。旧 v7 checkpoint 不再续训；保留大模型方向，重建可见性契约并从头训练（原 `v7_clean_plan.md` 计划已归并入下方 v7-clean 各条）。

#### v7-clean — P1–P3 可见性防火墙完成（2026-07-14）

完成 shop 购买/删卡动作、bundle 内容、多选集合、目标敌人/目标实际伤害绑定；补入敌我 power、intent、boss、完整地图、orb/slot、enchantment/affliction、事件稳定变量与 13 维归一化 global。候选可绑定最多 16 个语义实体，模型对绑定集合做 masked pooling。

词表改为完整 ModelDb 目录 + 真实引擎 sweep + 审核 smoke 增量生成，共 2,299 个稳定实体。累计 `visibility_audit.json` 覆盖 381 个 episode、24,007 个决策，15 类动作全部 offered/chosen；collision、pointer miss、unknown field/entity、non-finite 和 violations 全为 0。最终 256×4×8 smoke 完成 PPO 更新：KL 0.00474、clip fraction 0.0574、grad norm 0.922→0.440、explained variance 0.0254。旧 v7 checkpoint 继续冻结；正式 `m2_v7_clean_init0` 尚未启动，下一步做 LR 短程对照。

external 协议扩展已固定为 `e1a0688e2d873ddfe5bd8dd898369b7749d5c54c`。完整 external suite 在修复测试夹具 stderr 管道阻塞后完成：70 passed；2 个旧 save/load 用例失败于当前游戏构建的 `RelicGrabBag` 重复 Populate，记录为与训练路径分离的兼容遗留项。

#### v7-clean — P4 LR/批处理校准（2026-07-14）

原始 minibatch 512 随机混入完整地图状态，导致所有样本 pad 到超宽实体序列：RTX 5080 占用约 15.8GB，10 分钟仍未完成一轮。PPO 改为按实体长度装箱、桶/桶内仍随机，minibatch 256 后完整 train+50-dev 一轮降至 84–92 秒、峰值约 8.4GB。

同 init/seed 流的一轮结果：`lr=5e-5`（6,341 steps，KL 0.00667，dev 12%）、`1e-4`（6,278，KL 0.01857，dev 2%）、`3e-4`（6,176，KL 0.02804，dev 6%）。50-seed dev 单点噪声大，不用于 LR 排序；`3e-4` 越过 0.02 hard stop，`1e-4` 接近 hard stop，正式起点选 `5e-5`。

#### v7-clean — on-policy boss replay（2026-07-14）

正式训练不再读取 v4 盲策略生成的 `m2_boss_loadouts.json`。新增 `--on-policy-boss-replay`：仅从当前策略真实抵达 boss 的首回合记录提取 HP/max HP、保留 `+` 升级层级的完整牌组、遗物、药水和实际 boss encounter；run 内原子持久化，8 个快照后启用 15% replay，滚动上限 256。boss 与快照成对恢复，不再随机把某个 boss 的牌组状态配给另一个 boss。

验收包括 121 项 Python/根目录测试，以及真实引擎恢复：从 artifact 抽取 `THE_KIN_BOSS`（39 HP、17 张牌、1 张升级牌）的快照后，`start_combat` 正确进入 `combat_play`。两局 CUDA trainer smoke 明确报告 `boss_replay_source=on_policy`、0 engine error、0 visibility violation；旧静态 boss stage 未进入阶段表。正式配置据此固定为 256×4×8、`lr=5e-5`、minibatch 256 length packing、96 局/轮、12 workers、KL 0.01/0.02、`--max-stage act1`。

正式 `m2_v7_clean_init0` 随后由 watchdog 启动。iteration 0 完成 96 局 / 1,709 decisions：0 engine error、0 visibility violation，KL 0.00354，4 epochs，grad norm 0.996→0.460，reward 未反转，耗时 22.1 秒。iteration 9 normal dev 达 42/50（84%），超过 80% 门槛，保存首个 checkpoint 后晋级 mixed。长跑限制在 Act 1，未经三次 gate 与 450-seed audit 不会自动进入 full A0。

#### v7-clean — mixed→Act 1 恢复循环与 append-only vocab（2026-07-14）

iteration 79 mixed dev 40/50 后首次进入 Act 1，strict audit 在 PPO 前拦截 `THE_FUTURE_OF_POTIONS.pages.INITIAL.options.POTION` 与 `DigRestSiteOption`。同时发现 checkpoint 先保存旧 `stage_index`、再执行晋级，导致 watchdog 总是恢复 mixed；每十轮生成一个新 mixed checkpoint 后再次撞同一 Act 1 防火墙。未知数据从未进入梯度，但浪费了多轮 mixed 训练。

修复分三层：(1) gate 后先计算下一 stage，再把下一 stage/index 原子写入 checkpoint；(2) watchdog 即使单次运行超过 `min_uptime`，只要连续从同一 checkpoint 失败也会熔断；(3) 词表改为全局 append-only。因为所有 kind 共用一个 id embedding，旧的按 kind 重排会让 option 插入点之后一千多个实体权重静默错位；现在旧 1–2298 索引逐个不变，Dig、Lift、Slippery Bridge 两个后续页和 Future of Potions 只追加为 2299–2303。

修后全量审计覆盖该 run 的 1,200 artifacts / 87,212 decisions：15 类动作全部 offered/chosen，collision、pointer miss、unknown field/entity、non-finite 与 violations 全为 0。Python 测试 126 项通过。`ckpt_00079` 加载到 2,304 行词表时只迁移 `id_embed.weight`，新增尾行清零并按既定安全策略重置 Adam；旧实体权重不变。

恢复验证：以 `--stage act1 --max-stage act1` 从 checkpoint 79 启动，iteration 80 完成 82 个整局 + 14 个 boss replay / 10,805 decisions，0 engine error、0 visibility violation，所有 15 类动作均 offered/chosen。KL 0.0110 触发 target early stop、仅跑 1 epoch且未越过 0.02 hard stop；说明新 optimizer 的首步被正确约束，训练阶段也已真正保持在 Act 1。

#### v7-clean — 完整事件选项目录与逐迭代恢复（2026-07-14）

恢复后的 iteration 80–82 均正常，但 iteration 83 在 Slippery Bridge 连续撞到 `HOLD_ON_2→3→4→5→6` 四个新 option，strict audit 在更新前中止。检查 checked-in 英文 localization 后确认完整链继续到 `HOLD_ON_6→HOLD_ON_LOOP`，且 `HOLD_ON_LOOP` 存在自环；这暴露出“每次从失败 artifact 增补稳定 option”的方法仍会让长尾事件反复打断训练。

词表生成器现直接读取 localization 中全部 `.options.*.title` 键，过滤描述和页面标题，并保持全局 append-only。新增 147 个事件 option 位于 2,304–2,450，原有 1–2,303 索引逐个不变，词表共 2,451 行。重建后对正式 run 的 1,304 artifacts / 99,938 decisions 全量审计仍为 0 collision、0 pointer miss、0 unknown、0 non-finite、0 violation。

同时修复恢复损失与 Dashboard 分支混淆：训练器每个成功 iteration 原子写 `resume.pt`，watchdog 在它比 milestone 更新时优先恢复，因此不再因十轮一存而丢掉 iteration 80–82；每次 resume 还写入带 cutoff 的 history marker，Dashboard 据此只构造当前 canonical 分支，并按 live/history/checkpoint 最近活跃时间默认选 run。相关测试加上 checkpoint 临时文件清理回归后，RL 102 + 根目录 29 = 131 passed。下一步从 checkpoint 79 再次进入 Act 1，现场验证 iteration 80 的逐迭代恢复点，然后连续观察至少 10 轮至新 milestone。

现场恢复验收通过：Dashboard 在 resume marker 后从废弃分支 iteration 87 正确回到 canonical 79；新的 iteration 80 完成后原子生成 `resume.pt`，watchdog helper 选择它而非旧 milestone。iteration 80–89 连续十轮共 820 episodes / 118,832 decisions，所有轮次均为 0 engine error、0 collision、0 pointer miss、0 unknown entity、0 non-finite、0 violation；原失败点 iteration 83 正常通过。新 `ckpt_00089.pt` 含 2,451 行 embedding，50-seed dev 胜率 26%、平均 15.3 层；训练继续到 iteration 90，`resume.pt` 也已推进到 step 90。

#### v7-clean plan 归档说明（`v7_clean_plan.md` 已并入本文件后删除）

`plan/v7_clean_plan.md` 的完成内容已由上述 v7-clean 各条吸收。补记其中两点，以免随文档删除而丢失：

- **触发 v7-clean 的旧 v7 审计缺口（含计数）**：704 artifact / 58,301 决策审计——shop 304 个 state / 3,952 商品**购买/删卡候选为 0**（只能 `leave_room`）；193 个多选 state 的 **7,919 个候选表示完全相同**（升级/删除/变换≈随机）；64 个 state / 128 个 bundle 全 `UNK`；约 118,628 个定向出牌候选只有裸 `target_index`（无目标敌人绑定）；敌人 power 未提取、intent 类型丢失、23,324 次 `damage_by_target` ≠ `stats.damage`、`context.boss` 与完整地图不可见、6,472 个 state 的 enchantment/affliction 按普通卡处理、`offered/consume_warnings` 未接入训练 loop。均为 v1–v5 同类结构盲区在 v7 大模型上的重现。
- **未做的延后实验 P6 — 序列 PPO**：v7-clean 基线稳定后再做 truncated BPTT（sequence length 32、burn-in 8、episode-boundary mask），不得与可见性修复混入同一 run 以保持因果可解释。**仍未开始**，作为后续独立实验保留于此。

v7-clean 的 P5 act1-gate 计划（连续三 gate ≥30% 才进 full A0）已被后续**直接进入多幕（`acts_12`）**的方向取代。

#### Approach A — zhiyue 快模拟器作为训练后端 · 已搁置为训练策略，保留为依赖

**2026-07-15 起。方案文档 `approach_a_zhiyue_sim_backend.md` 与结果文档 `zhiyue_phase0_results.md` 均已并入本条后删除（zhiyue sim 实验整体退役，见文末「退役」）。**

- **动机**：v7 act1 ~72% 天花板的一个真实根因是**采样饥饿**——真 STS2 CLI 仅 ~70 步/秒（12 workers）。`zhiyue/sts2-rl-agent` 的纯 Python headless 模拟器单核 ~146 战斗/秒、3,658 步/秒，且有整轮 `run_env`（地图/商店/事件/奖励）。目标：把它接成一个**环境后端**，把每个实验从「几十小时」压到「几十分钟」，保留我方全部领先（entity 模型 / 指针动作空间 / curriculum / reward）。
- **实施**：不 commit 它的源码（无正式 license，research-only）→ vendored 到 `vendor/sts2-rl-agent`，pin `1b7e7ce` + 本地 parity 修正（见 `vendor/sts2-rl-agent/UPSTREAM.md`，gitignored）。复用 `sim/combat-backend` 分支已有、且带真游戏 parity 测试的 `SimCombatBackend` 包 zhiyue `CombatState`，新增 `ZhiyueRunBackend` 包 `RunManager` 提供整轮能力；`--combat-backend {real,sim,zhiyue}` + `--vocab-source`。
- **结果**：**Phase 0 = GO**（端到端 env ~54x 吞吐、无保真告警、能学）；**Phase 1 = 完成**（整轮后端）。**Phase 2**：native vocab、on-policy boss replay、per-minibatch KL guard、96-episode Act 1 基线均落地，但 100-seed dev 最高仅 **18%，未突破 72%**——瓶颈已从采样/协议**收敛到 boss 信用分配**（不再是吞吐）。
- **决策**：**zhiyue sim 作为训练策略搁置，真引擎仍是正式训练主线**。后续多幕训练（`acts_12`、`from1499`、`hp-shaping`）均在真引擎上进行。
- **补记的 Phase 2 实测**：native vocab 发现、on-policy boss replay、per-minibatch KL guard、96-episode Act 1 基线;100-seed dev 峰值 18%;`SimCombatBackend`(战斗)+`ZhiyueRunBackend`(整轮)有真游戏 parity 抽查(vendored `1b7e7ce` + 本地数值修正)。

#### zhiyue sim 实验 · 整体退役（2026-07-23）

既然真引擎已是确定主线、多幕训练全在其上进行，zhiyue sim 这套休眠基础设施整体退役,`m2_train.py` 回归**纯 real 引擎**:

- **删除**:`rl/src/sts2rl/sim/`(SimCombatBackend / ZhiyueRunBackend / discover_sim_vocab)、`rl/tests/sim/`(parity harness)、`requirements-sim.txt`、`scripts/setup_sim.ps1`、`tools/transfer_eval.py`、本地 `vendor/`(gitignored)与 `.gitignore` 的 `vendor/` 行、`plan/{approach_a_zhiyue_sim_backend,zhiyue_phase0_results}.md`(内容已并入上方 Approach A 条）。
- **`m2_train.py` 改动**:去掉 `--combat-backend / --eval-backend / --vocab-source / --sim-vocab-rollouts` 四个参数、`make_combat_backend` 工厂、sim vocab 发现与 sim stage 守卫;后端固定为 `make_client()`,vocab 固定 `fixed`(persisted/checked-in)。real 训练路径本就不实例化 sim,行为不变。
- 退役在正式训练(`m2_v7_acts12_hpshape`)暂停期间完成,验证 py_compile / import / 全测试通过后恢复。

---

#### v7-clean → Act 1 晋级 + live bridge：让模型在真实游戏里打（2026-07-15→16）

**改的是协议健壮性 + 一个全新的「观看」通道** · ckpt_00409 在真游戏里自建牌组走到 Act 1 boss

- **NoopGuard 熔断器**（`735e4f1`）：某些引擎动作内部失败、异常被吞、返回**逐字节相同**的决策状态——典型是满槽 `buy_potion`（`RunSimulator.cs:1445`）。贪心策略会对同一「死动作」反复重选直到步数截断：iter 289+ 记到 25 次截断，dev seed 325 被锁在商店到 step 600，**同时污染训练样本和贪心评估结果**。`NoopGuard` 按决策状态记住哪些动作 inert（前后 hash 相同）并不再提供，把策略推到能推进的动作上（商店永远还有 `leave_room`）；训练与审计 rollout 共用。ckpt_00409 上验证：熔断触发 1 seed、0 步数锁死、act1 通关 77.5% 不受影响。
- **`tools/live_play.py` — 「观看模型打真游戏」里程碑**（`b396f1f` → `92094a8`）：一条 Python 桥，让训好的 checkpoint 通过 mod 的 HTTP API 在**真实（modded）Slay the Spire 2** 里打。spike 证实两个序列化器只有外观差异（mod 发裸实体名，headless 发 `TYPE.NAME`），所以桥只做一件定制事——把 live JSON 翻成 headless `raw` dict——**下游整套 RL 栈原封不动复用**（`legal_actions` / `normalize_state` / policy / NoopGuard）。先做到 combat-only，再进化为**整幕驱动**：map/combat/elite/boss/card_reward/event/rest/shop/hand_select/card_select/treasure 每种决策各自 live 验证。**ckpt_00409 自主打完一整个 Act 1、在自己搭的牌组上走到 Act 1 boss。**
- 附带：`4eef729` 上游加只读 `get_draw_pile`（sim differential harness 需要锁定开局手牌/中途抽牌），并把此前未推的 `e1a0688` 推到 fork remote，补上「pin 的 commit 不在 remote」的坑。

#### act2/3 硬化 · full_a0 · acts_12 多幕课程（2026-07-16→17）

**改的是引擎（act2/3）+ 课程 + 多幕奖励** · 从 act1-only 迈向整轮

- **act2/3 headless 硬化**（`b224e31`）：`Vector2I.One` stub、Amalgamator/CrystalSphere 的 UI 代码 event auto-skip、引擎 fault 带 stack trace。full_a0 此前 ~12% 到达 act2 的 episode 崩溃；ckpt_00429 全三幕探针 engine faults **7 → 0**。
- **act1 gate-audit 工具**（`a255a3e`）：在进 full_a0 前挑最稳的 checkpoint。450 个 held-out dev seed 上 ckpt_00339 / 409 / 429 = **65.3% / 72.2% / 73.8%** 通关，0 error/0 截断。
- **full_a0 四道拦路**（`209cbbb`，全修）：① 词表 2451 → **2605**（act2/3 新实体；`m2_build_vocab` 现从 events localization 静态收割所有 `EVENT_VAR.*`）；② **容忍性可见性契约**（部分身份运行时才合成——rolled-relic 的事件选项、召唤生物 `MONSTER.BATTLE_FRIEND_V2`——容忍稀疏运行时尾巴，只在洪泛时 abort）；③ 候选爆炸（`select_cards` 的 C(30,4)=27k 候选 → 34 GiB OOM，加 `SELECT_CARDS_CANDIDATE_BUDGET` 封顶）；④ dashboard 不再把被容忍的 unknown 尾巴误报为契约违规（`9fdbbb5`）。满槽 `buy_potion` 也在引擎侧根治（`44dd1fe`：满槽时不再提供该动作，与 NoopGuard 互补）。
- **acts_12（act1+2）课程阶段**（`2270d17` / `e02362b`）：act1 通关 ~74% 时直接跳整三幕给的是**平坦 0% 胜率目标**（act3 到达太稀、稀释 act2 信号）。在 act1 与 full_a0 之间插入 `max_act=2` 的 `acts_12`：episode 到 act2 boss 结束，act2 拿到密集信号、迭代更短、act1 继续 co-train。首迭代 act1+2 胜率 **7.3%**（vs 整轮 0%）。
- **多幕坍塌 → milestone 奖励**（`b862d8f` → `02a1617`）：acts_12 上策略坍塌（reached-boss **87%→24%**、avg_floor **16→10**、entropy **上升** 0.79→1.04 漂向随机）。根因：net-zero 楼层势能下「清 act1 boss」在多幕阶段挣不到可区分奖励——「到 floor 17」和「死在 floor 3」都记 −1，只有罕见的 act1+2 胜（~2%）付 +1。加 **`act_clear_bonus`**（每次幕序推进=**打赢**一个 act boss 直接给奖，不能重学「走到 boss 去死」的老 exploit）：0.4 只减缓坍塌 → 提到 **1.0**（匹配 act1 阶段清 act1 付的 +1）。同时 `entropy_coef` **0.01 → 0.003**（`0ae4fea`：稀疏多幕信号下 0.01 太强，acts_12 贪心峰值 18% @ iter 469 后又飘回 2–10%）。
- **累积楼层修复**（`22a7e35`）：引擎按幕编号（每幕 floor 从 1 重启、boss ~17），`final_floor` 封顶 ~17，dashboard 对死在 act1 boss 和清了 act2 都显示 17，**act2/3 进度不可见**。`EpisodeRecord` 改记 `final_act` + **累积** `final_floor`（act1 1-17 / act2 18-34 / act3 35+）；shaping 仍用原始 per-act floor。
- **这一轮的 run = `m2_v7_acts12`（iters 430–651）**，后被判定为**欠训 + 过调**的失败（详见下方 07-21 重启条）——不是奖励错。

#### v7 act1「72% 天花板」调参扫描 + v8 大模型（2026-07-18）— 诊断后被证伪

**改的是奖励模型 + 模型容量** · 得出「72% 是容量墙」的结论 — **两天后被推翻**

- 通宵调参扫描（`m2_v7_act1_dense_overfit`，从 v7 act1 线续，hidden 256/4/8，~4.2M 参数）得出：**act1 dev 通关率封在 ~0.68–0.72 均值（峰值 0.80）**，超参/奖励/课程都推不到用户的 85% 目标。试过：plain reward、dense HP-shaping（`hp_shaping_coef` 0.5 与 1.0）、boss_mix 0.3、entropy pulse——**只有 boss_mix 有效**（dev 均值 0.65→0.71、boss_conv 首破 0.90；机制：`boss_mix>0` 才把 boss episode 切进 PPO batch，gamma=0.999 会稀释整轮尾部 boss 战的信用）；dense HP 抬 conv 但 reached_boss 补偿性下降 → 净 dev 平；entropy pulse 连 entropy 都没抬起来。
- **当时的诊断（后证伪）**：签名 = 收敛在容量上（`policy_loss≈0`、`explained_variance≈0.95`、entropy 稳 ~0.67、KL 在 2–3/4 epoch early-stop、~200 迭代 6 配置全平）。**用户决策：放大模型。**
- **`m2_v8_h512`**（`v8-h512-bigmodel-run`）：从零重训，**hidden 512 / 6 layers / 8 heads（~20M 参数，5x）**，验收判据「若 act1 dev 破 72% 则容量即墙；若也封 72% 则容量不是墙」。**不稳定发现**：512 模型 20 迭代就从 combat 冲到 act1（组合阶段太易、跳过 boss_combat）→ **combat 地基太薄**；在 act1 里脆弱——entropy_coef 0.01 让 entropy 单调爬 0.88→1.0 后坍塌，0.003 不发散但 dev 从 0.28 漂到 0.10。开放假设：薄地基（5x 模型晋级太快）才是真瓶颈。

#### 🔴 大翻案：72% 不是天花板，是欠训——clean_init0 续训命中 85%（2026-07-19→21）

**没改代码，只是不去动它** · dev 0.68 → **0.848 → 0.896**，本项目 act1 最强模型

> **上面 07-18 的「72% 容量天花板」诊断是错的**，正如 v6 里「boss 墙从来不存在」。这条把它推翻。

- 直接 resume `m2_v7_clean_init0`（真引擎 act1，hidden 256/4，仍是仓库里单个最强模型）**按它原始配方**（entropy 0.01、无 act_clear_bonus）不受打扰再训 ~200 迭代：dev win **0.68 → 0.81 均值 / 0.90 峰值**，reached_boss 0.88→0.97，boss_conv 0.76→0.84。**机制**：iter 489 的 `policy_loss≈0 + entropy 0.70` *看着*收敛，其实是**慢补丁不是天花板**——entropy 继续退火 0.70→0.50，policy_loss 回到真实 −0.04..−0.06，KL early-stop 从 2 放松到 4 epoch，胜率随之涨。继续跑满预算：iters 700–999 dev 稳 ~0.85（**last-10 均值 0.848，峰值 0.90**），到 1500 iter 收官 **last-10 dev 0.896、峰值 0.96 @ ckpt_01469、boss_conv ~0.94**。归档精简快照到 `rl/runs/archive/m2_v7_clean_init0_1500iter_2026-07-21/`（~199M，含 resume.pt + ckpt_01469/1489/1499 + config/history/vocab/loadouts，不含 episodes）。
- **翻案的教训（本文件核心主题再现一次）**：`policy_loss≈0` 这个签名对「真到容量」和「暂时慢补丁」**读起来一模一样**。07-18 的扫描叫早了；而**之后每一个扰动实验（dense HP / v8 大模型 / 多幕 / sim 后端）都把这条 run 从高产的退火轨道上撞下来，然后被误读成「又一次确认了那堵墙」**。规则：**一条还有 entropy 可退火的 PPO plateau，别去调它——让它煮。**（配套修复：把 dashboard「continue training」按钮接上——`feat/zhiyue-sim-backend` 上 `rl_dashboard.py` 原是只读 GET 版，从 `v8-training-wip` port 了 `train_control.py` 控制后端；抬 `config.json` 的 `iterations`（1000→1500）即可从按钮延长已 finish 的 run。）

#### acts_12 从 1499 重启：真正进入多幕（2026-07-21）

**改的是 CLI（永久修 dataclass-default 漂移）+ 启动方式** · 真正把 baseline 拉到 act2

- clean_init0 跑满 1500 后，续进多幕为 **`m2_v7_acts12_from1499`**（resume `resume.pt` @ step 1499）。奖励 = `terminal ±1 + 0.2×per-act 楼层势能（terminal Φ=0）+ act_clear_bonus=1.0`，`entropy_coef=0.003`，其余同 clean_init0（lr 5e-5、hidden 256/4/8、mb 256、96 ep/iter、boss_mix 0.15、on-policy boss replay）。
- **为什么*旧* acts_12（`m2_v7_acts12`，iters 430–651）失败——不是奖励**：它在 iter 430、act1 只有 ~0.74 时就分叉（clean_init0 后来到 0.88+），只跑了 ~220 迭代（act1 从类似 plateau 需要 ~600），且中途被重调 3 次（act_clear_bonus 0.4→1.0、entropy 0.01→0.003、累积楼层遥测）——每次重启都把它撞下退火。**和被证伪的 act1 天花板同一个「欠训 + 过调」陷阱**：policy_loss≈0、entropy 反向漂 **上** 0.73→0.93（unlearn act1）、avg_floor 才刚爬 14→19。**奖励本身是对的。**
- **永久代码改动**（修一再咬人的 dataclass-default 漂移）：给 `tools/m2_train.py` 加 `--entropy-coef` / `--act-clear-bonus` CLI flag（None → 保持 PPOConfig 默认，单幕 act1 run 不受影响；传了就写进 config.json 可复现）。`act_clear_bonus` 也加进 `train_control.py` 的 `VALUE_OPTIONS`。**不再靠编辑 PPOConfig dataclass 默认值来切 act1↔多幕。**
- **首 2 迭代（健康）**：train_win ~0.10，reached act2 boss ~0.95，**act2 boss_conv ~0.11（墙晚了一幕重现）**，median_floor 23 / max 34–35（真在 act2、碰到 act3），reward_health 赢 +2.1 / 输 −0.3（未反转）。**WATCH**：entropy 0.586→0.681，若继续爬过 ~0.9 就是坍塌重演。下一杠杆：给 **act2 boss** 专门的 on-policy replay / bridge（目前 boss_combat 阶段只练 act1 boss）。

#### 引擎 bug：KAISER_CRAB_BOSS 免费通关（2026-07-21，已修）

**引擎侧** · 灌水 acts_12 的 `cleared_act2` / dev 胜率

- 用户在 dev 局 `01589_m2-a0-ironclad-36` 发现：选 act2 boss 房（HP 63/96）→ 下一步直接 card_reward（HP 不变、零战斗步）。隔离证实：**`KAISER_CRAB_BOSS` 遭遇在 headless 里刷 0 个怪** → 引擎当战斗已结束、发 boss 卡奖励、推进幕数 = **免打免掉血的白赢**。全 12 boss 扫描只有它坏；act2 有 3 个 boss，~1/3 的局 boss=KAISER_CRAB → 走到即免费通关，**教策略「走到 act2 boss 有时=白赢」**。
- **根因**（反编译定位）：Kaiser Crab = `Crusher`+`Rocket` 两怪，两者 `AfterAddedToRoom()` 访问的 `NKaiserCrabBossBackground Background` getter 在 headless（无视觉节点）必抛 `InvalidOperationException`，异常被 `TaskHelper.LogTaskExceptions` 吞掉 → 怪没加进战斗。**修复（引擎 2 处）**：`GodotStubs/UI.cs` 补 `CanvasItem.SetVisible/IsVisible`；`RunSimulator.cs` 加 `PatchKaiserCrab()` Harmony 补丁（反射扫描 → 前缀 Background getter 返回 dummy、no-op 所有 `Play*` 动画）。纯视觉、零 gameplay 改动。验证：KAISER_CRAB 现刷 CRUSHER(209)+ROCKET(199)、68 步真交火；12/12 boss 刷怪；3/3 完整 Ironclad 通关；rl pytest 133 passed。**修后 acts_12 `cleared_act2` 会掉到诚实值**（信号变干净不是退步）。详见 `known_gaps_backlog.md #0`。引擎 submodule 改动尚未 commit/pin。

#### dashboard 整合 + per-act 漏斗 + 满槽药水盲区确认（2026-07-21→22）

- **per-act boss 漏斗**（纯后端）：把 v8 的 `depth_profile(records, acts=...)` port 进本分支 `telemetry.py`（键 `reached_act{n}_rate` / `cleared_act{n}_rate`），`m2_train` 两处调用点传 `acts=stage.max_act`。部署的 v8 前端 bundle 一见到 per-act 键就点亮分幕漏斗（iter 1544 验证：reached_act1 0.91 / cleared_act1 0.60 / reached_act2 0.14 / cleared_act2 0.086）。
- **前端分支分裂解决**（`3fa8efa` / `744752b`）：能编译的 v8 `dashboard/src`（served dist 的来源）被搬到 `feat/zhiyue-sim-backend` 并提交，坏的 feat src 删除；**现在直接在 feat 上改前端**、`npm run build`、dist 原地服务（dist 仍 gitignored）。同 commit 两个 UX：boss 漏斗加 `reached_act{n}` 走到线；action-mix 图例变成点击筛选（14 种动作全可 toggle）。
- **`WinError 10053` 刷屏修复**：浏览器关掉轮询 tab 打断 socket → 写失败 → except 里又对死 socket 写错误再次抛 → 每个断连一整条 traceback。加 `_CLIENT_GONE` 元组吞掉并置 `close_connection`。**需重启 dashboard 生效**（用户手动跑 `rl_dashboard.py`）。
- **满槽药水盲区确认**（`known_gaps_backlog.md #1`）：30 场对照实测——战后金币/遗物/药水由引擎**自动领取（非决策）**，只有卡牌奖励是 agent 决策；药水槽满（3 瓶）时掉的新药水被**静默丢弃**，belt 从不涨到 4、**从不弹「换哪瓶」决策**（`RunSimulator.cs:2489` `DetectPostCombatState`）。是真实游戏 parity 偏差 + 药水换牌学习上限，与旧 `use_potion`-never-offered 同族；训练/评估同引擎内部一致，暂缓修、优先级低。

#### HP shaping 奖励实验（当前 `feat/hp-shaping`，2026-07-23）

**改的是 P3（奖励）· 在多幕 baseline 上做隔离 A/B** · 针对 act2 消耗战漏斗

- **健壮性先修**：`181652a` — 不干净关机留下的半写 `resume.pt`（zip 中央目录未落盘）让每次重启 `torch.load` 失败、`latest_checkpoint` 总重选它（mtime 最新）→ watchdog 崩溃循环、dashboard「continue」看着像死了。修：`latest_checkpoint` 新→旧遍历、跳过 zipfile 探针失败的候选；checkpoint `os.replace` 前 `fsync` 临时文件。
- **sim 后端提交为运行时依赖**（`db5e3c5`）：`rl/src/sts2rl/sim` 的 `SimCombatBackend` / `ZhiyueRunBackend` + native vocab 发现 + real-vs-sim parity harness 进仓（vendor 本体仍 gitignored）。**作为训练策略已在 Phase 0 搁置**，real 后端仍是 live 主线，但 `m2_train` 即使 real 也 import 它。多幕训练层（per-act 漏斗 / backend 接线 / dashboard 控制）落在 `3865204`，**保持成一个干净的 bug-fixed baseline 供 A/B**，HP-shaping 单独分层在 `feat/hp-shaping`。
- **potential-based HP shaping**（`b27d80e`，`--hp-shaping-coef`，默认 0）：`Φ(s)=coef·hp/max_hp`，`γ·Φ(s')−Φ(s)`，**terminal 强制 Φ=0**——与楼层 shaping 同款 policy-invariant 构造，把「碎步掉血」的信用变密而**不改最优解**（不会诱发平坦掉血惩罚会招来的「走到 boss 去死」）。**目标 = acts_12 消耗战漏斗：一半清了 act1 的局死在跨 act2 的路上，进入致命战时中位 HP 仅 41%**——一个稀疏 −1 在 γ=0.999 折扣数百步后几乎够不到的长程信号。test_ppo 验证默认关 + 折扣贡献 telescopes 到 `−coef·Φ(s0)`（路径无关）。
- **凹势能 + smith/remove 微推**（`dcaba74`）：修 acts_12 动作分布里两个**结构性零**坍塌。① HP 势能改**凹/饱和** `g(x)=1−(1−x)²`（原线性 `hp/max` 给 80%→100% 的浪费性休息治疗付平坦 +coef·0.2，会奖励并固化「永远 HEAL」——**~4500 次休息决策 100% HEAL、0 SMITH，中位 HP 80%**；凹性让低血回复付得陡、满血 topping off 几乎不付，健康时 SMITH 才有竞争力，受伤时消耗信号仍锐利）。② `--smith-bonus` / `--remove-bonus`：对休息点选 SMITH、商店选 remove_card（**offered 241× / taken 0×**）给小的直接（非势能）奖励，与凹势能叠加（受伤仍 HEAL 胜、健康 SMITH 胜）；remove 是无差别的（商店态看不到牌组、无法只针对起手牌），两者都保持小（~0.05）、默认关。

---

## 教训累加（07-18→21，与文首「返工分布」同一主题）

**「72% act1 天花板」是本项目第二个「自洽但错误」的故事，机制和 v6 的「boss 墙」如出一辙。** v6 那次是**一条欠训、被抢 GPU 的 run** 讲出了一个「策略在退化」的连贯假象；这次是**一条已进入慢补丁 plateau 的 run**（`policy_loss≈0`）被读成「到容量了」，然后接连三个方向（v8 大模型、zhiyue sim、多幕 acts_12）各自把它从退火轨道撞下来、各自「再次确认」了那堵不存在的墙。真相是：**什么都不改、让 clean_init0 按原配方多煮 200 迭代，就从 0.68 走到了 0.85+，跑满 1500 到 0.896。**

**给未来的两条规则（追加）：**
1. **`policy_loss≈0` 不能区分「到容量」和「暂时慢补丁」——只要 entropy 还在退火，就别下「到天花板」的结论，更别据此调参或换方向。** 让它跑，看 entropy 是否继续降、KL early-stop 是否从 2 放松到 4 epoch。
2. **每一个「实验」都会把一条正在高产退火的 run 撞下轨道；如果你在被撞下的数据上读趋势，它会给你一个和上一次实验方向一致的、自信的、错误的确认。** 想验容量/奖励假设，先复制出一条**不动的对照 run**，别在主线上原地扰动。

（v1–v5 打在 P2、真 bug 在 P3；v6「boss 墙不存在」；07-18「72% 容量墙不存在」——三次都是**把一个未经证伪的诊断当成已验证的事实**。）
