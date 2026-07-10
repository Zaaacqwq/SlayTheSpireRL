> **状态：✅ 已完成 (2026-07-09)。** 这是 Phase B v1 里程碑最初批准的 plan-mode 设计文档，原样存档在这里（当时只存在于临时的 plan-mode 文件里，2026-07-10 按用户要求搬进 `plan/` 目录持久化保存）。实施结果记录在 `plan/plan.md` 对应日期的 Progress Log 里；这份文件之后的路线图状态以 `plan/rl_roadmap.md` 为准。

# Phase B (RL 线) — v1 里程碑：单场战斗模拟器 + Gym env + MaskablePPO

## Context

STS2LLM 项目的 Phase A（MCP 交互式打游戏）今天已经端到端验证通过——用 Sonnet 5 模型跑通了一整局铁甲战士，从主菜单一路打到 Act 1 boss（Vantom）通关，全程 ~150+ 次工具调用零报错（细节见 `plan/plan.md` Progress Log）。用户决定跳过 Phase A 第4项（无人值守批量跑图 harness，留待以后需要时再补），直接推进 Phase B：clean-room 训练一个能打 STS2 战斗的 RL 策略，架构上参考 sts2-rl-agent 的公开设计思路（无头模拟器 + Gymnasium env + MaskablePPO），但不抄它的代码（该仓库无 license）。

**关键新发现，修正了 `plan/plan.md` 里 Phase B 的原始假设**：为了确认能不能像计划里写的那样"从 STS2MCP 的 MIT 授权接口 `get_compendium`/`search_wiki` 引导出卡牌/遗物/怪物/能力数据"，我直接调用了这两个接口做了实测：

- `get_compendium`：不是卡池数据库，是**当前 profile 的进度追踪器**（已发现的卡/遗物/药水 ID 列表 + 战绩统计 + run 历史）。Bestiary 段落明确标注 `"status": "locked_in_ui"`，只有对局胜负统计，**没有任何怪物属性数据**（HP、意图、技能）。
- `search_wiki`：**能**返回完整规则文本（base + upgraded 的 cost/description/keywords），但 `"scope": "active_profile_discovered_cards_and_relics"`——**只覆盖当前 profile 已经在游戏里遇到过的卡和遗物**，不是全卡池。目前这个 profile 已发现 55 张卡、26 个遗物（够铺一个铁甲战士战斗模拟器的基础卡池）。`item_type` 只支持 `card`/`relic`，**没有 `monster` 或 `potion`**。

结论：怪物数据在这两个接口里完全没有来源。唯一干净（clean-room）的数据来源是**本次交互验证 session 里亲手打过的真实对局**——那 9 场战斗的 `get_game_state` 记录（怪物 HP、attack 数值、debuff 效果等）是我们自己通过合法游玩观察到的第一手事实，不是抄来的。这正好对应 `plan.md` Verification 部分本来就写好的思路："与真实对局的 `get_game_state` 输出交叉核对，而非照抄逻辑"。

因此这版计划把 Phase B v1 的范围收窄为：**只做单场战斗** RL 环境（不做地图/商店/事件/选卡奖励），用观察到的真实数据做怪物和验证锚点。这是用户在澄清问题里明确选择的范围（相对于"直接做完整 run 结构"）。

## 推荐方案

### 范围（v1）
- 单一角色：铁甲战士（Ironclad）。
- **只做单场战斗**，不做地图导航/商店/事件/卡牌奖励选择——这些留给后续里程碑。
- 卡池：铁甲战士已发现的基础出装（Strike/Defend/Bash）+ 精选常见 Attack/Skill 卡（约 15-20 张），规则文本来自 `search_wiki(item_type="card")` 的实测输出，落盘成静态 JSON，不在训练循环里反复调 API。跳过 Power 类卡和高复杂度随机效果卡（Catastrophe、Panache、Mayhem 等）——这些效果需要"打出抽牌堆顶部的牌"之类的递归逻辑，v1 先不碰。
- 怪物：手工整理 5-8 只本次亲测过的 Act 1 怪物（Shrinker Beetle、Fuzzy Wurm Crawler、Inklet、Cubex Construct、Ruby Raider 三人组、Nibbit），数值来自本 session 里真实 `get_game_state` 观测记录，在数据文件里明确注明"observed data, not exhaustive, no ascension scaling"。
- 不做：遗物、药水、地图/事件/商店、多角色、更高难度层级（ascension）。按 plan.md 原有的分层思路留给 v2+。

### 机制覆盖
- 回合结构：能量/出牌/回合结束，抽牌堆/弃牌堆/消耗堆，抽牌到手牌上限。
- 资源：HP、格挡（block）、Strength、Dexterity。
- Debuff/Buff：Vulnerable（+50% 受到伤害）、Weak（-25% 造成伤害）、Frail（-25% 格挡获取）——这三个是本次实测中反复用到、且效果确定的。
- 伤害结算顺序按本次实测数值对齐（例如 Bash 命中后接 Strike+，在 1 层 Vulnerable 下算出 13 点伤害——这条真实观察到的数据将直接用作单测锚点）。

### 文件结构（新建，均在 `rl/` 下）
```
rl/
  data/
    cards_ironclad.json         # search_wiki 实测拉取并落盘的卡牌规则文本
    monsters_act1_sample.json    # 手工整理的怪物属性表，注明数据来源和局限
  sim/
    __init__.py
    card.py                      # Card 数据类 + 效果解释器
    combat.py                    # 单场战斗状态机：回合结构、出牌、结算、终止判定
    powers.py                    # Strength/Dexterity/Vulnerable/Weak/Frail 实现
  env.py                         # Gymnasium Env 包装 combat.py，含 observation/action space + action mask
  train.py                       # MaskablePPO 训练入口脚本
  tests/
    test_known_interactions.py   # 用本 session 亲历的战斗数据做 cross-check 单测
  pyproject.toml                 # 独立依赖，参考 mcp/pyproject.toml 的 uv 管理方式，不共享虚拟环境
```

新增依赖：`gymnasium`、`stable-baselines3`、`sb3-contrib`（MaskablePPO 在这里）、`numpy`、`pytest`。

### 训练可复现性
`rl/train.py` 固定使用种子 `LFLFWUJ8KS`（字符串本身转成整数派生，如 `int(hashlib.sha256(b"LFLFWUJ8KS").hexdigest(), 16) % (2**32)`）作为模拟器/Gym env 的随机数种子，保证每次训练跑（怪物出手顺序、卡组抽取顺序等模拟器内部随机性）可复现对比。这是我们自己模拟器的 RNG 种子，跟真实 STS2 存档的 run seed 系统无关——不涉及地图/敌人序列（v1 没有地图）。

### 复用的现有产出
- `search_wiki` / `get_compendium` 的实测输出（已在本次 plan 阶段抓取过样例，卡牌规则文本可直接复用）。
- 本次交互验证 session 的战斗记录（怪物 HP/attack 数值、伤害结算结果）作为怪物数据和单测的事实来源。
- `mcp/pyproject.toml` 的 uv 项目结构作为 `rl/pyproject.toml` 的参考范式。

## Verification

1. `rl/tests/test_known_interactions.py`：至少 2-3 条从本次真实对局摘出的已知交互（如"Bash 接 Strike+，1 层 Vulnerable 下应为 13 点伤害"）跑模拟器算出结果，与当时观察到的真实数字比对，pytest 断言一致。
2. `rl/env.py`：跑 `gymnasium.utils.env_checker.check_env()`，确认符合 Gym API 规范，无报错。
3. `rl/train.py`：跑一次小规模训练（几万步量级），观察 episode reward / 胜率随训练步数是否上升——不要求这次收敛，只验证训练循环本身能跑通且指标在改善。
4. 全程保持 clean-room：不查阅/借鉴 sts2-rl-agent 的任何代码；数值来自 `search_wiki` 实测输出或本 session 亲测记录，不逐行照搬任何第三方实现。

---

**后续实际发生的范围调整**（记录在 `plan/plan.md` Progress Log，未回填进上面的原始设计）：卡池数据源后来从 `search_wiki` 实测输出换成了 spire-codex API 转换（见 `plan/plan.md` 2026-07-10 spire-codex 决定条目），18 张手工卡变成经审计的 28 张自动转换卡；怪物也从纯手工整理扩展为"8 只亲测 + 3 只 spire-codex 自动转换"的混合来源。这些调整发生在 Stage 1 之前，Stage 1 的重组工作基于的就是这个演化后的 28 卡/11 怪物基线。
