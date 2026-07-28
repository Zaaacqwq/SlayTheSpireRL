# 已知盲区 / 待修 backlog

记录已确认但暂不立即修的动作空间 / 引擎保真度缺口。每条含：现象、证据、影响、优先级、修复方向。

---

## 00. ✅ 强制多选 5+ 张的 `card_select` fail-closed → 拖垮整迭代（已修复）

**状态**：**已修复（2026-07-24，`protocol.py`）**。

**现象**：`m2_v7_acts12_hpshape` 训练跑几分钟就自动停：trainer `exit 1`、watchdog 连崩 8 次后放弃。

**证据**：trainer 日志大量 `ProtocolError: no legal actions derived for 'card_select'`（本 run 257 次），单迭代内攒到 6/96 越过 `MAX_EPISODE_ERROR_RATE=0.05` → `episode error rate too high` → fail-closed exit。`from1499` 也有 40 次但从未在单迭代扎堆到 >5%。

**根因**：`card_select` 只在 `0 < min_select <= max_select <= 4` 时枚举 `select_cards`;`min_select>0 且 max_select>4`（强制选 5+ 张）两分支都不进 → `out` 空 → `ProtocolError`。**关键：`<=4` 守卫不只是防组合爆炸,它正好把每候选的实体绑定数压在 `MAX_CANDIDATE_BINDINGS=16` 以内**——select_cards 每张牌绑 ~2–4 个实体(slot + stat/enchant/affliction),所以只有小选牌能被编码。

**为何现在爆发**：hp-shaping 干净 run 模型变强、走更深（dev reached_act2 0.5+），撞深层大选牌屏更频繁。**不是 sim 退役引起的**。

**弯路(记录教训)**：第一次「修复」直接去掉 `<=4` 守卫让大选牌照常枚举——结果产生 17-绑定候选,在 `entities.py:318` 抛 `ValueError: candidate select_cards references N entities`,而且**这错在 `BatchedAgent` 推理线程里抛 → 推理服务器崩 → 整批 episode 报 `InferenceServerError`**(比原来的 per-episode ProtocolError 更糟)。之前误判的「InferenceServerError / CUDA」批量崩其实同根。truncate 绑定也不行——会造候选碰撞,而碰撞是 fail-close 违规项。

**正确修复**：新增 `SELECT_CARDS_MAX_SIZE=4`,枚举张数封顶到它(proven 绑定安全,from1499 从没报过 ValueError);`min_select` 超过它无法表示 → 兜底 `skip_select` 优雅降级(不崩、不碰撞、per-episode 安全)。测试 `test_card_select_caps_selection_size_to_the_binding_budget` / `test_mandatory_select_larger_than_cap_falls_back_to_confirm` / `test_degenerate_card_select_never_raises`。**遗留**:agent 对 >4 张的选牌无法精细表达(选 ≤4 或跳过),是已知表征限制。

---

## 0. ✅ KAISER_CRAB_BOSS（act2 boss 之一）不刷怪 → 免费通关（已修复）

**状态**：**已修复（2026-07-21，引擎侧 Harmony 补丁）**。发现→根因→修复→回归全部完成。

**现象**：agent 走到 act2 boss 房，若该 boss 是 `KAISER_CRAB_BOSS`，则**零战斗、零掉血、直接拿 boss 卡奖励并算通关**（幕数推进）。用户在 dev 局 `01589_m2-a0-ironclad-36` 发现：step 309 选 boss 房（HP 63/96）→ step 310 直接 card_reward（HP 仍 63/96），中间无任何 `combat_play` 步。

**证据**：
- 逐场：act1 boss 50/50 正常打（HP 掉、有 combat 步）；act2 boss 到达 20 场里 **14 正常打、6 跳过**，6 个跳过的**全是 KAISER_CRAB_BOSS**（0 combat 步、HP 进出不变）。正常打的是 KNOWLEDGE_DEMON(8)、THE_INSATIABLE(6)。
- 根因隔离：`start_combat(encounter=KAISER_CRAB_BOSS)` 直接返回 `phase=card_reward, enemies=[]`——**遭遇本身不刷怪**，与 act 切换路径无关。
- 全 12 boss 扫描：**只有 KAISER_CRAB_BOSS 一个坏**（enemies=0）；act1/2/3 其余 11 个 boss 全部正常 `combat_play` 刷怪。

**影响**：act2 有 3 个 boss，~1/3 的局 boss = KAISER_CRAB → 走到即免费通关。`cleared_act2` / dev 胜率被灌水，真实 act2 boss 战力被高估；训练信号被污染（教 agent「走到 act2 boss 有时=白赢」）。附带：该「通关」步 reward=-1.2（per-act 楼层 shaping 在幕边界的负跳变），但 outcome 记为 True。

**根因（反编译 `lib/sts2.dll` 定位）**：Kaiser Crab 由两个怪组成——`Crusher`（身体/左臂）+ `Rocket`（右臂）。两者的 `AfterAddedToRoom()` 会访问一个 `NKaiserCrabBossBackground Background` getter，该 getter 在 headless 里必抛：
```csharp
Control val = (Control)((NCombatRoom.Instance?.Background) ?? (NBestiary.Instance?.Layout ?? throw new InvalidOperationException()));
_background = ((Node)val).GetNode<NKaiserCrabBossBackground>("%KaiserCrab");
```
headless 无视觉节点 → 两个单例都 null → `throw`。异常被 `TaskHelper.LogTaskExceptions` 吞掉，怪物没加进战斗 → 0 敌人。第一层其实还有 `Godot.CanvasItem.SetVisible(bool)` 在 GodotStubs 里缺失（`MissingMethodException`）。

**修复（引擎侧，2 处）**：
1. `src/GodotStubs/UI.cs`：给 `CanvasItem` 补 `SetVisible(bool)` / `IsVisible()`（Godot 把 Visible 属性绑定成这俩方法，游戏代码直接调）。
2. `src/Sts2Headless/RunSimulator.cs`：新增 `PatchKaiserCrab()` Harmony 补丁（跟已有的 `PatchTalkCmd` VFX no-op 同款）——把所有 `Background` getter 返回该类型的怪物（Crusher/Rocket，反射扫描不写死）的 getter 前缀成返回一个 `GetUninitializedObject` 造的 dummy（不跑构造、无副作用），并把 `NKaiserCrabBossBackground` 的所有 `Play*` 动画方法（PlayAttackAnim/PlayHurtAnim/PlayArmDeathAnim/PlayBodyDeathAnim/PlayRightSide*）no-op（它们 deref 空的 `_animController`）。只影响 Kaiser Crab，纯视觉/音效，零 gameplay 改动。

**验证**：KAISER_CRAB 现在 `combat_play` 刷出 CRUSHER(209)+ROCKET(199)；打满 68 步真实交火、双怪击杀、正常到 boss 卡奖励；12 boss 全扫 0 坏；3 局完整 Ironclad 对局 Completed 3/3；RL pytest 133 passed。

**训练影响**：修复后 acts_12 的 `cleared_act2` / dev 胜率会**下降到真实水平**（免费通关消失）——这是信号变干净，不是退步。

**待办**：提交 submodule commit 并更新父仓 pin（记得：提交前从干净源码重建 + 跑回归 + 确认 commit 在 fork remote 可达——见 timeline 的三次踩坑）。

---

## 1. 满槽药水掉落被静默丢弃（无「换药」决策）

**状态**：已实测确认（2026-07-21），暂缓修复，优先级低。

**现象**：战斗后金币 / 遗物 / 药水由引擎自动领取，不是决策；只有卡牌奖励作为 agent 决策。当**药水槽已满（3 瓶）**时又掉落一瓶药水，引擎**静默丢弃**这瓶新药水——belt 原封不动，**从不向 agent 弹出「丢弃哪瓶换新的」决策**。

**证据**：
- 代码：`external/sts2-cli/src/Sts2Headless/RunSimulator.cs:2489` `DetectPostCombatState` —
  `GoldReward | RelicReward | PotionReward → reward.SelectUnsynchronized()`（自动领取，try/catch 吞异常），
  只有 `CardReward` 走 `CardRewardState` 交给 agent。满槽注释见同文件 ~3436
  「A full potion belt is a normal UI outcome: keep rewards already taken, mark the remaining choices skipped」。
- 实测：30 场对照/测试（`FUZZY_WURM_CRAWLER_WEAK`，Ironclad，高血，战斗中不喝药）：
  - 空槽：~33% 战斗掉药 → 自动进空槽（0→1）。
  - 满槽 3 瓶：全部 30 场战后**恰好还是原来那 3 瓶**（`same_set=True`），从不涨到 4、从不顶替、从不出现丢弃决策。
  - 金币每场自动入账（`gold_earned` 10–20）。

**影响**：真实 STS 满槽掉药会给玩家「丢一瓶换新的」决策，headless 引擎砍掉了这点 agency。
触发条件窄（满槽 **且** 掉药 ~33% **且** 掉的比手里好才有实际损失），是次要策略损失。
训练/评估都用同一 headless 引擎，内部一致，模型不会被相对惩罚。仅两处要紧：
(a) 将来与真实游戏 parity 对比时算一个已知偏差；(b) 想让模型学「换药」精细操作时。

**修复方向**：在引擎 `DetectPostCombatState` 里，当 `PotionReward` 遇到满槽时不要静默丢弃，
而是发出一个 `card_select` / 专门的 potion-discard 决策（候选 = 现有 3 瓶 + 「跳过不换」），
并在 `protocol.py` 加对应动作。属于 `use_potion`-never-offered 那一类动作空间修复。
注意：探测时启发式会喝药（`use_potion` 优先级 1 > `end_turn`）导致 belt 提前清空、污染测试——
测满槽奖励行为时战斗中绝不能喝药。

---

## 2. Act 1 只跑 Overgrowth，Underdocks 半个幕从未训练（headless 写死，非游戏行为）

**状态**：**已确认根因（2026-07-28，反编译 `lib/sts2.dll` 定位）**。**决定：暂不修，等 act2 训练完全收敛后再开放 run 配置**，让两个 act1 区域都进训练分布。

**现象**：所有对局的 Act 1 恒为 **OVERGROWTH** 区域。Underdocks 的全部内容——boss `WATERFALL_GIANT_BOSS` / `LAGAVULIN_MATRIARCH_BOSS` / `SOUL_FYSH_BOSS`，精英 `PHANTASMAL_GARDENERS_ELITE` / `SKULKING_COLONY_ELITE` / `TERROR_EEL_ELITE`，外加 4 weak + 10 regular——**在训练和 dev 里一次都没出现过**。

**证据**：
- 既有实测：300/300 个 A0 Ironclad train seed 的 Act 1 都是 Overgrowth（见 `rl_v2_current_stage.md`）。
- 本次补充探针（共 105 局，全部 `start_run` 后读第 1 层公布的 boss id）：Ironclad 75 局 = 5 个种子命名空间 × 15（dev `m2-a0-ironclad-*` / train `m2-a0-ironclad-train-*` / 纯整数 / 随机词 / 十六进制），另加 Silent / Defect / Necrobinder 各 10 局——**100% OVERGROWTH，零例外**。说明根本不是掷骰子。
- `ModelDb.ActsByIndex` = `[[OVERGROWTH, UNDERDOCKS], [HIVE], [GLORY]]`。**只有 act1 是多区域**，act2/act3 各只有一个区域，不受影响。

**根因**：`RunSimulator.StartRun`（`RunSimulator.cs:271`）调用
`RunState.CreateForTest(players:, ascensionLevel:, seed:)`，**没有传可选的 `acts` 参数**，于是落到
`CreateForTest` 内部的 `(acts ?? ActModel.GetDefaultList())`。两个方法的差别是决定性的：

```csharp
// headless 实际走的
GetDefaultList()   // 每个 act index 取 IsDefault 的那个 —— 确定性，永远 Overgrowth

// 真实游戏走的
GetRandomList(rng, unlockState, isMultiplayer)
    // 收集该 index 下所有 IsUnlocked 的 act
    // 若存在「已解锁但不在 Progress.DiscoveredActs 里」的非默认 act → 强制给它（新内容优先展示）
    // 否则 → rng.NextItem(list2)    ← 在已解锁 act 之间随机
```

即：**两个区域都解锁并发现之后，真实游戏按 run 随机选，约 50/50。always-Overgrowth 是 headless 入口的产物，不是游戏行为。**

**影响**：策略对**约一半的真实 act1 曝光为零**，迁移到真实游戏时这一半会直接翻车。

这同时修正了 `bdedd83`（act1 区域过滤 `act_variant`）的定性：该优化当时的推理是「真实局永远走不到 Underdocks，训练它是浪费」——**这个前提本身是 headless 造成的假象**。在当前 harness 内它仍是正确优化（确实永远遇不到，且把有效训练密度翻倍），但作为通往真实游戏的训练，它把缺口固化了。注意历史上「Waterfall Giant 通关率最低 / 几乎不可赢」的记录来自它还在 boss 池里的时期。

**修复方向**：让 `StartRun` 自己构造 acts —— 用 run seed 派生的 Rng 从 `ModelDb.ActsByIndex[i]` 逐 index 随机取。
**不要直接调 `GetRandomList`**：它依赖 `SaveManager.Instance.Progress.DiscoveredActs` 和 `TestMode.IsOff`，headless 下这两个是 stub，行为不可控；自己按 index 随机既确定（seed 可复现）又干净。建议加协议开关（如 `start_run` 的 `act_variant` / `random_acts` 字段），默认保持现状，便于 A/B。

**为何押后（连带后果，都需要重新校准）**：
1. `detect_act_variant()` 的 8 个探针会开始分歧 → fail-open 保留全池 → `bdedd83` 的「有效训练密度翻倍」直接回退（normal 16→30、mixed 19→36、boss 3→6）。
2. dev 种子集有一半变成 Underdocks，**dev 胜率在改动前后不可比**，曲线会断层。
3. 课程三个阶段的晋级门槛是在单区域池上校准的，需要重定。

**过渡方案（不改 run 生成也能覆盖）**：Underdocks 的遭遇本来就能用 `reset_combat`（`CombatConfig(encounter=...)`）直接开——已实测 `WATERFALL_GIANT_BOSS` 可正常开战并观测到 `Steam Eruption` 逐回合叠加。所以可以先靠 `boss_mix` / `on_policy_boss_replay` 定向注入做曝光，不动主分布。

**优先级**：中。当前 acts_12 run 不受影响（内部一致），但**在追求真实游戏 parity 之前必须解决**。
