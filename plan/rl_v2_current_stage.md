# M2 Ironclad（进行中）

更新日期：2026-07-11

M0、M1 已完成（M1 验收记录见 git 历史 `8d6bf01` 时点的本文件；关键锚点：主仓库 `c22d4af`、CLI fork `7fe000619930a199ab1cfccdbde727a0b30613af`、1,000 局 A0 零错误、跨进程 resume 参数 hash 一致）。M2 于 2026-07-11 开始。

## M2 验收门槛（roadmap 权威定义）

按普通战斗、混合战斗、Act 1、完整 A0 curriculum 推进。最终 5 个独立初始化、隔离 1,000 test seeds：A0 平均通关率 ≥40%，95% bootstrap CI 下界超过启发式基线，非法动作 0、timeout <1%，完成 reward ablation（shaped vs terminal-only）和拆分报告。

## 阶段拆解

- **P0 基线与前置复核**：冻结 `m2-a0-ironclad` seed 命名空间的 SHA-256 split（1,000 test seeds 记录 seed_hash 后不参与任何选择）；development seeds 上测 random 与 heuristic 基线（通关率 + 95% bootstrap CI）；复核 M1 遗留的事件 card reward skip 客户端屏蔽并给出 M2 决策。
- **P1 curriculum reset 协议扩展**：原子指定 character/seed/牌组/遗物/HP/遭遇的单命令战斗 reset，引擎侧改动只进 `rl-v2-protocol-state-machine` fork 并固定 commit；Python `EngineClient` 对应接口；确定性与 `start_run` 互不污染的回归测试。
- **P2 表征与模型**：实体级编码（卡牌/敌人/遗物/药水 id 词表 + 数值字段，未知进 `UNK` 并告警）、phase embedding、实体 Transformer encoder、候选 pointer 与实体注意力融合、GRU 历史、value head。
- **P3 训练环路**：多 worker 并行采样（复用 M1 持久进程池）、Recurrent Masked PPO + GAE、reward = 终局 ±1 + `0.2 × potential-based` 楼层进度、entropy、AdamW、TensorBoard、周期性 development 评估、checkpoint/resume；保留 terminal-only ablation 开关。
- **P4 curriculum 训练**:普通战斗 → 混合战斗 → Act 1 → 完整 A0，每阶段以 development seeds 达标后推进，全部配置/seeds/checkpoint/逐 episode 结果落盘。
- **P5 最终验收**：5 初始化 × 1,000 test seeds，按 plan/README.md 记录全部实验元数据与报告。

## M1 遗留的 M2 前置项

- 事件内 card reward 不暴露 `skip_card_reward` 是客户端屏蔽而非根治（引擎 `can_skip` 仍为 `true`）；P0 需决定保留（记录为动作空间偏离）或引擎侧根治。
- 「任意指定牌组/敌人/遗物/HP/seed 的原子 curriculum reset」协议尚不存在（上游只有 `start_run`/`load_save`/`set_player`/`enter_room`/`set_draw_order`），P1 解决。
- `card_select` 多选组合、药水目标、商店移除选牌等动作覆盖需在训练前用真实状态样本再确认。

## 已完成（有仓库证据）

- [x] M2 阶段开启：roadmap 状态更新，本文件重写为 M2 阶段计划（2026-07-11）。
- [x] **P0 阻塞发现并修复：固定 commit `7fe0006` 从未以提交状态通过验证。** M2 首次基线全部 200 局 `ProtocolError`：`HeadlessPresentation.Install()` 给 `NDebugAudioManager.SetMasterAudioVolume/SetSfxAudioVolume` 打 Harmony 补丁时，方法体引用的 `AudioServer.Singleton.GetBusIndex/SetBusVolumeDb` 与 `Mathf.LinearToDb` 在 GodotStubs 中不存在，token 解析抛 `TypeLoadException`，**100% StartRun 失败**（与 seed 无关）。该补丁代码正是 `7fe0006` 引入且历史上任何 commit 都没有 AudioServer stub——M1 验收所用二进制（02:12 构建）早于最终源码状态，验证结论对提交状态不成立。修复：GodotStubs 增加 `AudioServer`/`AudioServerInstance`（签名对齐真实 GodotSharp）与 `Mathf.LinearToDb/DbToLinear`，fork commit `bd7c512f46c3ffd2af91d11d5c3af11bed14bf0f` 并更新 pin。回归证据：dispatcher 自测 PASS；`play_full_run.py` 五角色各 5 局全部 Completed 5/5；`p0_baseline_hash.py` 重新生成后 `git diff` 为空（状态 hash 锚点逐位一致，引擎语义未变）；fork pytest 55 passed / 2 failed（`test_save_load.py` 两个失败为既有 `load_save` 返回缺 `decision` 字段的协议缺陷，与本修复无关，作为 P1 输入记录）。教训：固定 submodule commit 前必须从干净源码重建并跑回归。
- [x] **P0 seed split 冻结**：`rl/seeds/m2_ironclad_seed_split.json`，命名空间 `m2-a0-ironclad-<0..9746>`，1,000 test seeds（hash `d4d636c2cb855eac…`）+ 500 development seeds（hash `abb4c775c48f27bb…`）；`tools/m2_baseline.py` 在冻结文件存在时校验可复现性，不一致直接失败。test seeds 在 P5 前不参与任何选择。
- [x] **P0 基线实测**（`tools/m2_baseline.py`，Ironclad A0，前 200 个 development seeds，6 workers，timeout 10s）：random 0/200 通关、0 错误、0 timeout、0 非终止、54.3s；heuristic（`sts2rl.policy.heuristic_action` 固定优先级）0/200 通关、0 错误、0 timeout、108.5s。两者 95% bootstrap CI 均为 [0, 0]。结论：启发式基线在完整 A0 上通关率为 0，M2 验收实际由 ≥40% 门槛主导；"CI 下界超过启发式"等价于下界 > 0。逐局结果在本地 `rl/runs/m2_baseline_{random,heuristic}.json`。
- [x] **P0 skip 屏蔽复核决定：M2 保留客户端屏蔽**。理由：(1) 引擎侧根治需让 `SkipReward()` 真正推进事件状态机，涉及 M1 刚稳定的 selector bridge，回归风险与收益不成比例；(2) 屏蔽位于协议 adapter（`protocol.py::legal_actions`），训练、启发式基线与未来两个 backend 使用同一动作空间，对比公平且无 M6 parity 漂移；(3) 行为代价有界（事件卡奖励必须选一张，普通战斗奖励不受影响，`test_combat_card_reward_remains_skippable` 双向守护）。复核触发点：M6 parity 验证时，或训练中观察到事件强制选卡显著劣化策略。
- [x] **P2 起步：实体级表征与 Transformer 模型初版**。新增 `sts2rl.entities`（每类实体 id 词表 + UNK=0 + 告警队列、`phase_id`、`encode_entity_batch` padding/mask/零实体行守护）与 `sts2rl.model.EntityTransformerPolicy`（type/id/numeric 实体 token、phase embedding、TransformerEncoder key-padding mask、masked mean pool、候选 pointer + value head）。`rl/tests/test_entities.py` 8 个测试：词表 UNK/告警/JSON roundtrip、padding 零泄漏、确定性、候选 mask、固定 batch 过拟合（loss 降至 <20%）。全量 pytest 33 passed。GRU 历史变体与真实词表构建（需大规模轨迹采样）归入 P2/P3 后续。

- [x] **P1 curriculum reset 协议扩展**（fork commit `91c91a8ad766720ce32ff91461d28ecd7e743985`，pin 已更新）。新命令：`start_combat`（原子 start_run → set_player 覆盖 hp/max_hp/gold/deck/relics/potions → enter_room 指定 encounter，任一步失败 fail-closed 直接返回错误）与 `list_models`（枚举 encounter 含 act/类别、card 含 type/rarity、monster/relic/potion/event/power/character 规范 id）。Python 侧 `CombatConfig` + `EngineClient.reset_combat/list_models`。修复三个引擎缺陷：(1) `SetPlayer` relics 直接写 backing list 导致下一场战斗 `RunState.IterateHookListeners` NRE——改走 `Add/RemoveRelicInternal`；未知 deck/relic/potion id 由静默跳过改为 fail-closed；(2) headless 从不运行 Godot 驱动的 `ModManager.Initialize`，`AllPowers` 永远抛错——初始化时反射置 `Skipped`（引擎自身的无 mod 终态）；(3) **跨 episode 污染**：胜利后不领 card_reward 直接 reset，遗留 `_pendingRewards` 使下一局 `start_combat` 直接打开上一局的奖励界面（训练数据被污染成秒胜）——`CleanUp` 现在废弃全部 pending 状态（不 complete，避免向被拆除的 run 执行延续）。验证：跨进程/同进程同 seed 状态 hash 一致、未知 encounter/relic fail-closed、start_combat 后 start_run 干净、污染回归测试；fork pytest 66 passed（新增 11 个 curriculum 测试），dispatcher 自测 PASS，五角色 5×5 全部 Completed。
- [x] **P2 实体表征完成**：状态序列化补充 enemy/relic/potion/power 的稳定 ModelId（`MONSTER.X`/`RELIC.X`/…），词表不再依赖本地化名字；`tools/m2_build_vocab.py` 从 `list_models` 目录构建 `rl/schema/m2_vocab.json`（1,316 词条：578 card、102 enemy、297 relic、65 potion、273 power），真实 `start_combat` 状态 10/10 实体命中、零 UNK 告警。observation 升级：12 维全局特征（新增 block/deck_size/draw_pile_count/discard_pile_count）、从 `player` 嵌套提取 relics/potions（此前从未被提取）、`player_powers` 实体、known 字段对齐真实 schema。`EntityRecurrentPolicy` = 实体 Transformer + GRUCell 历史（更新时 hidden 作为数据回放，不做 BPTT）。**状态 hash 锚点因加字段而有意变更**：`p0_baseline_hash.json` 重新生成，连续两次重生成 md5 一致（969c95b3…）。
- [x] **P3 PPO 训练环路初版**：`sts2rl/curriculum.py`（四阶段梯子 normal_combat→mixed_combat→act1→full_a0，encounter 按 seed 确定性采样）、`sts2rl/agent.py`（单决策推理包装）、`sts2rl/ppo.py`（run_episode 战斗/整局两种终止语义、GAE（截断 episode bootstrap 末值）、advantage 归一化、entropy、梯度裁剪）、`tools/m2_train.py`（多 worker 持久引擎并行采样、train split 种子流、dev seeds 阶段晋级门槛 normal 0.90/mixed 0.70/act1 0.30、TensorBoard、checkpoint/resume、episode 错误率 >5% fail-closed 退出）、`tools/m2_final_eval.py`（P5 验收：5 checkpoint × 1,000 test seeds、跨初始化 bootstrap CI、门槛检查）。整局 reward = 终局 ±1 + `0.2×(γ·floor′−floor)` shaping，`--terminal-only` 为 ablation。真实引擎 smoke：3 迭代 × 16 局 6 workers，train 胜率 0.44–0.56、~23 步/局、0 错误，dev 贪心 16/16；PPO 单测证明 on-policy 单轮更新提升有利动作概率。RL pytest 44 passed。

## P4 训练记录（进行中）

- init-seed 0 第一次训练（run `m2_init0`）：normal_combat 第 9 迭代 dev 50/50 晋级；mixed_combat 第 19 迭代 dev 0.96 晋级；act1（整局模式，act>1 即胜）到第 59 迭代 dev 仍为 0。诊断（checkpoint 贪心探测 24 个 dev seeds）：多数死亡在 6–9 层的**普通小怪战**（同类战斗在 mixed 阶段以 96% 通过），根因是战斗课程永远满血开局，策略从未学到 HP 是跨战斗资源（不格挡保血、不休息）。
- **课程修正（课程内部设计，roadmap reward 规范不变）**：战斗阶段开局 HP 改为按 seed 确定性采样 25–80（`sample_starting_hp`，守护测试）；训练/评估日志新增 `avg_floor` 遥测。从 `ckpt_00059` 恢复为 run `m2_init0b`，重刷战斗阶段后回到 act1。随机 HP 下 normal_combat 起步胜率 0.65–0.75（改动生效）。
- **晋级门槛校准**：随机 HP 下 init0 的 normal_combat 贪心 dev 连续三次评估恒定 0.82（同样 9/50 低血难局每次都输，属实际不可赢局面），0.90 门槛是满血时代设定、语义已失效。门槛调整为 normal 0.80 / mixed 0.60（act1 0.30 不变），从 `m2_init0b/ckpt_00089` 恢复为 run `m2_init0c`。
- **候选-实体指针对齐（v3，roadmap"动态候选 pointer"的正确实现）**：v2 在 act1 依旧 ~10 层持平后定位到第二个结构盲点——`select_card_reward`/`play_card`/`use_potion` 等候选只带索引标量，pointer 无法直接"看到"所指实体（卡奖励选择≈随机 → 牌组不成长 → 中段墙）。新增 `candidate_entity_slots`（按 action 类型把候选映射到实体行：card/potion/relic/option/bundle 按 index 匹配，地图节点按 col/row 匹配），pointer head gather 所指实体的 Transformer 输出加进候选表示。守护测试：候选对齐正确、gather 改变 logits。50 个 RL 测试通过。训练重启为 `m2_v3_init0`。
- **表征盲点修复（v2 表征，checkpoint 不兼容，训练从头重启为 `m2_v2_init0`）**：发现 `select_map_node` 候选只带 col/row 而候选特征没有对应槽位——所有地图候选编码**完全相同**，选路纯盲选；地图 choice 实体也没有可嵌入的键（Rest 与 Monster 不可分）。修复：候选特征加 col/row 槽 + potion_index/relic_index 并入索引槽（`CANDIDATE_FEATURE_DIM` 16→18）；实体数值特征加 col/row（11→13）；`entity_key` 回退链加 `type`，词表新增 7 种房间类型（Boss/Elite/Monster/RestSite/Shop/Treasure/Unknown，从 `get_map` 真实地图采集），词表 1,323 项。守护测试：地图候选可分、药水候选可分、choice 实体嵌入房型。48 个 RL 测试通过。

## 下一步

- P4：init0b 通过 act1 后进入 full_a0；随后 4 个独立初始化 + terminal-only ablation。
- P5：`tools/m2_final_eval.py` 跑 1,000 test seeds 验收报告。
