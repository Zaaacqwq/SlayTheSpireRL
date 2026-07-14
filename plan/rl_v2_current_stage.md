# M2 Ironclad（进行中）

更新日期：2026-07-13

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

## 下一步

- P4：v5 长跑（act1 → full_a0 晋级门槛 dev 0.30）；到点后扩展到 5 初始化 + ablation（`--terminal-only`）。
- P5：训练产出后跑正式 1,000 test seeds 验收报告（`tools/m2_final_eval.py <5 ckpts> --seeds 1000`）。
- 仓库卫生：修正 fork 分支名拼写（`rl-v2-protacal-` → `rl-v2-protocol-state-machine`）并把 pin commit 推到正确分支，否则下一台新机器按 `.gitmodules` 仍会拿到坏的 `7fe0006`。
