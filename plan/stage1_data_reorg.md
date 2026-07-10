> **状态：✅ 已完成 (2026-07-10)。** 这是 Stage 1（数据目录重组）的 plan-mode 设计文档，原样存档（用户要求把每个 Stage 的 plan 都持久化到 `plan/` 目录）。实施结果记录在 `plan/plan.md` 对应日期的 Progress Log 里；路线图状态以 `plan/rl_roadmap.md` 为准。

# STS2LLM 训练路线图：从 v1 到最终目标

## Context

Phase B v1（单场战斗模拟器 + Gym env + MaskablePPO）已经建好并验证通过：铁甲战士单角色、只做单场战斗、28 张卡（来自 spire-codex 转换）、11 只 Act 1 普通怪，训练 loop 跑通、胜率/reward 随训练上升、6/6 单测通过、check_env 通过、500 局随机 fuzz 测试零报错。细节见 `plan/plan.md` 的 Progress Log。

用户现在提出两个诉求，促成这次重新规划：

1. **数据组织方式**：卡牌应该按角色分类（Ironclad / Silent / Defect / Necrobinder / Regent），怪物应该按区域分类（不是简单的 Act 1/2/3，而是 spire-codex 实测数据显示的 4 个区域：**Act 1 - Overgrowth**、**Act 1 - Underdocks**、**Act 2 - Hive**、**Act 3 - Glory**），方便以后分角色/分难度训练。
2. **完整路线图**：从 v1 现状到"最终目标"，在 spire-codex 这个数据源的实际能力边界下，训练计划可以怎么规划。

在回答过程中发现两个改变路线图形状的关键事实：

- **v1 只保留了 28/87 张铁甲战士卡的根本原因，不是转换脚本的 bug，而是 v1 批准的机制集合本身太窄**。59 张被排除的卡里，48 张（29 张"复杂 exhaust/draw-pile/条件触发机制" + 19 张"Power 类持续效果卡"）本质上都需要同一样东西：一个**触发式效果系统**（监听回合开始/结束、抽卌、卡牌被消耗、HP 变化等事件，而不是"打出卡牌立刻结算"这种一次性模型）。这个系统一旦建好，会同时提升**所有**职业的卡牌保留率，不只是铁甲战士——所以它在路线图里的排序很重要：应该在批量转换其他职业之前做，否则以后要返工重新审计一遍。
- **spire-codex 提供的数据远不止卡牌/怪物**。它的 API 路由表（`_spire_codex_readme.txt`）显示还有：`relics`、`potions`、`events`、`encounters`、`orbs`（Defect 的机制）、`afflictions`、`keywords`、`modifiers`、`ascensions`，甚至 `/api/runs/list` 和 `/api/runs/shared/{hash}`——**真实玩家提交的完整跑图记录**（角色、遗物、卡组、种子、结局）。这意味着"完整 run 结构"这个终极目标，不必靠我们自己瞎猜地图生成规则/商店定价/事件概率分布，可以用真实社区数据校准。当然，所有这些内容都受 spire-codex 的 **PolyForm Noncommercial License 1.0.0** 约束（非商业用途，需带版权声明），这个限制会延续到路线图的每一个阶段。

## 路线图：Stage 0 → 最终目标

（完整的 Stage 0-10 表格见 `plan/rl_roadmap.md`，那里是持续更新的权威版本，这里只保留本次 Stage 1 规划时的原始快照。）

## 本次建议实现的范围

鉴于以上是一个多阶段、跨越数周的路线图，不可能一次性做完。建议这次实现 **Stage 1**（数据目录重组）：

- 把 `rl/data/` 从"两个大 JSON 文件"改成按角色/区域拆分的目录结构，例如：
  ```
  rl/data/
    cards/
      ironclad.json   # 现有 28 张，原样迁移
      silent.json     # 占位，标注"未转换"，为 Stage 3 留位置
      defect.json
      necrobinder.json
      regent.json
    monsters/
      act1_overgrowth.json   # 现有 11 只怪物需要先核实分别属于哪个区域再分拣
      act1_underdocks.json   # 占位
      act2_hive.json         # 占位
      act3_glory.json        # 占位
  ```
- 更新 `sim/card.py`/`sim/combat.py` 的 loader，支持指定角色/区域文件（保持向后兼容，默认仍指向 ironclad + 已有怪物，不破坏现有训练脚本）。
- 更新 `_conversion_report.json` 相关的 `convert_from_spire_codex.py`，让它按角色分别输出到 `cards/<color>.json`，并读取怪物的真实 `act` 字段自动分拣到对应区域文件（目前 11 只怪物需要先查一下各自的 encounters.act 字段属于 Overgrowth 还是 Underdocks）。
- 不在这次新增任何卡牌/怪物内容——Stage 2（触发式 Power 系统）和 Stage 3/4（其余职业/区域内容）留到用户明确要继续时再做，因为这两步工作量分别是数量级更大的架构改动，值得单独确认范围后再动手。

这个范围小、风险低，直接满足"方便训练"的组织诉求，同时不用现在就决定"要不要建触发式系统"这种大架构问题。

## Verification

1. 重组后跑一遍现有的 `rl/tests/test_known_interactions.py`（6 条已知交互），确认 loader 改动没有破坏铁甲战士的战斗数值。
2. 跑 `gymnasium.utils.env_checker.check_env()`，确认 env 改动（如果 loader 接口变了）依然符合 Gym API。
3. 跑一次和之前一样的随机 fuzz 测试（500 局，跨所有已迁移的 encounter），确认零报错。
4. 检查新目录结构下 4 个占位职业文件、3 个占位区域文件的 `_meta` 块是否清楚标注"尚未转换，等待 Stage 3/4"，避免以后误以为已经有内容。

---

**实际实现结果**（详见 `plan/plan.md` 2026-07-10 条目）：按计划完成，`rl/data/cards/ironclad.json`（28 张原样迁移）+ 4 个占位职业文件，`rl/data/monsters/act1_overgrowth.json`（10 只）+ `act1_underdocks.json`（1 只，新查出 Seapunk 属于这个区域）+ 2 个占位区域文件。loader 改成接受 `paths` 可迭代对象，默认值保持原有合并池行为不变。验证全部通过。
