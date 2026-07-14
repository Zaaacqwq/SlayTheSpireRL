# M2 Ironclad（进行中）

更新日期：2026-07-14

M0、M1 已完成。**完整历史（M / P / v 三级，含 M0、M1 的验收证据）见 [`timeline.md`](timeline.md)。** M1 的原始文档见 `git show bbc48db:plan/rl_v2_current_stage.md`（**此前引用的 `8d6bf01` 在本分支历史中不存在，是坏引用**）。关键锚点：主仓库 `c22d4af`、CLI fork `7fe0006`、1,000 局 A0 零错误、跨进程 resume 参数 hash 一致。M2 于 2026-07-11 开始。

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
- **事件语义可见（v4）**：v3 act1 出现**首批真实胜利**（第 29 迭代 dev 3/50，avg_floor 11.26→12.52 爬升，v1/v2 恒为 0），但 50 迭代内未起飞。第三个盲点：事件与选项对策略不可见（选项只有本地化文本，事件本体无标识）。fork `d90d46fdb176e517464a55330c4d286283153acc`（pin 已更新）：`event_choice` 增加稳定 `event_id`（ModelId 形式），`list_models(event)` 改为全形式并纳入 ancients；选项本就带稳定 `text_key`（rest 选项带 C# 类型名），`entity_key` 回退链加入 `text_key`/`title`；observation 合成 event 伪实体（ENTITY_KINDS + "event"）；词表 sweep 采集 47 个选项键 + 65 个事件，共 1,435 项。PPO 学习性测试改为对照式（正/负优势动作），50 个 RL 测试通过；fork 66 通过、抽样回归 5/5、dispatcher PASS;anchor 重生成两次 md5 一致（4941cca8…）。训练重启为 `m2_v4_init0`。
- **候选-实体指针对齐（v3，roadmap"动态候选 pointer"的正确实现）**：v2 在 act1 依旧 ~10 层持平后定位到第二个结构盲点——`select_card_reward`/`play_card`/`use_potion` 等候选只带索引标量，pointer 无法直接"看到"所指实体（卡奖励选择≈随机 → 牌组不成长 → 中段墙）。新增 `candidate_entity_slots`（按 action 类型把候选映射到实体行：card/potion/relic/option/bundle 按 index 匹配，地图节点按 col/row 匹配），pointer head gather 所指实体的 Transformer 输出加进候选表示。守护测试：候选对齐正确、gather 改变 logits。50 个 RL 测试通过。训练重启为 `m2_v3_init0`。
- **表征盲点修复（v2 表征，checkpoint 不兼容，训练从头重启为 `m2_v2_init0`）**：发现 `select_map_node` 候选只带 col/row 而候选特征没有对应槽位——所有地图候选编码**完全相同**，选路纯盲选；地图 choice 实体也没有可嵌入的键（Rest 与 Monster 不可分）。修复：候选特征加 col/row 槽 + potion_index/relic_index 并入索引槽（`CANDIDATE_FEATURE_DIM` 16→18）；实体数值特征加 col/row（11→13）；`entity_key` 回退链加 `type`，词表新增 7 种房间类型（Boss/Elite/Monster/RestSite/Shop/Treasure/Unknown，从 `get_map` 真实地图采集），词表 1,323 项。守护测试：地图候选可分、药水候选可分、choice 实体嵌入房型。48 个 RL 测试通过。

- **P5 验收管线已验证可用（2026-07-11）**：`tools/m2_final_eval.py` 增加 `--dev-seeds` 开关（仅用于管线验证，绝不用于正式验收），用 `m2_v4_init0/ckpt_00069` 在 24 个 development seeds 上端到端跑通：checkpoint 贪心整局评估、启发式对照、跨初始化 bootstrap CI、四项门槛检查，0 错误 0 超时。正式验收命令：`tools/m2_final_eval.py <5 个 ckpt> --seeds 1000`。

- boss_combat 门槛校准：dev 四次评估 30–34% 平台（部分"14 张无升级牌组 × boss"组合不可赢），0.40 → 0.30 放行；boss 战技能已从起步 17% 提升到 ~32%。
- **boss 桥接阶段（课程内部设计）**：v4 act1 dev 爬到 8%（4/50，第 99 迭代，历史最佳），且 loadout 收割显示贪心策略**中位到达 16 层（boss 门口）后死亡**——瓶颈精确定位为 boss/精英终结战。`tools/m2_harvest_loadouts.py` 用当前 checkpoint 收割 56 个真实中期 loadout（牌组 14–22 张、含遗物/药水/血量；`start_combat` 重建会丢失升级状态，为已接受的近似）；新增 `boss_combat` 阶段（Act1 精英+boss 遭遇 × 收割 loadout，晋级门槛 dev 0.40），插在 mixed_combat 与 act1 之间，仅当 loadout 文件存在时启用。战斗 episode 成本约为整局的 1/10。51 个 RL 测试通过。从 `ckpt_00099` 恢复继续（模型形状不变，无需重头训练）。

- **boss-only 桥接 + 升级保留（fork `2f51640f11cbc9b55d54767131529bc7e56a991c`，pin 已更新）**：诊断后续——桥接 32% 由精英战主导，且收割牌组丢失升级。`SetPlayer` deck 支持 `BASH+` 语法（逐级 `UpgradeInternal/FinalizeUpgradeInternal`，未知卡 fail-closed，fork 13 测试）；收割器保留升级（发现仅 4% 卡牌有升级——策略几乎不 SMITH，本身是 boss 弱的根源之一）；桥接阶段改为 boss-only（40 个 loadout，中位 16 层）。51 RL 测试通过，从 `ckpt_00379` 恢复。
- boss-only 桥接实测：纯 boss dev 12–14%（确证旧 32% 由精英主导）；专项训练 40 迭代后 12→22→30%，第 449 迭代晋级回 act1。
- **桥接→整局转化缺口（待解）**：boss 专项 30% × 到 boss 率 ~50% 预期 act1 ~15%，实测 0–6%。已排除到达质量与 GRU 错配；剩余候选：药水持有差异、bridge 起始相位分布、boss 采样分布、评估方差。这是下一轮迭代的第一课题；当前最优策略是继续积累训练量（act1 长跑本身同时训练 boss 战）。
- **act1 瓶颈的决定性诊断（2026-07-12）**：桥接后 act1 dev 一度 12%（历史最佳），随后策略学会稳定走到 boss 门口（avg_floor 12→14.5，50% 的局到 16 层），但 boss 战近乎全败。排除了两个假设：(a) 到达质量——boss 门口 loadout 中位 68.5 血、携 ~3 药水（36 个 ≥15 层快照）；(b) GRU 隐状态错配——boss 房重置隐状态的 A/B 实验 0/20 vs 0/20 无差异。结论：桥接阶段 32% 主要由精英战贡献，**Act 1 boss 战本身是当前模型/训练量的硬墙**。后续方向（按杠杆排序）：boss-only 专项阶段（收割 loadout 保留升级需引擎扩展 `start_combat` 支持升级卡）、更长训练、更大模型。
- act1 训练量至第 369 迭代约 1.2 万整局 episode，错误率恒为 0，训练基础设施在长跑下稳定。

- **哨兵数值炸毁梯度：`boss_combat` 一进就 NaN（2026-07-13，v5 首次 GPU 长跑发现并修复）**。现象：v5 在第 49 迭代晋级 `boss_combat`，第 50 迭代 `loss/policy_loss/value_loss/entropy` **全部 NaN**（该迭代 `errors: 0`、48 局正常采样），下一迭代推理时 `torch.multinomial` 触发 CUDA device-side assert（`input[0] != 0`），采样线程死亡、主循环挂死。
  - **根因**：Act 1 boss **Waterfall Giant 的 HP/max_hp 由引擎报为 999,999,999**（"不可击杀、靠机制解"的哨兵值）。`entities._entity_numeric` 把 hp 直接除以 `_HP_SCALE=100` 且不设界，于是单个实体向 Transformer 注入 **~1e7 量级**的数值特征（其余特征都是 0–1）。**前向完全正常**（encoder 的 LayerNorm 把巨值重新归一化，loss 看着健康 = 0.11），但注意力打分饱和后 **softmax 的反向梯度溢出为 inf → inf−inf → NaN**，NaN 梯度精确地只出现在实体输入侧（`numeric` / `type_embed` / `id_embed` / `self_attn.in_proj`），指针与 value head 干净；Adam 一步就把权重写成 NaN。normal/mixed 阶段永远不刷 boss，所以前 49 迭代毫无征兆。
  - **定位证据**：从 `ckpt_00049` 忠实重放训练第 50 迭代（同 48 个 train seed、同 checkpoint、CUDA + BatchedAgent），复现 `loss_finite=True` 但 `bad_grads=True`；二分到最小触发集，落在 `MONSTER.WATERFALL_GIANT hp=999999999` 的战斗步上。排除的假设：候选/实体全屏蔽行（每行至少 1 个有效项）、slot 越界（max 7 < width 42）、ratio 爆炸（max≈1.0）、TransformerEncoder nested-tensor 快路径、padding mask 本身。
  - **修复**：`_entity_numeric` 对全部 13 个字段做对称截断 `_FEATURE_LIMIT=10.0`（并把 NaN 归零）。真实内容全部远低于该界（55 血小怪 = 0.55，act3 boss 量级 ≈ 8），**对正常状态是恒等变换**，只中和哨兵值；模型仍能靠 `id_embed` 区分该 boss。张量形状不变，旧 checkpoint 仍可加载。
  - **验证**：同一批第 50 迭代数据在修复后 6 个 minibatch 梯度**全部有限**（修复前 minibatch @0 即 NaN），因果确凿。新增两个守护测试（`test_entity_numeric_bounds_engine_sentinels`、`test_encode_entity_batch_bounds_the_tensor_fed_to_the_model`），均已确认**去掉截断即失败**（断言写死字面量 10.0，避免调大常数就能蒙混过关）。61 个 RL 测试通过。注意：**没有**为「梯度有限」写单测——随机初始化的小模型喂哨兵值并不会炸（需要训练后的权重 + batch 组合），硬写会得到一个"因为错误的原因而通过"的空测试；梯度层面的因果证据以上述真实数据重放为准。
  - **教训**：引擎数值字段进模型前必须有界。前向健康 + LayerNorm 会完整掩盖这类问题，只有反向才暴露；`errors: 0` 的干净迭代日志也不能证明数据无毒。

- **GPU/批量推理支持（为 9800X3D+5080 目标机迁移准备）**：`tools/m2_train.py --device cuda|mps`；新增 `sts2rl.batch_inference.BatchedAgent`（推理服务线程把并发 worker 的决策合批成单次 forward,`act` 签名与 `PolicyAgent` 一致），accelerator 上自动启用；PPO 更新 minibatch 搬 device。等价性测试:贪心决策/logp/value/hidden 与单条推理一致、并发异构请求正确、隐状态传递一致（54 RL 测试通过）。本机 MPS 端到端冒烟 0 错误。

## PC（9800X3D + RTX 5080）迁移：已完成（2026-07-13）

跨平台验证两项全过，**引擎语义无漂移**：`pytest rl/tests` 61 passed；`tools/p0_baseline_hash.py` 重生成后 `git diff` 为空——Windows/x64 的状态 hash 与 macOS/arm64 锚点逐位一致。环境：.NET 9.0.315、Python 3.10.11、torch 2.11.0+cu128（RTX 5080 = sm_120，必须 cu128 轮子）、12 workers。

落地时踩到的四个坑，复制到新机器时直接看这里：

1. **submodule pin 的 fork commit 从未 push**（最严重）。主仓库四次 bump 的 pin（`bd7c512`/`91c91a8`/`d90d46f`/`2f51640`）在 fork remote 上一个都不存在，`git submodule update --init` 直接 `upload-pack: not our ref`；远端 `rl-v2-protocol-state-machine` 分支头还停在 `7fe0006`——正是本文件 P0 记录的 100% StartRun 失败版本（在 Windows 上原样复现了 `TypeLoadException: Godot.AudioServer`，反证构建链本身正确）。由 Mac 侧补 push 解决。**制度补充：固定 submodule commit 前，除了「从干净源码重建并跑回归」，还必须确认该 commit 在远端可达。** 遗留：Mac 推的分支名拼错为 `rl-v2-protacal-state-machine`，而 `.gitmodules` 追踪的 `rl-v2-protocol-state-machine` 仍指向坏的 `7fe0006`，需要修正后重推、删除拼错分支。
2. Windows 游戏数据目录是 `<Steam>/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64`（`STS2_GAME_DIR` 指这里，对应 Mac 的 `data_sts2_macos_arm64`）。上游 `setup.sh` 的 MINGW 分支硬编码 `C:/Program Files (x86)/Steam/...` 且不含 data 子目录，须显式传 GAME_DIR。
3. `pytest rl/tests` 要全绿必须装 `export` extra：`test_m1.py` 的 parquet 测试无条件 `import pyarrow`，而 `rl[dev,train]` 不含它。安装用 `pip install -e "rl[dev,train,export]"`。
4. Windows 的 multiprocessing 是 `spawn`（macOS/Linux 为 `fork`）。已用 2 迭代 smoke 验证 12-worker + CUDA 采样路径无误后才开长跑。

训练：`tools/m2_train.py --device cuda --workers 12`（checkpoint 可从 Mac 直接带过去续训）。

## P4 当前状态与延续方式

v4 已消除三个结构性表征盲点（选路、指针对齐、事件语义），act1 有真实但零星的胜利（dev 2%，avg_floor ~11 爬升）。剩余瓶颈是**训练量**：目前 act1 仅 ~2,400 局，此类整局 RL 通常需要数万至数十万局。`m2_v4_init0` 按 900 迭代预算继续；后续操作（人不在场时可直接执行）：

1. init0 完成后：`tools/m2_train.py --run-name m2_v4_init<k> --init-seed <k>`（k=1..4）依次训练。
2. ablation：`--terminal-only --run-name m2_v4_ablation --init-seed 0`。
3. 验收：每个 run 取 dev 最优 checkpoint，`tools/m2_final_eval.py <5 ckpts> --seeds 1000`（自动含启发式对照与门槛判定）。

## ⚡ 吞吐 2.2×：Windows 的 15.6ms 调度节拍，同时拖慢引擎和我们自己的 Python（2026-07-14）

**Windows 只在调度器节拍上唤醒睡眠线程，默认 64Hz = 15.625ms。任何短于该值的等待一律被舍入到 15.6ms。** 实测：`Event.wait(0.5ms)` → 15.17ms（30×）、`Event.wait(2ms)` → 15.50ms（7.7×）、`Sleep(2ms)` → 15.50ms；只有 `perf_counter` 自旋是准的（2.00ms）。

**三处中招，两处在关键路径上：**

| 位置 | 代码 | 意图 | 实际 |
|---|---|---|---|
| 我们的 Python | `BatchedAgent` 攒批 `Event.wait(2ms)` | 2 ms | **15.5 ms / 决策** |
| **引擎 dispatcher** | `SingleThreadDispatcher.RunOne(1)` → `WaitOne(1)` | 1 ms | **15.6 ms × 2 / 决策** |
| 引擎其他 | `Thread.Sleep(5)` ×3、`Sleep(10)` ×7 | 5–10 ms | 15.6 ms |

**定位过程中我犯了一个方法错误并纠正**：先用 CPU 占用推断「引擎只用 0.02 核，所以引擎不是瓶颈」——**错的。CPU 低 ≠ 耗时短**，引擎在 sleep，不烧 CPU 但照样占墙钟。改用**墙钟时间**直接测：引擎往返 58.4ms（73%），我们的 Python 19.2ms（27%）。

**修复：**
- **Python（方案「不等」）**：`BatchedAgent` 的攒批窗口**完全移除**——队列里已有的请求就是天然的批。那 2ms 窗口本是为省下批量推理的 **0.4ms**，实际每次决策付出 **15.5ms**，是 **38 倍的净亏损**。`wait_ms` 默认改为 0；若确需窗口，改用 `perf_counter` 自旋（精确到 0.01ms）。守护测试 `test_the_server_does_not_sleep_between_batches` 直接断言服务线程不做任何 sub-tick 睡眠。
- **引擎（方案 `timeBeginPeriod(1)`）**：fork commit `fa56e7d`（pin 已更新）。**Windows 10 (2004) 之后该请求是每进程的**——父进程调用救不了 fork 出的 12 个 dotnet 子进程，**引擎必须自己调**。这正是实测「父进程修好了（15.5→2.16ms），引擎纹丝不动（58.3→56.1ms）」的原因。

**效果（真实训练，12 workers，act1）：**

| | 修复前 | 修复后 |
|---|---|---|
| 引擎往返中位 | 58.4 ms | **24.2 ms** |
| `agent.act()` 中位 | 19.2 ms | **4.7 ms** |
| **训练吞吐** | **78–80 steps/s** | **172–174 steps/s（2.2×）** |

**语义零影响**：`p0_baseline_hash.py` 重新生成后 `git diff` 为空——状态 hash 锚点逐位一致。

**教训**：M1 的 timeline 里就记着完全相同的根因（`Thread.Sleep(1)` 被舍入到 15.6ms，把 1 秒预算变成 31 秒，导致 nested card-select 超时）。**当时修好了那一处，但没有人回头问：整个系统里还有多少个同样的地方。** 结果它在 dispatcher 里、在我们自己的 Python 里，又活了半年。**踩过的坑要全局搜一遍，不能只补脚下那一个。**

## 🔴 两条 v6 run 并行、抢 GPU，导致我几乎改掉一个正确的奖励函数（2026-07-14）

**事故经过**：`rl/runs/` 下同时存在两条从同一个 `ckpt_00049` 恢复的 act1 训练：`m2_v6`（我的）与 **`m2_v6_resume2`（另一个会话或手动命令起的，我并未创建）**。二者配置完全相同，仅 run 名不同，**并行运行并争抢同一块 GPU**——凌晨的 CUDA `unknown error` 与性能异常很可能即源于此。

我只监控了自己那条。它因抢占而严重欠训（63 个 act1 迭代 vs resume2 的 343 个），数据表现为：`train_win_rate` 0.002→0.002、`avg_floor` 9.71→**8.07（崩塌）**、dev 最高 0.10。

**我据此得出「奖励修复抹掉了深度信号、策略在退化」的结论，并已准备重写 shaping。** 而真实的 run（`m2_v6_resume2`，343 迭代 / 14,063 局）显示**完全相反**：

| | v5 | **v6（真实 run）** |
|---|---|---|
| act1 dev 最佳 | 0.14 | **0.22** |
| boss 转化率 | **0/31 = 0%** | **~19%（稳定，34 次评估）** |
| act1 `train_win_rate` | 0.004 → 0.002（**跌**）| **0.002 → 0.039（涨 20 倍）** |
| act1 `avg_floor` | —— | **10.07 → 11.38（涨）** |

**奖励修复是对的。** 我险些基于一条被 GPU 抢占污染的数据，去改掉一个刚被证明有效的奖励函数。

**教训**：
1. **训练前必须确认没有其他 trainer 在跑**（`Get-CimInstance Win32_Process | Where CommandLine -match m2_train`）。多条 run 争抢 GPU 不会污染数据，但会**制造出「策略在退化」的假象**。
2. **run 命名必须唯一且可追溯**；`rl/runs/` 下出现来历不明的 run 目录时，先查 `config.json` 的 `resume`/`run_name` 再下任何结论。
3. **在一条欠训的 run 上读趋势，比读噪声更危险**——它会给出一个看似连贯、方向明确、但完全错误的故事。

## 课程数据分离（dashboard，2026-07-14）

各课程阶段**不是可比的量**：战斗阶段是在第 1 层生成的单场战斗，`avg_floor` **恒为 1.00**（实测 40 个点全是 1.00），无 boss 漏斗，胜率 ~0.8；而整局阶段 `avg_floor` ~11、胜率 ~0.1。**混在一条曲线里，40 个贴地的点会把 act1 唯一有信号的曲线压平。**

修复：`useStageFilter` + `StagePicker`——所有图表按课程阶段过滤，默认跟随当前训练阶段；楼层曲线与 boss 漏斗在战斗阶段**直接隐藏并说明原因**，而不是画一条恒为 1.0 的假线。

## 健壮性：推理线程死亡不再挂死训练（2026-07-14）

**一天之内发生两次**：(1) 哨兵 HP 把权重写成 NaN → `multinomial` 触发 CUDA device-side assert；(2) 系统重启时更新了显卡驱动（610.47 → 610.74），运行中的 CUDA context 失效 → `cudaErrorUnknown`。两次都导致 `BatchedAgent` 的推理服务线程死亡。

**旧行为**：`act()` 里 `request.done.wait()` **无超时、无存活检查**。线程死时只唤醒「正在处理的那一批」，**排在其后的 11 个 worker 永久阻塞**——训练器挂死、12 个引擎进程空转，只能靠人发现。

**修复**：`BatchedAgent` 记录失败（`_failure`），线程退出时唤醒全部在飞与排队的请求；`act()` 带超时轮询并检查线程存活，死亡时抛 `InferenceServerError`。episode 因此正常计入错误率，训练器按既有的 >5% fail-closed 逻辑**主动退出**，而不是无声挂起。守护测试 `test_a_dead_inference_server_fails_workers_instead_of_hanging_them`（旧实现下会挂死超时）。

## 训练期自动诊断（`sts2rl.telemetry`，2026-07-14）

今天的两个 bug 之所以能从 M1 活到 v5，是因为**没有任何东西在看它们**。这三项诊断从已有的 episode 记录里就能算出来，成本近乎为零，现在每个迭代都算并落盘：

- **`reward_health`：赢的回报必须高于输**——这是整个奖励函数最基本的不变量，而它被违反了整个项目周期。`inverted: true` 时向 stderr 打印醒目告警（"The policy is being taught to lose"）。
- **`action_mix`：各类动作的实际占比**——某一类动作恒为 0，意味着策略**根本够不到它**（而不是"不喜欢它"）。配套 `offered_actions()` 区分「从没被提供」（bug）与「提供了但不选」（偏好）。**药水 bug 会在第一个迭代就暴露。**
- **`depth_profile`：到达 boss 率 / boss 转化率 / 楼层中位数**——`avg_floor` 单一指标掩盖了 v5 的真实形态（"62% 走到 boss 门口，0% 赢下"）。

同时修复：**dev 指标此前只打到 stdout、从不写入 `history.jsonl`**，导致任何有 history 文件的 run 在 dashboard 上「验证胜率」序列全空。现在 dev 行也走 `history_writer`，并对整局阶段附带 depth 剖面。

v6 首个 act1 smoke 的实测输出已显示 `use_potion: 0.023`（今天之前结构上恒为 0）与 `loss_return: -1.13 / inverted: false`。

## 🔴🔴 奖励函数把目标教反了：死在 boss 面前比赢下 boss 更值（v1–v5 全中，2026-07-14 修复）

**这是本项目至今最严重的 bug，它解释了 act1 的全部症状——不存在什么「boss 墙」，策略只是在忠实执行我们给它的目标：走到 boss 面前，然后去死。**

- **根因**：potential-based shaping 只有在**所有终局状态的势能 Φ 都为 0** 时才是 policy-invariant 的。而引擎在**赢下一幕后的状态里不带 `floor`**（`_floor()` → 0，Φ=0，歪打正着是对的），**死亡的 `game_over` 状态却带 `floor`**（`RunSimulator.cs:2912` 发 `act`/`floor`，Φ=0.2×17=3.4）。这个**终局势能的不对称**给每个深度死亡白送约 **+2.9** 的 shaping 总奖励，而赢一分都拿不到。
- **实测（`m2_v5_deckobs/ckpt_00529`，真实引擎，贪心）**：

  | | 修复前平均折扣回报 | 修复后 |
  |---|---|---|
  | **赢下 Act 1**（n=2）| **+0.65** | **+0.65** |
  | **死在 boss 面前**（floor≥15，n=9）| **+1.76** 🔴 | **−1.05** ✅ |
  | 浅层死亡（n=5）| +0.91 🔴 | −1.10 |

  **修复前：9 个深度死亡局的回报全部高于 2 个胜局，无一例外；连浅层死亡（+0.91）都高于赢（+0.65）。** 单步看更直白：赢下 Act 1 的那一步 reward = **−2.4**（terminal-only 对照为 +1.0）。
- **这完美解释了 v5 的曲线**：`avg_floor` 从 10.4 单调爬到 14.2（走得深有 shaping 奖励），`dev 胜率`却从 0.074 单调跌到 **连续三次 0/50**（赢会被惩罚）。此前归因于「灾难性遗忘」的现象，**主因其实是奖励反了**。
- **修复**：shaping 时若下一状态是终局，则强制 Φ=0（`new_floor = 0.0 if next_done else _floor(...)`），使胜/死终局势能一致。修复后赢（+0.65）严格优于死（−1.05），且深度死亡（−1.05）仍略优于浅层死亡（−1.10）——**密集引导信号保留，但不再压倒终局胜负**。
- **连带修复：胜局的 `final_floor` 被记为 0**（终局状态无 floor），导致 **`avg_floor` 被每一个胜局向下拖累**。改为记录 episode 内**到达过的最深楼层**。
- 守护测试：`test_winning_an_act_out_returns_dying_deep`（回退修复即失败，已验证）、`test_a_win_is_recorded_at_the_deepest_floor_not_the_terminal_floor`。
- **教训**：奖励函数需要「赢的回报必须高于输」这条**不变量的自动化守护**，而不是靠人读代码。此前四个版本（v2–v5）全部投入在修表征，而真正的病灶在奖励——**表征修得再好，也只是让策略更高效地走向一个错误的目标。**

## 🔴 结构性 bug：策略从来不能喝药水（v1–v5 全中，2026-07-14 修复）

**`protocol.py::legal_actions` 的 combat 分支从顶层 `potions` 读药水并要求 `can_use` 字段。而引擎只在 `player.potions` 里发药水（`RunSimulator.cs:3042` → `{index,id,name,description,vars,target_type}`），且根本不发 `can_use`。两个条件同时不成立 → `use_potion` 候选从未被产出过一次。**

- **观测层却是从 `player.potions` 读的**（P2 加的）——所以**模型一直看得见那些药水（它们是实体），只是永远拿不到"使用"这个动作**。这个不一致从 M1 活到 v5。
- 引擎本身完全支持（`DoUsePotion`，且对不可用药水 fail-soft：不消耗就手动丢弃、异常也 catch）。**是我们的适配器读错了 key。**
- 实测（3 瓶药水的 boss loadout 起 `VANTOM_BOSS`）：修复前候选 = `play_card ×5 + end_turn`；修复后 = `play_card ×5 + use_potion ×3 + end_turn`。
- **对 boss 墙的直接影响**（同一 checkpoint `m2_v5_deckobs/ckpt_00529`、同一批 50 dev seeds、贪心、**到达 boss 门口同为 31/50**）：**boss 转化 0/31 → 3/31**。策略从未被训练过用药水（候选编码里 `use_potion` 那一维在整个训练中从未激活），等于在随机开瓶子就拿到 3 场胜利。**统计上 3 vs 0 尚不达常规显著（精确检验 p≈0.25），但机制已确凿；真正的收益要等带药水从头训练。**
- 修复：combat 分支改读 `player.potions`（保留顶层回退与显式 `can_use: false` 的尊重）；`AnyEnemy` 药水按存活敌人展开 `target_index`（引擎在多敌存活时强制要求，与 `play_card` 同规则）。`ACTION_TYPES` 本就含 `use_potion`，**张量形状不变、旧 checkpoint 仍可加载**。4 个守护测试，**去掉修复即失败**。
- **教训**：动作空间的每一类动作都应有「至少被产出过一次」的监控。若观测台早有「动作类型分布」视图，`use_potion` 恒为 0 会一眼可见——这个 bug 活不过一天。已列入观测台重做的首要视图。

- **连带发现：胜局的 `final_floor` 被记为 0**（实测：3 个胜局的 `final_floor` 全是 0.0）。因此 **`avg_floor` 这一遥测指标一直被胜局向下拖累**（赢了反而计 0 层），到达率/转化率的统计也需按「`outcome` 或 `floor≥15`」计算。待修（应记录 episode 内的最大楼层，而非终止态楼层）。

## Parity spike：第三方纯 Python 模拟器（`zhiyue/sts2-rl-agent`，2026-07-14）

**动机**：实测瓶颈是环境吞吐——一个 57s 的 act1 迭代里神经网络只占 **0.19s（0.3%）**，99.7% 是 C# 引擎在模拟战斗；GPU 利用率 3–12%、功耗 40W/360W。我们 **~85 decision steps/s**，该模拟器宣称 **~28,000 steps/s（~300×）**。而计划自己的诊断是「剩余瓶颈是训练量」（act1 仅训了 6,912 局，需求是数万至数十万局）。**加显卡/加 worker 都动不了这个数量级，纯 Python 模拟器是唯一的结构性解——前提是它的语义可信。**

**结论：值得投入做 backend 适配，但必须限定在「战斗」范围，且需先建更宽的差分测试。**

已验证（对着我们的真实引擎逐项比）：

- **内容名单：精确一致**。其 `encounters/act1.py` = 我们的 Overgrowth（12 normal / 3 elite / 3 boss，**逐个同名**）；其 `act4.py` = 我们的 Underdocks（10/3/3）。
- **怪物数值：精确一致**。CEREMONIAL_BEAST 252、VANTOM 173、THE_KIN 三体 **58 / 59 / 190**（连两个 Follower 血量 58 vs 59 的不对称都复刻了）、FUZZY_WURM_CRAWLER 55–57 随机区间一致；Ironclad 起始牌组一致。
- **核心机制：逐位一致**（同一副牌、同一遭遇、同一动作序列）：STRIKE 每张 6 伤（57→51→45→39）、DEFEND 每张 +5 格挡（0→5→10→15）、BASH 8 伤 + VULNERABLE 2 且耗能 2（energy 3→1）——**伤害、格挡、能量全部对上**。

未通过 / 风险：

- **seed/RNG parity 未建立**（0/3）。三种 seed 映射（`deterministic_hash_code` / 无符号 / 绝对值）都无法复现真实引擎的起手牌与敌人血量。真实引擎的战斗 RNG 由「run seed 哈希 → 命名流」派生，而 `CombatState(rng_seed=N)` 绕过了该派生链。**注意：位级 RNG parity 对训练迁移并非必需**（随机分布一致即可，敌人血量两边同在 55–57 区间；不同 seed 本就是跨 seed 训练的常态），但它意味着**暂时无法做 seed 锁定的逐步差分重放**。
- ~~其全局流程（full-run）不可用：`map/acts.py` 仍是过时的 STS1 配置且 RunManager 在读它~~ —— **该结论错误，已撤回（2026-07-14）**。`map/acts.py` 里确有 STS1 遗留名字（TheLich / GremlinNob / TwoLouses），但**全仓库无任何代码读取其 `boss_ids` / `weak_encounter_ids` / `elite_ids` / `strong_encounter_ids`——纯死代码**；ActConfig 实际只被用到 `num_rooms` 与 `num_weak_encounters`。真实遭遇池来自 `run_manager._get_encounter_pools()`：act 0 → `encounters/act1.py`（正确的 STS2 Overgrowth）、act 1 → `act2.py`、act 2 → `act3.py`。实跑一整局验证：刷出的是 LEAF_SLIME_S / TWIG_SLIME_M / TWIG_SLIME_S、FUZZY_WURM_CRAWLER、INKLET×3 —— **全部为正确的 STS2 Overgrowth 怪**。**其地图与幕结构没有问题。**
  - **教训（我自己犯的）**：先前的结论是**从静态代码推断而未验证调用链**——看到 STS1 名字就断言行为错误。与本文件反复强调的「读到的代码不等于跑起来的代码」是同一类错误。撤回并以实跑为准。
  - **仍然成立的限制**：本次 spike **只验证了战斗**（怪物血量/组成、伤害、格挡、能量、boss 阵容）。**地图生成、事件、商店、卡牌奖励等系统一概未验**。因此当前只应把它当**战斗后端**使用——理由不是「它的整局是坏的」，而是**「它的整局尚未被验证」**。若后续差分测试扩展到这些系统并通过，整局也可迁移（其相关系统均已实现）。
- 其官方 `docs/PARITY_GAPS.md` 自承 *"Exact parity is still not guaranteed"*（性质是审计覆盖不足，非已知错误），且 `KNOWN_ISSUES` #10 明言 *"trained model may develop strategies that exploit simulator inaccuracies"*。
- 其 full-run RL 训练 **100 万步后通关率 0%、avg_floor 8.9**（131 维扁平观测 + 固定 `Discrete(157)` 动作空间 + 无课程）——**对照我们 avg_floor 14.06 / act1 dev 7–14%**。即：**他们有速度没表征，我们有表征没速度**，这恰恰说明二者互补而非替代。

**吞吐实测（不采信其 README）**：单线程、无神经网络，act1 战斗 **250–272 场/秒、4,100–5,700 steps/s**（其 README 宣称 ~1,200 场/秒、~28,000 steps/s，**约虚报 5 倍**）。对照我们真实引擎（12 workers）：**2.5 boss 场/秒、~85 steps/s**。即**实测约 100×**（且是其单线程 vs 我们 12 workers）。直观换算：v5 的 boss 阶段跑了 15,408 局约耗时 1.7 小时，同样的量在模拟器上约 **1 分钟**。

**恰好互补的一点**：我们当前的墙是 **boss 战（0/31 转化）+ 灾难性遗忘**，这是**战斗问题**，而战斗正是该模拟器内容与机制都通过验证的部分。反过来，其 full-run RL 之所以 1M 步仍 0%，根源是表征（131 维扁平观测），而那正是我们 v2→v5 花四个版本治好的病。**他们有速度没表征，我们有表征没速度。**

**建议的下一步（尚未实施）**：(1) 先建更宽的差分测试（数百场随机战斗 × 脚本化动作序列，比对伤害/血量/powers 轨迹），覆盖 powers/遗物/药水/多敌人/intent；(2) 通过后，写一个把模拟器包装成我们 `EngineClient` / `protocol.py` 接口的 backend 适配器（架构本就为「两个 backend + M6 parity」预留），用于战斗课程阶段（normal/mixed/boss）；(3) **整局训练与全部评估仍走真实引擎，P5 的 1,000 test seeds 绝不允许碰模拟器**。

## 课程修复（2026-07-14，为 v6 准备；v5 当前 run 不受影响，它跑在固定 commit 的独立 worktree 上）

针对上面两条诊断（boss 采样池错误 + 灾难性遗忘）实施，`tools/m2_train.py` + `sts2rl.curriculum`：

- **Act 1 区域过滤（`act_variant`）**。实测：**300/300 个 A0 Ironclad train seed 的 Act 1 都是 Overgrowth**，boss 分布 CEREMONIAL_BEAST 36.0% / THE_KIN 34.7% / VANTOM 29.3%。而引擎目录里 Act 1 有**两个互斥区域**（OVERGROWTH / UNDERDOCKS），真实局只会走其中一个。此前课程把两个区域**全塞进遭遇池**，于是 **48% 的战斗训练量花在真实局里永远不会遇到的敌人上**（CORPSE_SLUGS / CULTISTS / SEAPUNK / TERROR_EEL / WATERFALL_GIANT…），**且三个阶段的晋级门槛都是在这个被污染的池子上校准的**。修复后遭遇池：normal 30→**16**、mixed 36→**19**、boss 6→**3**，**有效训练密度翻倍**。区域由 `detect_act_variant()` 在开训时探 8 个 seed 取共识**自动判定**（读 start_run 在第 1 层公布的 boss id → 查目录 act_id），**探针不一致则保留全池 fail-open**，避免游戏改版后悄悄只训半个课程。`--all-act-regions` 恢复旧行为。
- **boss 回放防遗忘（`--boss-mix`，默认 0.15）**。整局阶段（act1 / full_a0）每轮把 15% 的 episode 换成桥接 boss 战，对抗 ~1:100 的梯度稀释。**boss 回放的胜负单独记为 `boss_replay_win_rate`，不计入 `train_win_rate`**——晋级门槛不会被它污染。`--boss-mix 0` 恢复旧行为（v5 的等价配置）。
- 验证：84 个测试通过（新增区域过滤、未知区域 fail-closed、seed 切分不重不漏三个守护测试）；真机 smoke 确认 `act_variant=OVERGROWTH`、池子收缩、`boss_replay_episodes` 生效且不污染阶段分数。**注意：默认值已变（`--boss-mix 0.15`、区域过滤开启），复现 v5 需显式 `--boss-mix 0 --all-act-regions`。**

## 工具：训练实时观测（live telemetry）

`sts2rl.live.LiveEventWriter` + dashboard 的 Live 视图：worker 级事件（episode_start / action / status）经**有界队列 + 守护写线程**落到 `<run>/live/worker_NN.jsonl`，`workers.json` 原子替换供前端轮询。设计约束是**绝不阻塞采样线程**——队列满即丢（计入 `dropped_events`），`ppo.run_episode` 的 `live_callback` 外包 try/except（"Live observability must never be able to poison an episode"）。默认关闭（`live=None` 时为恒等路径），对训练语义无影响。RL + dashboard 测试 81 passed / JS 4 passed。

## v5 deckobs 训练（进行中，PC/RTX 5080）

`m2_v5_deckobs`（init-seed 0、cuda、12 workers、900 迭代 × 48 episodes、每 10 迭代 dev 50 局）已在修复哨兵数值 bug 后**从零重启**（含 bug 的那次运行已作废删除；前 49 迭代其实不受影响——normal/mixed 不刷 boss，编码逐位相同——但为了整条 run 只有一个代码版本、证据链干净，仍重跑）。

课程实测：`normal_combat` 第 39 迭代 dev 0.80 晋级（与 v4 的 0.82 平台吻合），`mixed_combat` 第 49 迭代 dev 0.68 一次过 0.60 门槛，随即进入 `boss_combat`（`rl/schema/m2_boss_loadouts.json` 在仓库中，桥接阶段默认启用）。第 50 迭代 loss 有限（0.0338）、熵健康——**哨兵截断修复在实战中确认**（同一阶段、同一批含 Waterfall Giant 的遭遇，修复前此处 loss 全 NaN）。

- **`boss_combat` 从零训练达不到门槛，决定跳过直接进 act1（2026-07-13，课程内部设计，roadmap reward 规范不变）**。跑满 320 个 boss 迭代（约 15,000 局 boss 战）后：dev 从 ~0.09（迭代 50–100）爬到 ~0.13（110–160）再到 **~0.19 平台**（170–370，峰值 0.24），train_win_rate 0.110 → 0.165。**确实在学，但收敛在 0.19，离 0.30 门槛还有距离且最近 200 迭代已平**。根因是课程位置不同：v4 的 boss 桥接是从 `ckpt_00379`（已在 act1 跑过几百迭代、上万局整局）恢复的**专项**，而 v5 从零开始在第 50 迭代就撞进 boss，此前只见过普通/混合战斗。继续耗在 boss 上会把 act1 预算烧光，而 act1 才是 v5「deck 可见性 → 组牌决策」命题唯一的检验场（且 act1 整局训练本身就包含 boss 战）。处理：从 `ckpt_00369` 用 `--stage act1` 恢复。
  - **过程教训（记录以免重犯）**：第 99 迭代时曾据「熵上升 + loss≈0 + 胜率平」判定 boss「没有学习信号」——**该判断是错的，下得太早**。此后 270 个迭代 dev 翻倍。低胜率区间用 50 局 dev 评估噪声极大（±3 局 = ±6pp），且慢学习在短窗口里与「不学」不可区分；判定停滞至少要看训练胜率的长窗口均值，不能只看熵和 loss 的形状。

- **boss 平台的根因诊断：桥接阶段被 v4 的盲眼牌组锁死（2026-07-13）**。剖开 `rl/schema/m2_boss_loadouts.json`（40 个 loadout）：
  - **到达质量是好的**——血量中位 74/80（87%）、遗物中位 5、药水中位 3，与 v4 的诊断一致，瓶颈不在这里。
  - **牌组是烂的**——大小中位 19 张，其中**起始牌占 56%**（742 张里 413 张是 Strike/Defend/Bash）；**升级卡仅 4.2%，40 副里 22 副（55%）零升级**。即「只加不删、见牌就拿」，牌组被稀释且不成长。6 伤的 Strike 磨 173 血的 Vantom 是算术问题。
  - **逐 boss 拆解**（`ckpt_00369` 贪心 × 120 dev seeds，总 28/120 = 23.3%）：CEREMONIAL_BEAST 33.3%、SOUL_FYSH 28.0%、THE_KIN 25.0%、VANTOM 24.0%、LAGAVULIN_MATRIARCH 18.8%、**WATERFALL_GIANT 5.6%**（哨兵血量那只，最难但**并非结构性不可赢**——1 胜证明有机制解）。**即使剔除 Waterfall Giant，其余仍只有 26.5%，够不到 0.30**，所以门槛过不去不能归咎于单个 boss。
  - **根因**：loadout 元数据写明收割自 `rl/runs/m2_v4_init0/ckpt_00369.pt`——**v4 的策略是看不见自己牌组的**（deck 可见性是 v5 才加的）。于是 boss_combat 在拿「盲眼策略造出的烂牌组」训练，且**牌组是给定的、不是策略组的**：**v5 的 deck 观测在该阶段结构上不可能起作用**。0.19 平台不是策略能力上限，而是**牌组质量上限**——这个阶段把 v4 的盲区固化进了课程，自我锁死。
  - **后续正确做法**：待 v5 在 act1 训练出成果后，用 v5 策略**重新收割 loadout**（`tools/m2_harvest_loadouts.py`），形成正循环（deck 观测 → 更好牌组 → boss 桥接才有意义）。在此之前 boss 桥接对 v5 无效，不应再投入预算。

对照基线：v4 act1 dev 最佳 12%、avg_floor ~14。

**act1 训练遥测**（第 370 迭代起，从 `ckpt_00369` 恢复）：dev 胜率在 0.02–0.14 之间剧烈震荡（50 局评估在该胜率区间的噪声 ±8–10pp，**大于待测效应，单点不可解读**）；`avg_floor` 是更稳的指标，已从 10.5 稳步爬到 **13.48**（第 489 迭代），逼近 v4 的 ~14。

- **「桥接→整局转化缺口」结案：不是转化缺口，是灾难性遗忘 + boss 采样池错误（2026-07-13）**。此前记为「待解」的那条（boss 专项 30% × 到 boss 率 ~50% 预期 act1 ~15%，实测 0–6%）已定位。证据链：
  - **到达质量与 boss 身份全部排除**。真实 act1 局走到 boss 门口的快照（`ckpt_00529` 贪心，28 个）vs 桥接 loadout（40 个）：血量 70/80 (86%) vs 74/80 (87%)、药水中位 3 vs 3（**无一局空药水**）、遗物 4 vs 5、牌组 17 vs 19 张——**几乎一致**。且 `start_combat` 生成的 boss 与真实 17 层的 boss **逐项相同**（CEREMONIAL_BEAST 252/252；VANTOM 173/173 + SLIPPERY_POWER 8；THE_KIN 三体 58+59+190 + MINION_POWER）——桥接打的就是真 boss，不是简化版。
  - **真凶一：灾难性遗忘**。同一批 dev seeds 上回测桥接 boss 胜率：`ckpt_00369`（boss 专项刚结束）**23.3%** → `ckpt_00529`（其后 160 个 act1 迭代）**12.5%**，**腰斩**。根因是信号稀释：一局 act1 只有 1 场 boss 且仅 62% 的局走得到，boss 的梯度相对普通战斗约为 **1:100**。
  - **真凶二：遗忘发生在最要命的地方**。逐 boss：CEREMONIAL_BEAST **33.3%→4.2%**、THE_KIN **25.0%→8.3%**、LAGAVULIN 18.8%→0%，而 VANTOM（24%）与 WATERFALL_GIANT（5.6%）基本不变。崩掉的恰是真实局最常遇到的两个。按真实 boss 分布加权，`ckpt_00529` 的期望 boss 胜率仅 **~9%**——观测到的 0/31 在噪声内（`0.909³¹ ≈ 5%`），**不再需要「神秘缺口」来解释**。
  - **课程 bug：boss 采样池与真实分布不符**。28 个真实到达快照里 boss 只有 3 种（THE_KIN 14、CEREMONIAL_BEAST 10、VANTOM 4，**全部 Overgrowth**）；而 `boss_combat` 从 **6 个 Act 1 boss 均匀采样**，另外 3 个（SOUL_FYSH / LAGAVULIN / WATERFALL_GIANT，全部 Underdocks）**在真实 Act 1 中一次未出现**。即**约一半的桥接训练量花在永远不会遇到的 boss 上**（含几乎不可赢的 Waterfall Giant），同时把「桥接胜率」这个指标稀释得不可用。
  - **act1 真实瓶颈的定量画像**（`ckpt_00529`，50 dev seeds 贪心）：终局层数中位 **17**（= boss 层），**62%（31/50）到达 boss 门口，boss 转化 0/31**。策略的路线规划与生存已经很强，**唯一的墙就是 boss 战**。
  - **修法（按杠杆，尚未实施，建议随 v6 一起上以保持 v5 可比性）**：(1) boss 遭遇池按真实 Act 1 分布采样（只保留实际出现的 boss）——近乎零成本；(2) **在 act1 迭代中混入固定比例的 boss 战 episode**，对抗 1:100 的信号稀释——这是真正的解药；(3) 更大的 act1 训练量。若 boss 转化率能从 ~9% 拉回 23%，act1 胜率即 `62% × 23% ≈ 14%`，超过 v4 的历史最佳。

- **v5 核心命题的第一个硬证据：策略确实在用牌组内容做组牌决策（2026-07-13）**。`tools/m2_probe_deckbuild.py` 在 **act1-only** checkpoint 序列（379/419/459/479）上的结果：
  - **盲参照是完美对照**：`--strip-deck` 下，三个牌组内容探针（perfected_strike / pommel / bloodletting）在**每一个** checkpoint 上 delta **恰好为 0.0000**（p_synergy 与 p_control 逐位相同）。因此 deck-visible 侧的任何 delta **因果上完全归因于牌组内容**，无其他信息通道可解释。
  - **信号真实且在增长**（delta，正=方向对齐）：

    | ckpt | act1 迭代 | perfected_strike | pommel | bloodletting |
    |---|---|---|---|---|
    | 379 | 10 | **0.0（仍是盲的）** | +0.008 | +0.264 |
    | 419 | 50 | +0.142 | +0.027 | **−0.273（翻转）** |
    | 459 | 90 | **+0.540** | +0.036 | +0.049 |
    | 479 | 110 | +0.107 | **+0.209** | +0.330 |

    最近两个 checkpoint **全部对齐且为正**；`pommel` 单调上升（0.008→0.027→0.036→0.209）；`perfected_strike` 在 459 达 +0.540（协同牌组下选取概率 0.83 vs 对照 0.29）。**关键时序：ckpt_379 时策略对牌组还基本不敏感，到 479 四项全响应——牌组感知是在 act1 里学出来的**，符合预期（只有 act1 才有选牌奖励；normal/mixed/boss 阶段没有组牌决策，故那些阶段的 checkpoint 对本探针无意义，早期用 `--checkpoints milestones` 跨阶段取样是方法学错误）。
  - **严格验收：尚未通过**。方向对齐 ✅（最近两个 ckpt），但跨 checkpoint **不稳定** ❌——`bloodletting` 在 419 出现一次符号翻转（−0.273 → +0.049 → +0.330）。翻转发生在进入 act1 仅 50 迭代时，需更多里程碑判断是早期抖动还是真不稳。
  - **探针工具的两个缺陷（会让验收虚高，待修）**：(1) `delta ≈ 0` 被判为 `aligned: true`——ckpt_379 的 perfected_strike 是 `0.4097/0.4097`，delta 恰好 0，**这是"对牌组不敏感"的证据却被标成"对齐"**；`skip_when_deck_is_bloated` 在 `p=0.0/0.0`（策略从不选该牌）时同样空洞通过。(2) `skip_when_deck_is_bloated` 被 `deck_size` 污染——`--strip-deck` 只遮牌组内容、**保留 deck_size**，而该探针正是靠改变牌组大小构造的（盲参照下 ckpt_219 delta 达 −0.0397，与其"信号"同量级），**它不能作为 v5 牌组内容可见性的证据**。

## v7-clean：可见性契约重建（2026-07-14，P1–P3 完成）

旧 `m2_v7_h256` 在 iteration 123 主动停止，以应用 Windows 计时器/攒批吞吐修复。随后对 704 个 artifact、58,301 个真实决策做离线覆盖审计，确认仍存在与 v1–v5 同类的结构盲区：shop 304 次却只有 `leave_room`；193 个多选状态产生 7,919 个碰撞候选；128 个 bundle 全为 `UNK`；目标敌人没有 pointer；敌我 power amount、intent 类型、未来 boss、完整地图、卡牌改造与目标实际伤害没有完整进入模型；unknown/offered 告警代码存在但未接入训练。

因此旧 v7 checkpoint 只作回归对照，不继续正式训练。保留 `hidden=256 / layers=4 / heads=8` 方向，按 [`v7_clean_plan.md`](v7_clean_plan.md) 完成动作/观测契约、visibility audit 与 fail-fast 门槛后，从新初始化启动 `m2_v7_clean_init0`。

P1–P3 初始验收词表为 2,299 行（含 UNK）；正式 Act 1 早期按失败样本追加到 2,304 后，iteration 83 又发现 Slippery Bridge 更深页面，证明逐个补洞不足。现在词表构建会从 checked-in 英文事件 localization 系统提取全部 `.options.*.title` 键，并继续全局 append-only：147 个新 option 位于 2,304–2,450，原有 1–2,303 索引完全不移动，当前共 2,451 行。最新全量复审覆盖 1,304 个 artifact、99,938 个真实决策，15 类动作全部 offered/chosen，candidate collision、pointer miss、unknown field/entity、非有限 feature 与 violations 均为 0。

P4 校准完成：随机 512 minibatch 因完整地图 padding 占满 15.8GB、10 分钟未完成；entity-length-aware packing + minibatch 256 将完整 train+50-dev 轮降至约 84–92 秒、峰值约 8.4GB。同 init/seed 流首轮 KL：`5e-5=0.00667`、`1e-4=0.01857`、`3e-4=0.02804`，因此正式配置选 `lr=5e-5`，dev 单点不参与 LR 排序。

P5 的 on-policy boss replay 已落地：`--on-policy-boss-replay` 完全不读取 v4 的 `m2_boss_loadouts.json`，只从当前策略真实抵达 boss 的首回合抽取 HP、保留 `+` 升级层级的完整牌组、遗物、药水与实际 boss id；run 内原子持久化，8 个不同快照后才启用 15% replay，滚动上限 256。真实引擎已从 `THE_KIN_BOSS` artifact 原样恢复 39 HP、17 张牌（1 张升级牌）并进入 `combat_play`；小型 CUDA trainer smoke 为 0 error / 0 visibility violation，且阶段表没有旧静态 boss stage。

正式 `m2_v7_clean_init0` 已由 watchdog 启动。iteration 0：96 局、1,709 decisions、0 engine error、0 visibility violation、KL 0.00354、4 epochs、grad norm 0.996→0.460、reward health 正常，墙钟 22.1 秒；iteration 9 normal dev 42/50（84%）超过 80% 门槛，首个 checkpoint 已落盘并晋级 mixed。on-policy boss buffer 在 combat curriculum 阶段按设计保持为空。

iteration 79 mixed dev 达 40/50（80%）后暴露两个独立问题：(1) Act 1 strict audit 发现 5 次未知 option（Future of Potions 与 Dig）；(2) checkpoint 在 stage_index 增加前保存，watchdog 恢复后回到 mixed，之后每逢 gate 又重复。PPO 防火墙始终在更新前中止，所以未知实体没有污染权重。现已修复保存顺序并增加 watchdog 无 checkpoint 进展熔断；从全部失败 artifact 另审核到 Lift 与 Slippery Bridge 后续选项，共追加 5 个稳定 ID。checkpoint 79 加载只扩展 `id_embed.weight` 尾部 5 行并重置 Adam，不改变任何旧实体权重。

修复后显式从 checkpoint 79 进入 Act 1 的 iteration 80 已完成：82 个整局 + 14 个 on-policy boss replay、10,805 decisions、0 engine error、0 unknown/collision/pointer/nonfinite/violation；15 类动作全部 offered/chosen。KL 0.0110 触发 target-KL early stop，只执行 1 epoch（低于 0.02 hard stop）；训练现持续停留在 Act 1，不再返回 mixed。

iteration 83 随后命中 Slippery Bridge 的 `HOLD_ON_2→3→4→5→6` 链并被 strict audit 正确中止。根因不是编码器漏报，而是稳定 option 词表仍靠轨迹偶遇补全；权威 localization 显示该事件最终进入 `HOLD_ON_LOOP→HOLD_ON_LOOP` 自环。已改为预注册全部本地化事件 option，并增加完整链回归测试。另补两项恢复/展示契约：每次成功 PPO iteration 原子保存 `resume.pt`，watchdog 优先恢复较新的逐迭代点；history 记录显式 resume 分支，Dashboard 丢弃 cutoff 之后的废弃分支指标并按最近活跃时间默认选择 run。重启前测试为 RL 102 + 根目录 29 = 131 passed。

恢复验收已完成：从 checkpoint 79 重启时 Dashboard 的 canonical 曲线正确回到 79；新的 iteration 80 写出 `resume.pt` 后，watchdog helper 实际选择该文件。iteration 80–89 连续完成 820 episodes / 118,832 decisions，0 engine error、0 candidate collision、0 pointer miss、0 unknown entity、0 non-finite feature、0 visibility violation；曾经失败的 iteration 83 已正常越过。`ckpt_00089.pt` 保存 2,451 行 embedding，50-seed dev 为 26% 胜率、平均 15.3 层；训练继续到 iteration 90 且逐迭代恢复点已推进到 90。

## 下一步

- P4/v7-clean：external submodule 与父仓 pin 已固定；on-policy replay 实现与计划更新随本轮提交。
- v7-clean 选定：96 episodes/iteration、minibatch 256 + length-aware packing、`lr=5e-5`、KL early-stop；P3 fail-fast 全程保持开启。
- 从 `m2_v7_clean_init0/ckpt_00079.pt` 显式恢复 Act 1：256×4×8、96 episodes/iteration、minibatch 256 length packing、`lr=5e-5`、KL 0.01/0.02、12 workers、CUDA、`--on-policy-boss-replay --stage act1 --max-stage act1`；先验证逐迭代 `resume.pt` 和 Dashboard 当前分支，再连续观察至少 10 轮。
- Act 1 连续三个 50-seed gate ≥30% 且 450-seed audit 不退化后进入 full A0。
- P5：5 初始化、terminal-only ablation 完成后，才运行正式 1,000 test seeds 验收。
