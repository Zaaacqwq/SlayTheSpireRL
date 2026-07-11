# M1 通用环境与训练基础设施（验收完成）

更新日期：2026-07-11

M0、M1 已完成；M2 尚未开始。M1 的验收为 1,000 局 A0 随机 agent、零非法动作、worker seed 隔离、episode 不污染和可恢复训练基础设施。

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

## 后续协议跟进项

- 本机已确认 .NET SDK 9.0.315、Python 3.10.11。
- 上游握手报告协议 `0.2.0`，但没有游戏版本或 CLI commit；状态没有统一显式 legal-actions 数组。当前 adapter 对未知 phase/缺失关键字段直接报错。
- 上游支持 `start_run`、`load_save`、`set_player`、`enter_room`、`set_draw_order`，但“任意指定牌组/敌人/遗物/HP/seed 的原子 curriculum reset”尚未满足，需要协议扩展设计与回归测试。
- `card_select` 多选组合、药水目标、商店移除选牌等动作需用真实状态样本确认，不能提前宣称合法动作覆盖完整。

## M0 验收记录

M0 验收已达到当前定义门槛：8 workers steady benchmark 122.81 decision steps/s、benchmark errors 0；五角色随机合法 agent 各 20 个完整 episode，全部 game_over、非法动作/timeout 0；进程 kill 后 reset 自动恢复；所有主要 decision phase schema 已观察并记录。长期崩溃率和 1,000 局 A0 属于 M1 规模验收，不作为 M0 的完成条件。

## M1 完成项

- [x] 初版 Gymnasium-shaped `STS2Env`，不强制依赖 gymnasium。
- [x] 统一 observation normalization、实体编码、未知字段告警。
- [x] trajectory `Transition` schema 和 Parquet/JSONL fallback writer。
- [x] random/heuristic action 入口。
- [x] evaluator/trajectory collector、BC loss、GAE、masked candidate pointer policy、checkpoint/resume 初版。
- [x] TensorBoard logger（无 tensorboard 时 JSONL fallback）和实验配置。
- [x] 1,000 局 A0 evaluator：1,000 episodes、995 正常 game_over、5 异常（3 EngineTimeout、2 ProtocolError）；异常 seed 已独立重试，Defect 140 恢复，Necrobinder 32/Regent 29/Regent 74 稳定失败，Regent 76 稳定协议错误。
- [x] BC update、PPO update、GAE、Recurrent GRU candidate policy 和 checkpoint/resume 初版。
- [x] 定位并修复 nested card-select timeout 根因，并完成非重入 FIFO dispatcher、quiescence、同步 selector bridge 和 fail-closed poison；固定为本地 fork commit `7fe000619930a199ab1cfccdbde727a0b30613af`。
- [x] 5 个原失败 seed 有界回归（各 80 steps）：Defect-140、Necrobinder-32、Regent-29、Regent-76、Regent-74 全部 `error: null`，无 EngineTimeout/ProtocolError，单步最长 2.03s（原为 10s 超时）。脚本 `tools/m1_regress_failed_seeds.py`、`scratchpad/bounded_regress.py`。
- [x] 全量 1,000 局 A0 evaluator 复跑：6 个持久 worker、10 秒单请求 timeout、五角色各 200 局；1000/1000 到达 `game_over`，EngineTimeout 0、ProtocolError 0、非终止 0，总耗时 280.0 秒。
- [x] seed isolation/episode pollution：同 worker 重复 reset、跨角色 episode 后 reset、另一 worker reset 的 anchor hash 均为 `01d53fd17756833c54cc7b2543aa8477f0b1a7a944b5a07e71f51304cee47f5e`。
- [x] evaluator 改为每 worker 一个持久 CLI，直接运行 `dotnet Sts2Headless.dll`；修复 timeout 只杀 `dotnet run` 父进程而遗留子进程的问题。P5 后孤儿进程为 0。
- [x] 200 局预验收发现事件内 selector reward 的 skip 可形成相同状态活锁；event selector reward 不再暴露该 no-op skip，Defect-23 从 2,000 steps 非终止恢复为 78 steps `game_over`。
- [x] M2 前引擎加固：quiescence/`RunInline`/dispatcher callback/`Send` 超时或 fault 会永久 poison 当前进程、取消未开始的 FIFO callback、返回 fatal error 并由 Python kill/restart；evaluator 遇到任一错误或非终止以非零退出。
- [x] fail-closed 首轮 200 局把此前被吞掉的 fault 显式化为 7 个 EngineFatal（DenseVegetation 4、JungleMazeAdventure 1、WhisperingHollow 2）。窄范围修复表现层调用和满药水槽奖励集后，200/200 和最终 1000/1000 均为 `game_over`，fatal/timeout/protocol error/非终止均为 0；最终耗时 273.0s，孤儿进程 0。

### M1 异常修复记录

- `ParticleWall` 的“卡仍在手牌”假错误已在本地 sts2-cli 适配中移除；Regent-76 回归已不再产生 ProtocolError。
- **nested card-select timeout 根因已定位（2026-07-10）**：`RunSimulator.DetectDecisionPoint()` 在每个战斗动作里会二次调用 `WaitForActionExecutor()`（动作 handler 自身一次 + 战斗房间检测一次）。当第二层嵌套选牌（Necrobinder Snap、Regent Begone 等）在第一层选择的效果尚未提交完成前 resolve 时，游戏 `ActionExecutor.IsRunning` 会在本局剩余时间内**永久卡在 true**。该等待循环原写为「自旋 1000 次 `Thread.Sleep(1)`」，作者意图约 1s 预算，但 Windows 默认定时器精度把 `Sleep(1)` 舍入到约 15.6ms，实际每次自旋约 15.6s，两次调用共约 31s，超过 CLI 客户端 10s 响应超时→`EngineTimeout`。用 Harmony 反射对 `Thread.Sleep` 做 burst 累加诊断 + 时间戳复现，捕获到 `DoSelectCards→DetectDecisionPoint→WaitForActionExecutor→Thread.Sleep` 调用栈与精确 31s 耗时后确认。
- **修复**：把该自旋循环从「迭代次数上限」改为基于 `Stopwatch` 的真实 1000ms 时间上限，并在出现 pending 选牌/奖励时提前退出；固定为本地 fork commit `5eef59f1b696367642b48f7de8a0d6739502ff2c`。
- **最终根治**：`HeadlessCardSelector.ResolvePending` 采用 clear-before-complete，nested selection 不再被外层清理覆盖；`SingleThreadDispatcher.Post` 只入 FIFO、不内联重入；命令完成不再依赖 `ActionExecutor.IsRunning`。
- 游戏的 `ICardSelector.GetSelectedCardReward` 是同步阻塞接口，event/rest/shop 四个入口保留单飞、显式跟踪并在选择后 join 的 bridge。若要彻底删除该 bridge，需要协议级 suspended-work-item broker，不能只靠 `async/await`。
- `SingleThreadDispatcher.Send` 使用 5 秒上限；超时后不是让 callback 留在队列稍后执行，而是原子取消所有尚未开始的 work item 并 poison 进程。`DispatcherSelfTest` 固定验证 quiescence timeout、Send timeout、队列取消和 poison 后拒绝执行。
- headless 表现层修复不再伪造全局 `NGame.Instance`；音频 getter/方法、表现层 wait 和 `DenseVegetation.Rest` 的唯一 rumble 调用在窄范围 no-op。可选药水奖励在槽满时保留已领取奖励并 skip 剩余奖励集，匹配 UI 可达结果。
- 此前固定的 `906751c5ddd4e30aa16bf899ac1962c729a38293`（nested-selector 与 transient card-play 补丁）为本修复的父 commit，二者叠加后 3 个 timeout seed 回归通过。
- 已创建 submodule 本地分支 `rl-v2-protocol-state-machine`，后续改动只进入该 fork；状态机设计见 `docs/RL_V2_CLI_STATE_MACHINE.md`。

## 有意的动作空间偏离（非修复，需在 M2 前复核）

- **事件内 card reward 不暴露 `skip_card_reward`**（`protocol.py::legal_actions`，由引擎的 `from_event=true` 触发）。这是**客户端屏蔽，不是根治**：引擎侧 `can_skip` 仍为 `true`、`SkipReward()` 仍然存在，直接驱动 CLI 或改动作空间仍可复现"skip 回到同一事件状态"的活锁。代价是 agent 永远无法跳过事件奖励，与真实游戏行为不一致。普通战斗奖励不受影响（`test_combat_card_reward_remains_skippable` 守护双向行为）。
- 若 M2 需要还原完整动作空间，正确做法是在引擎侧让 skip 真正推进事件状态，而不是继续在客户端隐藏。

## M1 训练基础设施完成项

- [x] `torch` / `pyarrow` 补进 `rl/pyproject.toml` 的 `train` / `export` extras（此前 `dependencies = []`，训练侧代码从未被执行过）。
- [x] 新增 `sts2rl.features`：动作 one-hot + 索引槽位的定长候选编码（`CANDIDATE_FEATURE_DIM`），变长候选列表 padding + mask。此前观测与模型之间**没有任何桥接**，训练代码无法消费引擎数据。
- [x] 修复 `collect_episode` 的轨迹语义 bug：原实现在 `step()` 之后记录，`state`/`legal_actions` 存的是动作执行后的新状态，而 `action` 选自旧状态，导致 BC 的目标索引未定义、会静默毒化训练数据。现记录决策发生时的状态与其候选集（`test_transition_records_the_state_the_action_was_chosen_from` 守护）。
- [x] 训练环路可学习性：固定 batch 过拟合从 1.4236 降到 0.0538，证明 candidate encoder → pointer → masked cross-entropy 的梯度通路完整。

## M1 验收记录

P5 初始实现 commit `46b6a770ed145c7098660c36676024e8572f8141`；M2 前 fail-closed 主仓库 commit `c22d4afe802f39ef7422fbca6c9af04a8c5460ba`，CLI 固定为 `7fe000619930a199ab1cfccdbde727a0b30613af`。协议 `0.2.0`、macOS arm64、6 persistent workers、timeout 10s、固定 seeds `m1-a0-<character>-<0..199>`。poison-enabled 逐局结果保存在本地忽略目录 `rl/runs/m1_a0_1000_hardened.json`：1,000 个唯一 seed，steps min/median/max = 11/62/138，episode seconds min/median/max = 0.47/1.48/10.97；错误和非终止均为 0。正确状态 hash anchor 仍与 `rl/schema/p0_baseline_hash.json` 逐位一致。

### 引擎侧复核（独立复跑，2026-07-11）

1,000 局 A0 在同一机器上独立重跑：`ERRORS 0 / NONTERMINAL 0`，280.5s；逐 seed 与 `rl/runs/m1_a0_1000.json` 的 steps/outcome **全部一致（1000/1000）**，确认结果可复现且无非确定性。`tools/m1_seed_isolation.py` 独立复跑，四个 hash 均为 `01d53fd17756833c54cc7b2543aa8477f0b1a7a944b5a07e71f51304cee47f5e`，`passed: true`。

### 训练基础设施验收（`tools/m1_training_e2e.py`，2026-07-11）

真实引擎端到端：五角色各 4 局共 20 局采集到 1,206 个决策 → `sts2rl.features` 编码 → BC 更新 → step 10 落 checkpoint → **在全新进程中 resume** 续训到 step 20。resumed 与 uninterrupted 的 20 步 loss 序列完全相同，最终参数 hash 同为 `f3b148cabb7c83a5daae32f6efe660773a09a4ecfe2fa0faa03989beb41d3927`，`passed: true`。跨进程是关键：进程内 resume 只能证明 `load_state_dict` 没报错，不能证明 checkpoint 足以在进程消失后重建训练。

注意随机策略数据的 BC loss 不会下降（克隆随机策略的最优解就是候选上的均匀分布，loss 贴在熵下界 ≈ ln(候选数)），因此可学习性用**真实 batch 过拟合**单独验证：1.4236 → 0.0538。

`pytest` 全量 25 passed（含 fatal response kill/restart）；dispatcher 自测 PASS，C# build 0 errors。当前环境仅有 PyTorch 的 NumPy 未安装 warning，不影响测试结果。
