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

---

## 3. ✅ on-policy boss replay 缓冲区 256/256 全是 act1 boss（已修复）

**状态**：**已修复（2026-07-28，`tools/m2_train.py`）**，已部署（`m2_v7_acts12_hpshape` iter 2585 重启）。

**现象**：`boss_mix=0.15` 每迭代拿 14 局专门重打 boss，`boss_replay_win_rate` 常年 0.79–0.93 看着很健康；但仪表盘新增的逐遭遇胜率显示 act2 三个 boss 只有 58–67%。

**证据**：直接读 `on_policy_boss_loadouts.json`（iter 2578）：
```
VANTOM_BOSS 93 (36%) · CEREMONIAL_BEAST_BOSS 84 (33%) · THE_KIN_BOSS 79 (31%)
```
**256/256 全是 act1 boss，act2 一个都没有。** 所以 `boss_replay_win_rate` 量的一直是 act1 boss 的保持率（本来就 ~95%）。

**根因**：`update_on_policy_boss_buffer` 对每局在**第一个** boss 快照上 `break`（docstring 写的就是 "one snapshot per run"）。每个走到 act2 boss 的局都必然先经过 act1 boss，所以 act2 快照永远进不了缓冲区。次要原因：`del buffer[:-max_size]` 这种平铺滚动窗口天然被出现频率最高的 boss 占满，即使收集修好了也会重新倾斜。

**影响**：15% 的采样预算长期花在**已经打赢 95%** 的三场仗上，而真正缺的三场（58–67%）一次定向训练都没吃到。参考 2026-07-18 的 act1 调参结论：boss_mix 是 act1 阶段唯一真正起效的杠杆，机制本身有效——只是瞄错了目标。

**修复**：收集改成**每局每个 boss 各一个快照**（用 `captured` 集合去重同一场 round-1 的多个 `combat_play` 决策，替代原来的 `break`）；保留改成 `trim_boss_buffer`——按 encounter 分层淘汰，每个 boss 拿 `max_size // n` 的配额，不足配额的余量再补给最新的。

**验证**：拿真实录制局回放（从线上那份 256/256 act1 缓冲区起步），3 个迭代批次后收敛到 `42/42/42/42/42/46` 六 boss 均分。线上重启后 3 个迭代实测：`THE_KIN 54 · VANTOM 47 · CEREMONIAL_BEAST 42 · KAISER_CRAB 42 · THE_INSATIABLE 42 · KNOWLEDGE_DEMON 29`。测试 `test_a_run_contributes_every_boss_it_meets_not_just_the_first` / `test_retention_gives_every_boss_a_share_instead_of_the_most_common_one`。

---

## 4. ✅ `remove_bonus` 奖励的是「打开删牌界面」而不是「删掉牌」（已修复）

**状态**：**已修复（2026-07-28，`rl/src/sts2rl/ppo.py`）**，随上条一起部署。

**现象**：act2 boss 开局牌组中位数 24–25 张（act1 boss 时 18 张），10 个遗物、80/80 满血——**不是被磨死的，是牌组越来越肥**。而 `remove_card` 100 个迭代里被提供 28,863 次、被选择 14 次（0.05%），是又一个准结构性零（形状同当年的 `use_potion`）。本来打算直接把现成的 `--remove-bonus` 开起来。

**根因**：引擎的 `remove_card` **只负责打开删牌界面**——`DoRemoveCard` 发现 `_cardSelector.HasPending` 就直接 `return DetectDecisionPoint()`，此时**一分钱没扣、一张牌没删**；gold 和牌是在随后的 `select_cards` 解决时才走的（实测 4 例录制局：`remove_card → card_select → select_cards →` 下一步 deck −1、gold −75）。而商店删牌**不是敌方强制**的选择，`DoSkipSelect` 会 `CancelPending()` 把它取消掉。

原实现 `if config.remove_bonus and action.action == "remove_card"` 把奖励挂在按钮上，等于教策略：**开界面 → 取消 → 白拿 bonus、留着牌、省下 75 gold**——比真删牌严格更优。

**修复**：改成挂在**实际观测到的牌组缩水**上（`_removed_a_card`：前后两个决策的 `deck_size` 下降）。无法被取消路径薅到，顺带也覆盖事件删牌。对没有牌组字段的界面（地图/部分事件）返回 None 而不是 0，否则每次切进这类界面都会误判成删了牌。测试 `test_remove_bonus_pays_for_the_removal_not_for_opening_the_screen`（完成 vs 取消两条路径）/ `test_remove_bonus_ignores_screens_that_do_not_report_the_deck`。

**教训（与上面 KNOWLEDGE_DEMON 的强制选择那条同类）**：给某个**动作**发奖励之前，先确认这个动作在引擎里是否原子完成。协议里好几个动作只是「打开一个 pending 选择」（`remove_card`、事件选项、`buy_relic` 的部分路径），奖励应当挂在可观测的**状态变化**上，而不是挂在动作名上。

**线上取值**：`--remove-bonus 0.1`。一局最多 ~1.6 次商店（`leave_room` 15,699/9,600 局），所以上限 ~+0.16，远低于 ±1 的终局奖励；重启后 3 个迭代 `reward_health inverted=False`（win 1.75–1.78 / loss −0.46～−0.61）。**待观察**：奖励现在落在 `select_cards` 那一步，需要靠 value function 往回传一步才能让 `remove_card` 变得有吸引力，比挂按钮上慢；盯 50–100 个迭代内 `remove_card` 选择率是否离开 0。

---

## 5. ✅ 战斗中 `player.deck` 不是牌组 —— Power 牌在场时会从里面消失（已修复）

**状态**：**已修复（2026-07-28，`rl/src/sts2rl/ppo.py`）**。这是本次自己引入又当天抓到的回归，`remove_bonus` 只带着这个 bug 跑了约 20 个迭代（2695–2700）。

**现象**：把 `remove_bonus` 改成「按观测到的牌组缩水发奖励」之后，抽查 iter 2700 的录制局，发现 10 次缩水里有 **4 次发生在战斗中的 `end_turn` 上**。

**证据**（iter 2700 录制局逐步）：
```
step 216 combat_play Monster deck=21 action=play_card
step 217 combat_play Monster deck=21 action=end_turn      ← 离开牌组的是 CARD.INFLAME
step 218 combat_play Monster deck=20 action=play_card
step 222 card_reward Monster deck=20
step 223 map_select  Map     deck=21                       ← 战斗结束后又回来了
```
TAUNT / INFLAME / DOMINATE 三张都复现：**Power 牌一旦打出进入场上，就会暂时从 `player.deck` 里消失，战斗结束后回来**。

**为何之前的验证没抓到**：早先只抽查了一局战斗里 `deck_size` 恒为 15，就归纳成「战斗中牌组稳定」。那一局恰好没打 Power 牌。**单局样本不足以支撑「不变量」这种结论**。

**影响**：`remove_bonus=0.1` 变成「每打出一张 Power 牌 +0.1」——一个可重复、每场战斗都能领的奖励，而且方向上直接鼓励打 Power 牌。`deck_shaping_coef` 的势函数也会跟着一凹一凸（PBRS 会自己抵消，无净偏差，但是噪声）。

**修复**：新增 `_in_combat(state)`（room_type ∈ Monster/Elite/Boss），三处一起挡掉：
- `_removed_a_card` / `_upgraded_a_card`：战斗中的牌组增量一律忽略；
- `_deck_potential`：战斗中保持上一帧的值（和 `_hp_potential` 对无 HP 界面的处理同构），势函数不再随 Power 上下场抖动。

顺带修掉 `smith_bonus` 的另一个误触发：同一批录制局里 `buy_relic` 一步让升级数 +2、`select_map_node` 一步 +2（遗物/事件送的升级），也会被算成「休息点升级」。现在额外要求 `room_type == "RestSite"`。

**测试**：`test_deck_deltas_are_ignored_inside_a_fight`（直接钉住 21→20→21 这个真实轨迹）/ `test_smith_bonus_only_counts_an_upgrade_made_at_a_rest_site`。

**教训**：奖励挂到「状态变化」上比挂到动作名上安全（见第 4 条），但前提是**那个状态字段真的是你以为的语义**。上线前应当扫一遍真实录制局里该字段的所有变化点，而不是只看它在预期路径上对不对。

---

## 6. ✅ boss 重放池只有 act1 —— act2 存档被重定向去打 act1 boss（已修复）

**状态**：**已修复（2026-07-29，`tools/m2_train.py`）**。这条把第 3 条的修复补完了：缓冲区确实修好了，但**重放从来没到过 act2**。

**现象**：第 3 条修复上线后跑了 70+ 个迭代，act2 boss 胜率纹丝不动（KAISER_CRAB 52→58%、Knowledge Demon 71→69%、Insatiable 55→52%，全在噪声内）。当时记为「修好了但没用」。

**根因**：`m2_train` 里重放阶段的遭遇池写死了 act1：

```python
boss_encounters = tuple(sorted(
    str(row["id"]) for row in catalog
    if row["act"] == 1 and row["category"] == "boss"      # ← 只有 act1
    and (act_variant is None or str(row.get("act_id")) == act_variant)
))
```

而 `curriculum.episode_config` 在 loadout 自带的遭遇不在池子里时会**重新抽样**：

```python
encounter = (loadout.encounter if loadout.encounter in stage.encounters
             else sample_encounter(stage, seed))
```

两者相乘的结果：缓冲区里 126/256 个 act2 boss 快照（21 张牌的 act2 中期存档）被拿去打 **act1 boss**。

**证据**：`boss_replay_win_rate` 六个窗口 0.886 / 0.920 / 0.900 / 0.916 / 0.921 / 0.937，与 act1 boss 的 dev 胜率（88.8–93.8%）严丝合缝，与 act2 的 50–64% 毫无关系。缓冲区本身是均衡的：`THE_KIN 46 · KNOWLEDGE_DEMON 42 · THE_INSATIABLE 42 · KAISER_CRAB 42 · VANTOM 42 · CEREMONIAL_BEAST 42`。

**修复**：新增 `replay_encounters(base, buffer)` = 引导池 ∪ 缓冲区里出现过的所有遭遇。**每个迭代重算**而不是启动时算一次——缓冲区会持续发现新遭遇（act2 boss 要等某局真的走到才会进来），启动时冻结的池子照样会重定向它们。

**验证**：拿线上真实缓冲区模拟 2000 次 `episode_config` 抽样，重放切片里 act2 的占比 **0% → 49%**（六个 boss 各 15–18%）。测试 `test_replay_pool_covers_the_buffer_so_act2_snapshots_are_not_redirected` / `test_replay_pool_keeps_the_bootstrap_encounters_while_the_buffer_is_empty`。

**教训**：修好了「喂什么数据」还不够，得同时确认「数据喂到哪里」。第 3 条只验证了缓冲区的构成，没有验证重放实际打的是什么——而后者有独立的、写死的过滤器。一个遥测指标（`boss_replay_win_rate`）当时就已经在报警了，数值精确等于 act1 的难度，只是没人拿它和 act2 对照过。

**精英尚未覆盖**：`context.boss` 是**本幕的 boss** 而不是当前战斗（Elite 房间里报 `THE_KIN_BOSS`，实际打 `BYGONE_EFFIGY`），而引擎 catalog 只有 `{act, act_id, category, id}`、没有怪物组成，无法从敌人反查遭遇 id。要采集精英存档需要一个启动探测：对每个精英 id 调一次 `start_combat`，记录敌人签名。act2 是单一区域 `HIVE`，3 boss + 3 elite（`DECIMILLIPEDE_ELITE` / `ENTOMANCER_ELITE` / `INFESTED_PRISMS_ELITE`）。
