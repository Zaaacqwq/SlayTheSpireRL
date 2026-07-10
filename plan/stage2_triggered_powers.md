> **状态：✅ 已完成 (2026-07-10)。** 这是 Stage 2（触发式 Power 效果系统）的 plan-mode 设计文档，原样存档（用户要求把每个 Stage 的 plan 都持久化到 `plan/` 目录）。实施结果记录在 `plan/plan.md` 对应日期的 Progress Log 里；路线图状态以 `plan/rl_roadmap.md` 为准。

# Stage 2：触发式 Power 效果系统

## Context

`plan/rl_roadmap.md` 里 Stage 2 的定位是"所有后续阶段的地基"：v1/Stage 1 只支持"打出卡牌立刻结算"的静态效果模型，导致 48/59 张被排除的铁甲战士卡（19 张 Power 类持续效果卡 + 29 张需要"本回合内条件判断"或"卡牌消耗触发"的复杂机制卡）全都因为同一个缺失能力被挡在外面：**没有一个"事件发生时自动触发效果"的系统**。

我把这 48 张卡的真实文本全部抓出来读了一遍（`rl/data/_stage2_scoping_dump.txt`），归类出实际需要的触发点其实就那么几种，而且很多卡是同一种触发点的不同数值组合。基于这个归类，这次 Stage 2 实现的目标是：**建好这套触发系统的核心架构，并用它把一批有代表性、机制单一明确的卡重新纳入卡池**，同时把机制上明显更复杂、风险更高的一批卡（会引入全新的、独立的子系统）显式排除并记录原因，留给下一轮。这跟 Stage 1 时对 87 张卡做审计的节奏一致：先把干净的部分做对，模糊/危险的部分宁可排除也不要猜。

现有架构（`sim/combat.py`）：`PlayerState`/`EnemyState`（dataclass）、`Combat._apply_effect()`（一个按 `effect["type"]` 分派的 if/elif 链，纯函数式，读 `self.player`/`target` 状态并直接修改）、`Combat._start_player_turn()`（回合开始重置 block/debuff/能量/抽牌）、`Combat.play_card()`（扣费→逐个执行 effects→移出手牌→进弃牌堆或消耗堆）。卡牌出牌自消耗（`self_exhausts`）和 `exhaust_random_from_hand` 效果目前各自独立地把卡塞进 `exhaust_pile`，没有一个统一入口——这是本次要重构掉的一个具体点，因为"卡牌被消耗时触发"这个新钩子需要一个唯一入口才能不漏判。

## 架构设计

### 状态扩展
- `PlayerState.powers: dict[str, dict]`——本场战斗内**永久生效**的 Power（Barricade/Cruelty/Demon Form 等），战斗开始后清空，不随回合重置。value 是"triggers"字典（事件名→效果列表，或特殊键 `"magnitude"`→int，给 Cruelty 这类连续检查的数值型被动用）。
- `PlayerState.temp_powers: dict[str, dict]`——**本回合内生效**、每次 `_start_player_turn()` 清空的 Power（预留给 Rage 这类"本回合内每次打出Attack触发"的卡，这次不实现 Rage 本身，但字段先建好，作为后续同类卡的落点）。
- `PlayerState` 新增回合内布尔/计数标记，均在 `_start_player_turn()` 重置：`exhausted_a_card_this_turn`、`lost_hp_this_turn`、`first_block_gain_used_this_turn`（给 Unmovable）、`no_draw_this_turn`（给 Battle Trance）。
- `EnemyState.temp_strength_penalty: int = 0` + `effective_strength` property（`strength + temp_strength_penalty`，仿照 `PlayerState.effective_strength` 现有写法），在该敌人自己回合结束后重置（给 Mangle）。`_run_enemy_turn()` 里 `enemy.strength` 的伤害计算调用点要改成 `enemy.effective_strength`。
- `CardDef.on_exhaust_effects: tuple[dict, ...] = ()`（`sim/card.py`）——某张具体卡**自己**被消耗时触发的效果（跟"任何卡被消耗"的持续 Power 触发是两回事），给 Drum of Battle 这类卡用。

### 统一消耗入口
新增 `Combat._exhaust_card(card_instance)`，替换掉 `play_card()` 里 `self_exhausts` 分支和 `_apply_effect` 里 `exhaust_random_from_hand` 分支各自直接操作 `exhaust_pile` 的写法。这个方法做三件事：把卡从原来的堆移到 `exhaust_pile`；设置 `exhausted_a_card_this_turn = True`；依次触发"任意卡被消耗"的持续 Power（Dark Embrace/Feel No Pain）和这张卡自己的 `on_exhaust_effects`（Drum of Battle）。

### 通用触发分发
`_fire_power_effects(event: str)`：遍历 `player.powers`/`temp_powers`，若某个 power 的 triggers 字典里有对应 event 键，就把它的效果列表逐条丢给现有的 `_apply_effect()` 执行（复用，不重复造轮子）。触发点及调用位置：
- `_start_player_turn()`（block 重置之后、抽牌之前）→ 回合开始类：Demon Form/Pyre/Crimson Mantle/Aggression/Inferno 的失血那一半。
- `_exhaust_card()` → 消耗类：Dark Embrace/Feel No Pain。
- `_apply_effect()` 的 `lose_hp` 分支末尾（战斗中只有玩家回合会触发 lose_hp，天然满足"本回合内"）→ 失血类：Rupture/Inferno 的伤敌那一半。
- `_apply_effect()` 的 `block` 分支（计算格挡量之前先查 Unmovable 特殊加倍，加完格挡后再触发列表类）→ 获得格挡类：Juggernaut。Unmovable 因为是"修改本次获得量"而不是"额外触发一个效果"，单独 if 分支特殊处理，不走通用 dispatch。
- `_apply_effect()` 的 `apply_vulnerable`/`apply_vulnerable_all` 分支末尾 → 施加虚弱类：Vicious。

### 被动伤害修正（不是事件触发，是公式修正）
新增 `Combat._player_attack_damage(base_amount, target)` 包一层 `powers.compute_attack_damage`，把现在 `_apply_effect` 里 5 处几乎相同的伤害计算调用全部换成这个 helper——顺手做一次去重，同时是加 Cruelty（目标有 Vulnerable 时伤害再乘 1.25，magnitude 从 `player.powers["CRUELTY"]["magnitude"]` 读，升级后是 50）的唯一改动点。Tank（受到伤害翻倍）同理包一层 `_damage_player`。

### 新增通用效果类型（`_apply_effect` 里加 elif 分支）
- `gain_power` `{power_id, triggers?}`——Power 卡自己的效果，写入 `player.powers`。
- `conditional` `{if, then, else}`——本回合条件判断的通用包装，`if` 是个已知字符串（`exhausted_this_turn`/`lost_hp_this_turn`/`exhaust_pile_size_gte_3`），`then`/`else` 是效果列表，递归调用 `_apply_effect`。这一个类型顶 Evil Eye/Forgotten Ritual/Spite/Pact's End 四张卡，以后其他职业的"本回合如果…"类卡也能复用。
- `damage_random_enemy` `{amount}`——Juggernaut 用，内部自己挑 `alive_enemies()` 里随机一个。
- `damage_scales_with_pile_count` `{pile, base, per_card}`——Ashen Strike 用（`pile="exhaust_pile"`），跟已有的 `damage_scales_with_tag` 是同一种模式换个计数来源。
- `enemy_lose_strength_this_turn` `{amount}`——Mangle。
- `double_target_vulnerable`——Molten Fist。
- `set_no_draw_this_turn`——Battle Trance（配合 `draw` 分支检查这个标记）。
- `return_random_attack_from_discard_upgraded`——Aggression。
- `exhaust_hand_and_damage` `{per_card}`——Fiend Fire：一次性把整手牌消耗掉（逐张走 `_exhaust_card`）+ 按消耗数量算伤害，原子操作，不依赖"消耗堆总数"（跟 Ashen Strike 的"终身消耗堆大小"是两个不同的计数语义，不能共用同一个效果类型）。
- `exhaust_hand_filtered_and_block` `{filter, per_card}`——Second Wind（`filter="non_attack"`）。
- `exhaust_random_attack_and_add_damage` `{base, hits}`——Thrash：从手牌随机选一张 Attack 消耗掉，读它的基础伤害加到自己身上一起打出去。
- `draw_until_non_attack`——Pillage。

### env.py 观测空间
`PLAYER_FEATURES` 加一小块"当前激活的 Power"标志位（覆盖这次要支持的 power_id 集合，预留几个空位给以后的卡），因为像 Barricade/Cruelty/Feel No Pain/Juggernaut/Vicious 这类卡的效果不会体现在现有的 hp/block/strength 等数值特征里——不加的话训练出来的策略根本"看不见"自己有没有这些被动效果在生效。`OBS_SIZE` 相应增大，`_build_observation()` 加对应几行。

## 这次纳入的卡（27 张，按触发机制分组）

| 机制 | 卡 |
|---|---|
| 回合开始触发 | Demon Form、Pyre、Crimson Mantle、Barricade、Aggression、Inferno（失血部分） |
| 消耗任意卡触发（持续 Power） | Dark Embrace、Feel No Pain |
| 消耗**自己**触发 | Drum of Battle |
| 本回合失血触发 | Rupture、Inferno（伤敌部分，跟上面共用一张卡） |
| 获得格挡触发 | Juggernaut、Unmovable |
| 施加 Vulnerable 触发 | Vicious |
| 被动伤害公式修正 | Cruelty、Tank |
| 通用条件包装 | Evil Eye、Forgotten Ritual、Spite、Pact's End |
| 消耗堆计数缩放 | Ashen Strike |
| 新简单效果类型 | Mangle、Molten Fist、Battle Trance |
| 原子批量操作（消耗+算伤害/格挡，不依赖事件系统） | Fiend Fire、Second Wind、Thrash、Pillage |

铁甲战士卡池会从 28 张涨到 55 张。

## 明确排除、留到下一轮（Stage 2b）

这些卡各自需要一个独立的新子系统，跟上面"复用同一套小机制"的性质不同，硬塞进这次容易埋雷：
- **递归自动出牌**（Havoc/Stampede/Howl from Beyond/Hellraiser）——需要不经过手牌索引、程序化调用 `play_card` 等价逻辑的能力，涉及自动选目标、嵌套触发，风险最高。
- **动态费用**（Corruption"技能费用变0"、Stomp"费用随本回合出的Attack数减少"）——现在 `CardInstance.cost()` 是纯函数，改成依赖实时战斗状态是接口级改动，会牵动 `legal_hand_indices()`/动作合法性判断。
- **卡牌实例级持久状态跨多次使用**（Rampage"这张卡这场战斗内伤害递增"）——需要 `CardInstance` 携带可变机制状态，是个新概念，值得单独验证。
- **"下一张牌"待定修饰符**（One Two Punch/Unrelenting/Rage）——需要一个"下次打出X类型的牌时消费掉"的队列机制，是另一个独立小子系统。
- **生成新卡实例**（Anger 自我复制、Stoke、Infernal Blade）——沿用一直以来的政策，不实现。
- **全新未充分定义的机制**（Stone Armor 的"Plating"）——文本信息不够描述清楚具体规则，宁可不猜。
- **场外无意义**（Feed 加 Max HP——v1 单场战斗没有跨战斗的 Max HP 概念，做了也看不出效果）。
- **玩家选择目标的消耗**（Brand/Burning Pact，Stage 1 已排除，原因不变）。

## 文件改动

- `rl/sim/combat.py`：核心架构改动（上述状态扩展、`_exhaust_card`、`_fire_power_effects`、新效果类型 elif 分支、各触发点接线）。
- `rl/sim/card.py`：`CardDef.on_exhaust_effects` 字段 + `load_card_defs()` 读取。
- `rl/data/convert_from_spire_codex.py`：为这 27 张卡在 `build_effects()` 里加 bespoke 分支（跟现有 `SIMPLE_BESPOKE_CARDS` 模式一致），从 `COMPLEX_MECHANIC_PATTERN`/Power-type 排除逻辑里把它们摘出来；重跑后人工核对每张卡生成的 effects 是否跟 `source_description` 一致（沿用 Stage 1 时反复验证过的审计流程）。
- `rl/data/cards/ironclad.json`：重新生成，28 → 55 张。
- `rl/env.py`：观测空间加 Power 标志位。
- `rl/tests/test_known_interactions.py`：新增几条针对新机制的单测（例如 Demon Form 连续两回合的 Strength 累加、Cruelty 对 Vulnerable 目标伤害的乘算顺序、Unmovable 每回合只翻倍一次）。

## Verification

1. `pytest rl/tests/`：全部通过，包含新增的机制单测。
2. `gymnasium.utils.env_checker.check_env()`：观测空间维度变化后依然合规。
3. 扩到 55 张卡 + 11 只怪物的随机 fuzz 测试（跟之前一样跑 500 局），零报错。
4. 对新纳入的 27 张卡逐一人工核对生成的 effects 是否忠实于 `source_description`（Stage 1 的审计经验是：静态字段抽取容易漏掉二级机制，这次改成"事件触发+条件包装"以后风险点变成"触发时机/清空时机对不对"，要单独盯这个）。
5. 完成后更新 `plan/rl_roadmap.md`：Stage 2 状态改为已完成，更新日志加条目。

---

**实际实现结果**（详见 `plan/rl_roadmap.md` 2026-07-10 Stage 2 条目和 `plan/plan.md` 对应条目）：按计划完成，27 张卡全部纳入（0 张意外被排除）。实施中额外发现并修复两个真实 bug：Drum of Battle 的 on-exhaust 效果需要升级感知（加了 `on_exhaust_effects_upgraded` 字段），Rupture 的升级 key 大小写/命名跟其他卡不一致导致升级加成没生效。验证：单测从 6 条扩到 11 条（全部通过）、check_env 通过（观测空间 98→112 维）、1000 局 fuzz 测试零报错（原计划 500 局，实际跑了更大规模）、外加一次 8000 步训练冒烟测试确认 MaskablePPO 在新观测空间下能正常训练（原计划未要求，属于额外验证）。
