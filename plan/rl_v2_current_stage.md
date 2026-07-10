# 当前阶段：M1 通用环境与训练基础设施

更新日期：2026-07-10

M0 已完成；当前开始 M1。M1 的验收为 1,000 局 A0 随机 agent、零非法动作、worker seed 隔离、episode 不污染和可恢复训练基础设施。

## 已完成（有仓库证据）

- [x] 创建 `archive/rl-v1-final`，完整提交原工作树为 `6c34069`，创建 tag `rl-v1-final-2026-07-10`。
- [x] 从未改动的 `main` 创建 `rl-v2`；删除旧 `rl/`、旧计划、模型、日志、转换数据和自建模拟器；未改动 `mod/`、`mcp/`、游戏安装。
- [x] 将官方 `sts2-cli` 以 submodule 固定到 `d11aa883b582dd68bd39b331f3370746b30d447e`。
- [x] 建立严格的协议类型、动态合法动作初版、稳定状态 hash、固定 seed split、超时检测与持久进程客户端。
- [x] 建立计划同步 CI 和不依赖游戏 DLL 的单元测试。
- [x] 配置本机 `STS2_GAME_DIR`，使用 Git Bash 完成上游 setup；`sts2.dll` 已复制、IL patch 已完成，`.NET build` 0 errors（仅 3 个 nullable warnings）。
- [x] 运行单次 JSON smoke test：Ironclad 固定 seed 返回 `event_choice`，ready/version 为 `0.2.0`。
- [x] 五角色启动 smoke test：Ironclad、Silent、Defect、Necrobinder、Regent 各固定 seed 均返回 Act 1 `event_choice`。
- [x] 同一 Ironclad seed 的两次 `EngineClient.reset` 状态 hash 一致：`685e709d1018645fc3769f11a9474f5b99c9da365b7bb6469b4b999fb715e9c5`。
- [x] 同一持久 CLI 进程连续 reset Ironclad → Silent 均成功返回 `event_choice`；当前 submodule 工作树包含针对 v0.107.1 reset/初始化的未提交本地修改，需后续迁移到 MIT fork 并固定 commit。
- [x] 真实引擎随机合法 rollout 已验证：Ironclad `m0-random-001` 经过 Neow、地图和战斗，前 10 steps 成功；第 11 step 在 10 秒内无响应，`EngineTimeout` 杀进程。该 episode 不计入成功率，trace 作为 M0 超时回归样本。
- [x] 根据真实 JSON 将地图候选兼容为 `choices`，休息/事件选项兼容 `is_enabled`；协议 adapter 单测仍通过。
- [x] 真实 schema 观察清单写入 `rl/schema/m0_observed_schema.json`；已观察 event、map、combat、card reward、shop、bundle/card select 和 game over，rest site 仍待真实样本。
- [x] 1/4/8/16 worker steady benchmark（每 worker 50 steps）：20.48 / 73.14 / 122.81 / 140.20 decision steps/s；8 workers 达到 100 门槛，benchmark errors 为 0。
- [x] 修复真实 card reward、shop、multi-select action names/组合；修复后五角色各 5 局 × 20 steps 全部无异常。
- [x] 最终五角色各 20 局 × 20 steps 复测：Ironclad/Silent/Defect/Necrobinder/Regent 均 `20/20 ok`，每角色 400 steps，ProtocolError/timeout 0。
- [x] 自动恢复验证：手动 kill 持久 CLI 后，下一次 `reset` 自动重启并成功返回 Silent `event_choice`。
- [x] 完整随机合法 episode：五角色各 20 局，全部返回 `game_over`（每角色 20/20，无 timeout/protocol error；随机策略结果均为死亡）。
- [x] 通过 `enter_room(type=rest)` 捕获并记录 `rest_site` schema（HEAL/SMITH options）。

## 调查结果与未完成项

- 本机已确认 .NET SDK 9.0.315、Python 3.10.11。
- 上游握手报告协议 `0.2.0`，但没有游戏版本或 CLI commit；状态没有统一显式 legal-actions 数组。当前 adapter 对未知 phase/缺失关键字段直接报错。
- 上游支持 `start_run`、`load_save`、`set_player`、`enter_room`、`set_draw_order`，但“任意指定牌组/敌人/遗物/HP/seed 的原子 curriculum reset”尚未满足，需要协议扩展设计与回归测试。
- 当前机器没有在已知 Steam 路径发现 `sts2.dll`，也未设置有效 `STS2_GAME_DIR`。因此 CLI build、schema 样本冻结、五角色实跑、确定性与吞吐验收均**未完成**。
- （已解决）游戏实际位于 `D:\steam\steamapps\common\Slay the Spire 2\data_sts2_windows_x86_64`；setup 的递归查找已正确复制 DLL。环境 doctor 现在全部通过。
- 上游 `tests/test_characters.py tests/test_play.py` 组合运行在约 40 秒内未完成，已终止；尚未判定原因，也未计入成功样本。
- 随机 rollout 的超时发生在战斗中连续合法动作之后，尚未确定是特定动作、CLI 引擎死锁还是 timeout 过短；需要保留 stderr、动作 trace 并用最小复现 seed 定位。
- 首轮五角色各 20 局（修复前）出现 `choose_card`/multi-select 协议错误；修复后复测已清零，但仍需完成各 20 局最终样本。
- 已为 `EngineClient` 增加最近动作 trace 与 stderr tail；同一 seed 的后续 20-step 重跑未再次超时，说明问题可能与动作/引擎时序相关，仍需多次重复和进程恢复统计。
- `card_select` 多选组合、药水目标、商店移除选牌等动作需用真实状态样本确认，不能提前宣称合法动作覆盖完整。

## 下一步（按顺序）

1. 设置 `STS2_GAME_DIR`，初始化 submodule 后运行上游 setup；不得提交 `external/sts2-cli/lib/`。
2. 对五角色各捕获从 `start_run` 到所有 decision phase 的原始 JSON，形成版本化 schema fixtures，并冻结兼容策略。
3. 补齐每一 phase 的动作 round-trip；任何未知字段进入告警/UNK，未知 phase 阻断运行。
4. 实现随机合法 agent、完整 episode runner 和进程池；五角色各 20 局并保存逐 episode 结果。
5. 对相同 seed/action trace 做逐步 hash 对比；随后测试 1/4/8/16 workers 的吞吐、RSS、崩溃和自动恢复。
6. 仅在全部门槛实际通过后，将 M0 标记完成并启动 M1。

## M0 验收记录

M0 验收已达到当前定义门槛：8 workers steady benchmark 122.81 decision steps/s、benchmark errors 0；五角色随机合法 agent 各 20 个完整 episode，全部 game_over、非法动作/timeout 0；进程 kill 后 reset 自动恢复；所有主要 decision phase schema 已观察并记录。长期崩溃率和 1,000 局 A0 属于 M1 规模验收，不作为 M0 的完成条件。

## M1 当前进度

- [x] 初版 Gymnasium-shaped `STS2Env`，不强制依赖 gymnasium。
- [x] 统一 observation normalization、实体编码、未知字段告警。
- [x] trajectory `Transition` schema 和 Parquet/JSONL fallback writer。
- [x] random/heuristic action 入口。
- [x] evaluator/trajectory collector、BC loss、GAE、masked candidate pointer policy、checkpoint/resume 初版。
- [x] TensorBoard logger（无 tensorboard 时 JSONL fallback）和实验配置。
- [ ] 1,000 局 A0 evaluator、seed isolation 和 episode pollution regression。

## M1 下一步

先实现 evaluator 和 trajectory 采集，再实现模型与 loss；所有训练实验必须同步记录 commit、配置、seed hash、checkpoint 和结果。
