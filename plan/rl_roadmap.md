# STS2LLM RL 训练路线图（Phase B 详细规划）

这份文件是 Phase B（RL 线）的持久化路线图，跟 `plan/plan.md` 分开维护：`plan.md` 是整个项目（含 Phase A）的主线进度流水账，这份文件专门记录 RL 训练从 v1 到最终目标的分阶段规划，以及每个 Stage 的状态。**以后每次对 Phase B 做新的规划/改范围，都在这份文件里更新**，不要只留在临时的 plan-mode 文件里（那些文件可能被下一次 plan mode 调用覆盖，不持久）。

**每个 Stage 的完整 plan-mode 设计文档也各自存成独立文件**（不只是下面表格里的一行摘要）：`plan/stage0_v1_single_combat.md`、`plan/stage1_data_reorg.md`、`plan/stage2_triggered_powers.md`，以此类推。新 Stage 开工前先在 plan mode 里设计好，approve 之后连同实现结果一起存一份 `plan/stageN_<slug>.md`。

## Context

Phase B v1（单场战斗模拟器 + Gym env + MaskablePPO）已经建好并验证通过：铁甲战士单角色、只做单场战斗、28 张卡（来自 spire-codex 转换）、11 只 Act 1 普通怪，训练 loop 跑通、胜率/reward 随训练上升、6/6 单测通过、check_env 通过、500 局随机 fuzz 测试零报错。详细实施记录见 `plan/plan.md` 的 Progress Log。

2026-07-10，用户提出两个诉求，促成了这份路线图：

1. **数据组织方式**：卡牌应该按角色分类（Ironclad / Silent / Defect / Necrobinder / Regent），怪物应该按区域分类（不是简单的 Act 1/2/3，而是 spire-codex 实测数据显示的 4 个区域：**Act 1 - Overgrowth**、**Act 1 - Underdocks**、**Act 2 - Hive**、**Act 3 - Glory**），方便以后分角色/分难度训练。
2. **完整路线图**：从 v1 现状到"最终目标"，在 spire-codex 这个数据源的实际能力边界下，训练计划可以怎么规划。

规划过程中发现两个改变路线图形状的关键事实：

- **v1 只保留了 28/87 张铁甲战士卡的根本原因，不是转换脚本的 bug，而是 v1 批准的机制集合本身太窄**。59 张被排除的卡里，48 张（29 张"复杂 exhaust/draw-pile/条件触发机制" + 19 张"Power 类持续效果卡"）本质上都需要同一样东西：一个**触发式效果系统**（监听回合开始/结束、抽卡、卡牌被消耗、HP 变化等事件，而不是"打出卡牌立刻结算"这种一次性模型）。这个系统一旦建好，会同时提升**所有**职业的卡牌保留率，不只是铁甲战士——所以它在路线图里的排序很重要：应该在批量转换其他职业之前做，否则以后要返工重新审计一遍。
- **spire-codex 提供的数据远不止卡牌/怪物**。它的 API 路由表（`rl/data/_spire_codex_readme.txt`）显示还有：`relics`、`potions`、`events`、`encounters`、`orbs`（Defect 的机制）、`afflictions`、`keywords`、`modifiers`、`ascensions`，甚至 `/api/runs/list` 和 `/api/runs/shared/{hash}`——**真实玩家提交的完整跑图记录**（角色、遗物、卡组、种子、结局）。这意味着"完整 run 结构"这个终极目标，不必靠我们自己瞎猜地图生成规则/商店定价/事件概率分布，可以用真实社区数据校准。当然，所有这些内容都受 spire-codex 的 **PolyForm Noncommercial License 1.0.0** 约束（非商业用途，需带版权声明），这个限制会延续到路线图的每一个阶段。

## 路线图：Stage 0 → 最终目标

| Stage | 状态 | 目标 | 依赖的 spire-codex 数据 | 需要新建的引擎能力 | 相对 v1 的工作量级 |
|---|---|---|---|---|---|
| **0** | ✅ 已完成 (2026-07-09) | 单场战斗冒烟测试 | cards(ironclad 子集)/monsters(act1 子集) | combat.py 状态机、powers.py、Gym env、MaskablePPO | 基准 1x |
| **1** | ✅ 已完成 (2026-07-10) | 数据目录重组：按角色分卡池、按 4 区域分怪物库 | 无新增，仅重新组织已抓取的 87×5 卡 + 115 怪 | 无（纯数据/文件结构改动，`sim/card.py`/`sim/combat.py` 的 loader 支持按角色/区域挑选文件） | 0.2x，快 |
| **2** | ✅ 已完成 (2026-07-10) | 触发式 Power 效果系统 | `/api/powers`（257 个 power 的完整定义，已抓取） | `combat.py` 里的通用事件总线：回合开始/结束、卡牌打出/消耗、HP 变化、格挡获得等 hook 点；重新审计 v1 已排除的 48 张铁甲战士卡能否用新系统建模 | 1.5-2x，架构性改动，是后续所有阶段的地基 |
| **2.5** | ✅ 已完成 (2026-07-10) | 连续战斗 + 简单卡牌奖励（run 连续性最小可行版） | 无新增，复用已有卡池/怪物库 | 新建 `sim/run.py` 的 `Run` 类：5 场连续战斗、HP 跨战斗结转不重置、场间简单选卡奖励（3选1或跳过）；`env.py` 改成 combat/reward_pick 双阶段状态机 | 0.5x，比完整 Stage 6 小很多，但直接解决"牌库不变化+战斗不连续"这两个训练代表性问题 |
| **3** | ⏸ 推迟 (2026-07-10 用户决定) | 全 5 职业卡池 | `/api/cards`（577 张，已抓取），Defect 需要额外看 `/api/orbs` | Defect 的 orb 子系统（Focus/Evoke/Passive，全新机制，需要专门调研）；Necrobinder/Regent 是 STS2 原创职业，机制未知，需要先读一遍它们的卡再设计 | 每个职业约等于铁甲战士当前投入的 3-4x × 4 个新职业 |
| **4** | ⬜ 未开始 | 全区域普通怪 + 精英/Boss | `/api/monsters`（115 只，已抓取） | 精英/Boss 常见的"HP 阈值切换阶段"或多分支 AI，需要扩展 `_build_monster_cycle` 之外的一套"条件式 FSM"解释器（比 v1 严格排除分支的做法更进一步） | 2-3x，尤其 Boss AI 复杂 |
| **5** | ⬜ 未开始 | 遗物 + 药水 | `/api/relics`、`/api/potions`（未抓取，需要新查） | 复用 Stage 2 的事件系统作为遗物触发的执行器；PlayerState 加 relic/potion 库存 | 1-1.5x |
| **6** | ⬜ 未开始 | 完整 run 结构 | `/api/events`、`/api/encounters`、`/api/merchant/config` | 地图节点图生成、商店、事件对话选择、战斗后奖励选卡——Gym env 从"一局战斗"变成"一整个 run"，动作空间要分层（地图选择 + 战斗内动作），reward 变稀疏（run 结束才有终局信号），比 v1 的 env 设计复杂得多 | 3-5x，最大的单一里程碑 |
| **7** | ⬜ 未开始 | 用真实玩家数据校准 | `/api/runs/list`、`/api/runs/shared/{hash}`、`/api/runs/stats`、`/api/runs/encounter-stats` | 用真实提交的跑图统计校准地图生成分布、怪物遭遇频率、事件选择结果分布，而不是凭空编造——这是 spire-codex 相对我们自己观测数据的独特优势 | 1-2x，主要是数据分析 + 校准逻辑 |
| **8** | ⏸ 推迟 (依赖 Stage 3) | 多角色训练/泛化 | Stage 3 产出 | 训练策略网络支持角色条件输入（单策略多角色，或分角色策略） | 依赖 Stage 3/6 完成度 |
| **9** | ⬜ 未开始 | 难度爬升（Ascension） | 卡/怪数据里已有的 `*_ascension` 数值字段 | 训练课程设计（curriculum learning），逐步提高 ascension | 1x，主要是训练策略 |
| **10（最终目标）** | ⬜ 未开始 | 用训练好的策略回接真实游戏 | Phase A 的 `mcp__sts2__*` / HTTP API | 把真实 `get_game_state` 输出翻译成训练时的 observation 格式，把策略输出的 action 翻译成真实工具调用（`combat_play_card`/`map_choose_node`/...），跑一整局真实对局验证 | 2x，是整个项目的收官验证 |

**许可证提醒会贯穿所有阶段**：只要某个 Stage 用到 spire-codex 的数据，那个 Stage 的产物（数据文件 + 在其上训练出的模型权重）都继续受 PolyForm Noncommercial 约束，需要在每个新增数据文件的 `_meta.license` 里带上 Required Notice。Stage 7 的真实玩家跑图数据额外受 spire-codex 自己的用户提交条款约束，用之前要看一下 `/api/runs/*` 有没有单独的使用限制说明。

## 排序原则

- **2026-07-10 用户决定：先把铁甲战士单角色训练"做完整"，再考虑扩到其他职业**——Stage 3（全5职业卡池）和依赖它的 Stage 8（多角色泛化）明确推迟，不是取消，是排到"铁甲战士线"稳定之后再捡起来。
- **"铁甲战士线"接下来的候选，按对训练质量的直接贡献排：**
  - **Stage 2b**：把 Stage 2 明确排除的剩余铁甲战士卡（递归自动出牌、动态费用、卡牌实例持久状态、"下一张牌"待定修饰符等，约32张里的一部分）做掉，卡池更完整。
  - **Stage 4**：怪物库扩到全区域 + 精英/Boss——这个不挑角色（怪物打谁都一样），跟"专注铁甲战士"完全不冲突，而且是"训练好铁甲战士"必须的一环（现在只有 Act 1 普通怪，没有精英/boss，真实 run 里这些是难度和资源压力的主要来源）。
  - **Stage 5**：遗物 + 药水——同样不挑角色，是 STS build 多样性和策略深度的重要来源。
  - **Stage 6**：完整 run 结构（地图选路、商店、事件）——目前最大的单一里程碑，Stage 2.5 已经把"牌库演化"和"战斗连续"这两个最核心的价值提前拿到了，Stage 6 剩下的增量主要是地图选路的自由度、商店/事件系统。
  - 这四个（2b/4/5/6）做完之后，"铁甲战士打完整局 STS2"这个目标在训练环境里才算真正闭环，届时再回头做 Stage 3（其他职业）会更顺——复用的是同一套已经验证过的 run 结构/触发系统，不用重新趟一遍。
- **Stage 7 依赖 Stage 6**：没有地图/商店/事件结构，真实跑图数据校准无从谈起。
- **Stage 9（难度爬升）主要是训练策略问题，不是数据/引擎问题**，可以在铁甲战士线稳定后随时插入，不强制排在 Stage 3 之前或之后。

## Verification 通用标准

每个 Stage 完成后都应该过一遍这三项，跟 v1 的验证标准一致：

1. `pytest rl/tests/`：已知交互单测全部通过。
2. `gymnasium.utils.env_checker.check_env()`：env 改动后依然符合 Gym API 规范。
3. 随机 fuzz 测试（跨所有 encounter/卡组随机对局）：零报错。
4. 新增/占位数据文件的 `_meta` 块要清楚标注来源、许可证、当前局限，不要让"占位"和"已完成"的内容混淆。

## 更新日志

- **2026-07-10**：初始版本。写下 Stage 0-10 完整路线图，Stage 0（v1 单场战斗）标记已完成。同一天内实现并完成 **Stage 1**（数据目录重组：`rl/data/cards/<color>.json` + `rl/data/monsters/<region>.json`，铁甲战士 28 卡 / 11 怪物原样迁移，其余职业/区域留占位文件）。验证：pytest 6/6、check_env、500 局 fuzz 测试全部通过。下一步候选待用户决定：Stage 2（触发式 Power 系统，推荐，杠杆最大）、Stage 3 单职业试点（如 Silent）、Stage 4 怪物扩区域、Stage 5 遗物+药水。
- **2026-07-10（同一天）：完成 Stage 2（触发式 Power 效果系统）。** 单独走了一遍 plan-mode 详细设计（见 `plan/stage2_triggered_powers.md`，本次也把它存成独立文件了）。核心架构：`PlayerState` 新增 `powers`/`temp_powers` 字典（value 是"triggers"字典：事件名→效果列表，在 `gain_power` 时从卡牌自身的 base/upgraded 数值直接捕获，所以升级后的数值——比如 Demon Form+ 每回合给 3 点 Strength 而不是 2——自动正确，不需要额外的升级感知逻辑）+ 4 个回合内标记；`EnemyState` 新增 `temp_strength_penalty`（给 Mangle）；统一的 `_exhaust_card()` 消耗入口（替换掉原来两处各自直接操作 `exhaust_pile` 的写法）；`_fire_power_effects(event)` 通用分发器，接线在回合开始/消耗任意卡/本回合失血/获得格挡/施加虚弱五个触发点；新增 `conditional` 通用条件包装效果类型（覆盖"本回合如果…"类卡）；`_player_attack_damage()` 集中了所有玩家伤害计算，作为 Cruelty 加成的唯一改动点。铁甲战士卡池 28→**55 张**（新增 27 张：14 张 Power 类 + 13 张复杂机制卡，具体映射见 `plan/stage2_triggered_powers.md`）。过程中审计发现并修了两个真实 bug：(1) Drum of Battle 的"消耗时获得能量"效果本身也会随升级变化（2→3），发现 `CardDef.on_exhaust_effects` 当时没有区分 base/upgraded，加了 `on_exhaust_effects_upgraded` 字段修复；(2) Rupture 卡在 spire-codex 原始数据里升级 key 用的是 `strength` 而不是其他同类卡通用的 `strengthpower`，导致按变量名读取时错过了升级加成（1→2 没生效，停在 1），手动核对全部 27 张卡的升级前后数值后发现并修复。验证：11/11 单测通过（含 5 条新增的机制专项测试：Demon Form 跨回合 Strength 累加、Cruelty 与 Vulnerable 的乘算顺序、Unmovable 每回合仅翻倍一次、Dark Embrace 响应"任意卡被消耗"、Barricade 格挡跨回合保留）、check_env 通过（观测空间从 98 维涨到 112 维，新增 14 个 Power 激活标志位）、1000 局随机 fuzz 测试零报错、8000 步训练冒烟测试正常跑通。明确排除、留到 Stage 2b：递归自动出牌（Havoc/Stampede/Hellraiser/Howl from Beyond）、动态费用（Corruption/Stomp）、卡牌实例级持久状态（Rampage）、"下一张牌"待定修饰符（One Two Punch/Unrelenting/Rage）、生成新卡实例（Anger/Stoke/Infernal Blade）——铁甲战士卡池里还剩 32 张排除卡，原因见 `_conversion_report_ironclad.json`。下一步候选：Stage 3（其他 4 个职业的卡池，现在有更强的机制系统打底，保留率应该会比这次铁甲战士的 55/87 更高）或 Stage 2b（把上述递归/动态费用类卡也做掉）。
- **2026-07-10（同一天）：补了 Stage 2 的正式训练跑（10 万步，跟 Stage 1 同规格），并把训练曲线可视化 artifact 改成跨 Stage 追踪的形式。** 用户要求"每个 stage 结束都这样训练一下"，已存成记忆（`rl-per-stage-training-run` memory）。Artifact 现在是一个 `STAGE_RUNS` 数组，每个 Stage 一条记录（卡池/怪物/训练历史），主图表显示最新 Stage，下面加了一个跨 Stage 对比表格（胜率/reward 前→后）。Stage 2 这次 10 万步结果：胜率训练前后都接近 100%（怪物库本来就不难，没有上升空间），reward 前 1.035 → 后 1.253，中间同样有一个探索期的下探（第 2000 步跌到 1.004）然后回升企稳，跟 Stage 1 的模式类似。链接见 `plan/plan.md` 对应条目。
- **2026-07-10（同一天）：完成 Stage 2.5（连续战斗 + 简单卡牌奖励）。** 用户看完 Stage 2 训练曲线后追问"这样训练真的有用吗，还是只是当前阶段罢了"，指出两个核心问题：起手牌固定不变、每场战斗孤立不连续，不像真实 STS。评估后确认：这不是"以后自然会变好"的问题——光做 Stage 3/4/5（加内容）不会解决它，需要专门做点什么。用户在"只做便宜的deck随机化临时修复" / "推荐的Stage 2.5" / "直接上完整Stage 6" 三个选项里选了推荐方案。设计与实施见 `plan/stage2.5_run_continuity.md`。新建 `sim/run.py` 的 `Run` 类：5 场连续战斗、HP 跨战斗结转（不再每场满血重置）、每场胜利后（非最后一场）简单选卡奖励（3选1或跳过，从非Basic稀有度卡池随机抽）。`env.py` 改成 combat/reward_pick 双阶段状态机，动作空间 41→45，观测空间 112→126维（新增phase标志、第几场进度、3张候选卡特征）。**训练过程中额外抓到一个真实 bug**：`train.py` 的胜率统计还在检查旧的 `"win"/"loss"` 字符串，新 env 上报的是 `"run_won"/"run_lost"`，导致第一次训练跑出"训练前后胜率都是0%"的假象（实际训练是有效的，只是统计代码没匹配上）；修复后重训，**训练前通关率 25% → 训练后 98-100%**，reward 3.156→8.350，是路线图里第一条真正的"从很难到学会"的S形学习曲线（对比 Stage 1/2 单场战斗下随机策略就有 97-100% 胜率，这次纯随机策略下 5 场连续战斗的通关率只有 23-25%，证明任务真的变难了、也证明策略真的学到了东西）。验证：Run类新增6个单测全通过、check_env通过、500局fuzz测试0报错。已加进训练曲线 artifact。明确排除，仍留给 Stage 6：地图选路（战斗序列固定随机，不给选择权）、商店、事件、遗物、药水。
- **2026-07-10（同一天）：用户决定推迟 Stage 3（全5职业），专注把铁甲战士单角色训练做完整。** Stage 3 和依赖它的 Stage 8 标记"⏸ 推迟"（不是取消）。"排序原则"改写为"铁甲战士线"优先：接下来候选按顺序是 Stage 2b（补剩余排除卡）→ Stage 4（怪物扩全区域+精英/Boss，不挑角色）→ Stage 5（遗物+药水，不挑角色）→ Stage 6（完整 run 结构），这四个做完之后铁甲战士才算在训练环境里"打完整局"，届时再回头做 Stage 3 会更顺（复用同一套 run 结构/触发系统）。下一步具体做哪个待用户确认。
