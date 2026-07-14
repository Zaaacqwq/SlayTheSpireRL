# M2 v7-clean：可见性契约与大模型训练计划

状态：**P1–P4 已完成，P5 on-policy boss replay 已落地，`m2_v7_clean_init0` 正式长跑中**
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
- [x] 新 run 使用 `m2_v7_clean_init0`，不覆盖旧目录。

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

- 词表由完整 ModelDb 目录、确定性真实引擎 sweep 和已审核 smoke artifact 生成：2,299 个稳定实体；卡牌 579、敌人 102、遗物 298、药水 66、power 273、全部 24 enchantment、10 affliction、常规及事件专用 orb。
- `rl/runs/v7_clean_visibility_audit.json` 汇总 381 个 episode、24,007 个决策：15 类动作全部 offered 且至少 chosen 一次；candidate collision、pointer miss、unknown field、unknown entity、non-finite feature 和 violations 全为 0。
- 最终 `hidden=256 / layers=4 / heads=8` 更新 smoke（`m2_v7_clean_update_smoke_final`）在严格门槛开启下完成一次 PPO：KL 0.00474、clip fraction 0.0574、grad norm 0.922、裁剪后 0.440、explained variance 0.0254；0 engine error。
- Python/根目录测试 119 项通过；C# 零警告编译；完整 external suite 通过 70 项。另 2 项旧 save/load 测试明确失败于当前 STS2 的 `RelicGrabBag` 重复 Populate 兼容问题，与训练路径分离，作为独立遗留项保留。

## P4：v7 大模型优化

初始候选与实测后选定配置：

| 参数 | 初值 |
|---|---:|
| hidden / layers / heads | 256 / 4 / 8 |
| learning rate | ~~1e-4~~ → **5e-5** |
| episodes / iteration | 96 |
| minibatch | ~~512~~ → **256 + entity-length-aware packing** |
| update epochs | 最多 4，受 KL early-stop 控制 |
| target / hard-stop KL | 0.01 / 0.02 |
| entropy coef | 0.01 |
| max grad norm | 0.5 |
| boss mix | 0.15 |

训练日志必须增加 approximate KL、clip fraction、clip 前后 grad norm、value explained variance。相同 init/seed 流的一轮校准结果：

| LR | train steps | dev（50 seeds，仅作噪声参考） | approximate KL | 结论 |
|---:|---:|---:|---:|---|
| 5e-5 | 6,341 | 12% | **0.00667** | 采用，低于 0.01 target |
| 1e-4 | 6,278 | 2% | 0.01857 | 接近 0.02 hard stop，淘汰 |
| 3e-4 | 6,176 | 6% | **0.02804** | 越过 hard stop，淘汰 |

dev 单点不用于 LR 排序，选择只依据同轮 KL 安全性。原始 512 随机混批因完整地图状态把所有样本 pad 到超宽实体序列，10 分钟仍未完成且占用约 15.8GB 显存；按实体长度装箱后，256 minibatch 的完整 train+50-dev 轮耗时约 84–92 秒，峰值约 8.4GB。

- [x] 大模型 + 最终输入契约完成一次严格更新 smoke。
- [x] KL、clip fraction、grad norm、explained variance 已进入 history/TensorBoard。
- [x] 固定 external submodule commit `e1a0688e…` 和父仓 pin。
- [x] 为全新 `m2_v7_clean_init0` 固定启动配置（从 init seed 0，不加载旧 checkpoint）。
- [x] 相同 init/seed 流完成 `lr=1e-4` 与 `3e-4` 等规模短程对照；另补 `5e-5` 安全校准。
- [x] 依据 KL 选择 `lr=5e-5 / minibatch=256 / length-aware packing`。
- [x] 启动 `m2_v7_clean_init0` 长跑；首轮 96 局为 0 error / 0 visibility violation，KL 0.00354，watchdog 持续守护。

## P5：课程与晋级

- [x] `--on-policy-boss-replay` 完全绕开 v4 固定文件；只从本 run 到达 boss 的首回合状态抽取 HP、升级牌组、遗物、药水和实际 boss id，原子落盘到 run 目录，满 8 个快照后启用 15% replay，滚动保留 256 个。
- [ ] 前 50 development seeds 用于 10-iteration gate；其余 450 seeds 用于阶段切换/每 50 iteration audit。
- [ ] 连续三个 gate 达到 Act 1 ≥30%，且 450-seed audit 不退化，才进入 full A0。
- [ ] 正式 1,000 test seeds 继续封存到 P5 最终验收。

## P6：序列 PPO（独立实验）

v7-clean 基线稳定后再做 truncated BPTT：sequence length 32、burn-in 8、episode boundary mask。不得与可见性修复混入同一个 run，以保持因果可解释性。

## 实施顺序

`P1 动作契约 → P2 观测契约 → P3 audit 硬门槛 → 真实引擎 smoke → P4 短程 LR 对照 → P5 长跑`。

P3 与 P4 短程校准已通过，P5 on-policy boss replay 已实现并完成真实引擎恢复验证：从真实 `THE_KIN_BOSS` 首回合快照恢复 39 HP、17 张牌（含升级牌）后可正确进入 `combat_play`；训练器 CUDA smoke 显示 `boss_replay_source=on_policy` 且未构建旧静态 boss stage。正式 `m2_v7_clean_init0` 不使用旧 checkpoint，也不读取 `m2_boss_loadouts.json`。smoke/calibration run 不计入正式训练结果。

正式启动参数固定为：`hidden=256 / layers=4 / heads=8`、`lr=5e-5`、`episodes=96`、`minibatch=256`、最多 4 epochs、`target_kl=0.01 / hard_kl_stop=0.02`、12 workers、CUDA、`boss_mix=0.15`、`--on-policy-boss-replay`、`--max-stage act1`。先从 normal curriculum 开始；达到 Act 1 后仍由人工执行连续 gate 与 450-seed audit，不能自动越过到 full A0。
