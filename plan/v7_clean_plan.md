# M2 v7-clean：可见性契约与大模型训练计划

状态：**P1–P3 已完成，P4 短程对照待开始**  
开始日期：2026-07-14  
目标：保留 v7 的 `hidden=256 / layers=4 / heads=8` 大模型方向，但在重新训练前消除动作不可达、候选碰撞和关键状态不可见问题，并把这些问题变成可自动阻断训练的契约。

## 背景与旧 v7 结论

旧 run `m2_v7_h256` 于 iteration 123 主动停止，以应用 Windows 15.625ms 定时器/攒批吞吐修复。该 run 不能继续作为正式训练链：对其 704 个 episode artifact、58,301 个决策的审计发现，模型和协议仍有与 v1–v5 同类的结构性盲区。

旧 checkpoint 只保留作回归对照，不迁移到 v7-clean。动作特征维度、实体类型和候选绑定都会改变，v7-clean 从新初始化开始。

## 审计证据

| 缺口 | v7 真实轨迹证据 | 影响 |
|---|---:|---|
| 商店动作不可达 | 304 个 shop state、3,952 件商品，购买/删卡候选为 0 | 只能 `leave_room` |
| 多选 card-select 候选碰撞 | 193 个多选 state；7,919 个候选表示完全相同 | 升级、删除、变换、多选近似随机 |
| bundle 内容不可见 | 64 个 state、128 个 bundle 全部为 `UNK` | 只能按序号选包 |
| 目标敌人未绑定 | 约 118,628 个定向出牌候选只有裸 `target_index` | 同一张卡对不同敌人缺少目标语义 |
| power 信息丢失 | 23,800 个 state 有敌人 power，22,186 个有玩家 power | 敌人 power 未提取，power amount 未编码 |
| intent 类型丢失 | 轨迹包含 Attack/Buff/Debuff/Defend/Summon/Sleep 等十类 | 非攻击意图大多不可区分 |
| 目标实际伤害丢失 | 23,324 次 `damage_by_target` 与 `stats.damage` 不同 | 易伤、虚弱、目标差异后的伤害不可见 |
| 地图与 boss 视野不足 | observation 忽略 `context.boss`，引擎只返回下一步节点 | 路线、组牌和资源无法针对未来 |
| 卡牌改造丢失 | 6,472 个 state 含 enchantment/affliction | 改造卡按普通卡处理 |
| 告警未接线 | `offered_actions()`、`consume_warnings()` 未进入训练 loop | 新盲区仍会静默存在 |

## P0：冻结旧 run

- [x] 停止 `m2_v7_h256`，保留 artifact/checkpoint。
- [x] 旧 v7 标为旧可见性契约，不用于正式结果。
- [x] timeline 记录停止原因、最佳指标和审计结论。
- [ ] 新 run 使用 `m2_v7_clean_init0`，不覆盖旧目录。

## P1：引擎与动作契约

- [x] shop item 输出稳定 `id`、`affordable`、`is_stocked`；shop 输出 `can_remove_card`。
- [x] 动作表加入 `buy_card`、`buy_relic`、`buy_potion`，真实引擎验证购买和删卡路径。
- [x] bundle 内卡牌输出稳定 id，并保留 bundle 所属关系。
- [x] card-select 单选绑定 card token；多选使用所选实体集合表示，禁止逗号字符串塌缩为同一标量。
- [x] 定向卡牌/药水同时绑定 primary entity 与 target enemy entity。
- [x] 所有不同合法候选在同一状态中必须具有不同的语义表示。

## P2：观测契约

- [x] `context.boss` 作为一等实体。
- [x] 地图输出并编码可见的完整节点/边，而非只有下一步房型。
- [x] 敌人/player powers 编码稳定 id、amount 与 owner。
- [x] intents 编码类型、伤害与 owner。
- [x] orbs 编码稳定 id、passive、evoke、slot。
- [x] bundle cards、enchantment、affliction 与 `damage_by_target` 可见。
- [x] event option 动态 vars 使用稳定键与有界数值；随机卡另输出稳定 `RandomCardId`。
- [x] 13 维 global features（含 `orb_slots`）归一化并对 NaN/极值 fail-fast。

## P3：可见性防火墙

训练前生成 `visibility_audit.json`，硬门槛：

- [x] 所有观察到的 decision 均可解析，所有合法动作均可编码。
- [x] candidate collision = 0。
- [x] 需要实体的动作 pointer coverage = 100%。
- [x] 稳定实体 `UNK = 0`（当前审核语料无 allowlist）。
- [x] unknown state field / unknown entity / 非有限 feature 自动报错并阻止训练。
- [x] shop、bundle、多选、目标敌人的 counterfactual 测试通过。
- [x] `offered_actions` 与 `chosen_actions` 每轮同时落盘；never-offered/never-chosen 单独报警。
- [x] reward health 反转时在 PPO 更新前硬停；胜局回报严格高于败局的回归测试通过。

### P3 验收结果（2026-07-14）

- 词表由完整 ModelDb 目录、确定性真实引擎 sweep 和已审核 smoke artifact 生成：2,296 个稳定实体；卡牌 579、敌人 102、遗物 298、药水 66、power 273、全部 24 enchantment、10 affliction、常规及事件专用 orb。
- `rl/runs/v7_clean_visibility_audit.json` 汇总 285 个 episode、17,366 个决策：15 类动作全部 offered 且至少 chosen 一次；candidate collision、pointer miss、unknown field、unknown entity、non-finite feature 和 violations 全为 0。
- 最终 `hidden=256 / layers=4 / heads=8` 更新 smoke（`m2_v7_clean_update_smoke_final`）在严格门槛开启下完成一次 PPO：KL 0.00474、clip fraction 0.0574、grad norm 0.922、裁剪后 0.440、explained variance 0.0254；0 engine error。
- Python/根目录测试 119 项通过；C# 零警告编译；完整 external suite 通过 70 项。另 2 项旧 save/load 测试明确失败于当前 STS2 的 `RelicGrabBag` 重复 Populate 兼容问题，与训练路径分离，作为独立遗留项保留。

## P4：v7 大模型优化

第一组候选配置：

| 参数 | 初值 |
|---|---:|
| hidden / layers / heads | 256 / 4 / 8 |
| learning rate | 1e-4 |
| episodes / iteration | 96 |
| minibatch | 512 |
| update epochs | 最多 4，受 KL early-stop 控制 |
| target / hard-stop KL | 0.01 / 0.02 |
| entropy coef | 0.01 |
| max grad norm | 0.5 |
| boss mix | 0.15 |

训练日志必须增加 approximate KL、clip fraction、clip 前后 grad norm、value explained variance。用相同 seed 和环境步数比较 `lr=1e-4` 与 `3e-4`，一次只改变一个变量。

- [x] 大模型 + 最终输入契约完成一次严格更新 smoke。
- [x] KL、clip fraction、grad norm、explained variance 已进入 history/TensorBoard。
- [x] 固定 external submodule commit `e1a0688e…` 和父仓 pin。
- [ ] 创建全新 `m2_v7_clean_init0`。
- [ ] 相同 seed/环境步数完成 `lr=1e-4` 与 `3e-4` 短程对照。
- [ ] 依据 KL、梯度和 dev gate 选择配置后启动长跑。

## P5：课程与晋级

- [ ] 不再长期使用 v4 盲策略生成的固定 boss loadouts；v7 到达 boss 后动态刷新 on-policy boss replay buffer。
- [ ] 前 50 development seeds 用于 10-iteration gate；其余 450 seeds 用于阶段切换/每 50 iteration audit。
- [ ] 连续三个 gate 达到 Act 1 ≥30%，且 450-seed audit 不退化，才进入 full A0。
- [ ] 正式 1,000 test seeds 继续封存到 P5 最终验收。

## P6：序列 PPO（独立实验）

v7-clean 基线稳定后再做 truncated BPTT：sequence length 32、burn-in 8、episode boundary mask。不得与可见性修复混入同一个 run，以保持因果可解释性。

## 实施顺序

`P1 动作契约 → P2 观测契约 → P3 audit 硬门槛 → 真实引擎 smoke → P4 短程 LR 对照 → P5 长跑`。

P3 已通过；正式 `m2_v7_clean_init0` 仍需先固定 external submodule commit/父仓 pin，并完成 P4 的短程 LR 对照。smoke run 不计入正式训练结果。
