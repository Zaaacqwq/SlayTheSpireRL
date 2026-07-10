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
- [x] 1,000 局 A0 evaluator：1,000 episodes、995 正常 game_over、5 异常（3 EngineTimeout、2 ProtocolError）；异常 seed 已独立重试，Defect 140 恢复，Necrobinder 32/Regent 29/Regent 74 稳定失败，Regent 76 稳定协议错误。
- [x] BC update、PPO update、GAE、Recurrent GRU candidate policy 和 checkpoint/resume 初版。
- [x] 定位并修复 nested card-select timeout 根因（见下方修复记录），固定为本地 fork commit `5eef59f1b696367642b48f7de8a0d6739502ff2c`。
- [x] 5 个原失败 seed 有界回归（各 80 steps）：Defect-140、Necrobinder-32、Regent-29、Regent-76、Regent-74 全部 `error: null`，无 EngineTimeout/ProtocolError，单步最长 2.03s（原为 10s 超时）。脚本 `tools/m1_regress_failed_seeds.py`、`scratchpad/bounded_regress.py`。
- [x] 全量 1,000 局 A0 evaluator 复跑（`tools/m1_evaluate_progress.py`，固定 fork `5eef59f`，16 workers，per-decision timeout 10s，seeds `m1-a0-<char>-<0..199>`）：**1000/1000，0 EngineTimeout / 0 ProtocolError / 0 非法动作**，五角色各 200 局。结果 `rl/runs/m1_a0_1000_v2.jsonl`/`.json`。
- [ ] **（新阻塞，2026-07-10）** 上述 1000 局里有 **4 局是 soft-lock**：撞满 2000 步上限、从未到 game_over。数字上算「clean」（无 error），但**不是真实对局**，属于 M1 明确禁止的 episode pollution，因此 **M1 仍未通过**。见下方 soft-lock 记录。
- [ ] 根治 stuck-executor soft-lock（或在 evaluator 中检测无进展并判为失败），再补 seed isolation 长测后才能通过 M1。

### M1 异常修复记录

- `ParticleWall` 的“卡仍在手牌”假错误已在本地 sts2-cli 适配中移除；Regent-76 回归已不再产生 ProtocolError。
- **nested card-select timeout 根因已定位（2026-07-10）**：`RunSimulator.DetectDecisionPoint()` 在每个战斗动作里会二次调用 `WaitForActionExecutor()`（动作 handler 自身一次 + 战斗房间检测一次）。当第二层嵌套选牌（Necrobinder Snap、Regent Begone 等）在第一层选择的效果尚未提交完成前 resolve 时，游戏 `ActionExecutor.IsRunning` 会在本局剩余时间内**永久卡在 true**。该等待循环原写为「自旋 1000 次 `Thread.Sleep(1)`」，作者意图约 1s 预算，但 Windows 默认定时器精度把 `Sleep(1)` 舍入到约 15.6ms，实际每次自旋约 15.6s，两次调用共约 31s，超过 CLI 客户端 10s 响应超时→`EngineTimeout`。用 Harmony 反射对 `Thread.Sleep` 做 burst 累加诊断 + 时间戳复现，捕获到 `DoSelectCards→DetectDecisionPoint→WaitForActionExecutor→Thread.Sleep` 调用栈与精确 31s 耗时后确认。
- **修复**：把该自旋循环从「迭代次数上限」改为基于 `Stopwatch` 的真实 1000ms 时间上限，并在出现 pending 选牌/奖励时提前退出；固定为本地 fork commit `5eef59f1b696367642b48f7de8a0d6739502ff2c`。
- 此前固定的 `906751c5ddd4e30aa16bf899ac1962c729a38293`（nested-selector 与 transient card-play 补丁）为本修复的父 commit，二者叠加后原 timeout seed 不再报错。
- 已创建 submodule 本地分支 `rl-v2-protocol-state-machine`，后续改动只进入该 fork；状态机设计见 `docs/RL_V2_CLI_STATE_MACHINE.md`。

### soft-lock 记录（2026-07-10，全量 1000 局暴露）

- Stopwatch 修复只消除了「超时报错」这个**症状**，底层 `ActionExecutor.IsRunning` 卡住的根因未除。全量 1000 局暴露出 **4 个 soft-lock 局**，均在一次嵌套选牌之后战斗停止推进、`combat_play` 候选恒定、打牌无实际效果，一路空转到 2000 步上限，从不 game_over：
  - 慢速模式（约 1990 ms/step，每局约 66 分钟）：`Necrobinder-32`、`Regent-29`、`Regent-74`——每个动作都被 `WaitForActionExecutor` 的 1000ms 上限拖满（两次调用）。
  - 快速模式（约 19 ms/step，每局约 37s）：`Defect-23`——同样空转到 2000 步，但不触发 executor 等待，旧的超时机制根本发现不了。
- probe（`scratchpad/probe_necro32.py`）确认：Necrobinder-32 自约第 37 步起 `combat_play` 恒定 4 候选，连续 120+ 步无变化，是软死锁而非「真的活了 2000 步」。
- 影响：这 4 局让 1000 局总耗时从 5:46（前 997 局）膨胀到 1:11:16；更重要的是它们是**污染样本**，会毒化任何 BC/PPO 轨迹采集。
- 根治方向（二选一或并行）：① 在 sts2-cli fork 里按 `docs/RL_V2_CLI_STATE_MACHINE.md` 的显式 pending-selector 状态机根除 executor 卡死；② 在 evaluator/env 里加**无进展检测**（连续 N 步 state-hash 不变即判定 soft-lock，将该 episode 标为失败并重启进程），既恢复吞吐又能把这类局正确计为失败而非 clean。

## M1 下一步

1. 复现并根治 4 个 soft-lock（优先引擎侧状态机；同时给 env/evaluator 加无进展 watchdog 兜底）。
2. soft-lock 清零后重跑全量 1000 局，要求 0 error 且 0 soft-lock（无 2000-step 截断局）。
3. 再补 seed isolation / episode pollution 长测，全部通过后方可标记 M1 完成。
4. 所有训练实验必须同步记录 commit、配置、seed hash、checkpoint 和结果。
