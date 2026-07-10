# 当前阶段：M0 清理、引擎验证与基准

更新日期：2026-07-10

## 已完成（有仓库证据）

- [x] 创建 `archive/rl-v1-final`，完整提交原工作树为 `6c34069`，创建 tag `rl-v1-final-2026-07-10`。
- [x] 从未改动的 `main` 创建 `rl-v2`；删除旧 `rl/`、旧计划、模型、日志、转换数据和自建模拟器；未改动 `mod/`、`mcp/`、游戏安装。
- [x] 将官方 `sts2-cli` 以 submodule 固定到 `d11aa883b582dd68bd39b331f3370746b30d447e`。
- [x] 建立严格的协议类型、动态合法动作初版、稳定状态 hash、固定 seed split、超时检测与持久进程客户端。
- [x] 建立计划同步 CI 和不依赖游戏 DLL 的单元测试。

## 调查结果与未完成项

- 本机已确认 .NET SDK 9.0.315、Python 3.10.11。
- 上游握手报告协议 `0.2.0`，但没有游戏版本或 CLI commit；状态没有统一显式 legal-actions 数组。当前 adapter 对未知 phase/缺失关键字段直接报错。
- 上游支持 `start_run`、`load_save`、`set_player`、`enter_room`、`set_draw_order`，但“任意指定牌组/敌人/遗物/HP/seed 的原子 curriculum reset”尚未满足，需要协议扩展设计与回归测试。
- 当前机器没有在已知 Steam 路径发现 `sts2.dll`，也未设置有效 `STS2_GAME_DIR`。因此 CLI build、schema 样本冻结、五角色实跑、确定性与吞吐验收均**未完成**。
- `card_select` 多选组合、药水目标、商店移除选牌等动作需用真实状态样本确认，不能提前宣称合法动作覆盖完整。

## 下一步（按顺序）

1. 设置 `STS2_GAME_DIR`，初始化 submodule 后运行上游 setup；不得提交 `external/sts2-cli/lib/`。
2. 对五角色各捕获从 `start_run` 到所有 decision phase 的原始 JSON，形成版本化 schema fixtures，并冻结兼容策略。
3. 补齐每一 phase 的动作 round-trip；任何未知字段进入告警/UNK，未知 phase 阻断运行。
4. 实现随机合法 agent、完整 episode runner 和进程池；五角色各 20 局并保存逐 episode 结果。
5. 对相同 seed/action trace 做逐步 hash 对比；随后测试 1/4/8/16 workers 的吞吐、RSS、崩溃和自动恢复。
6. 仅在全部门槛实际通过后，将 M0 标记完成并启动 M1。

## M0 验收记录

尚无正式实验。门槛仍为：8 workers ≥100 decision steps/s；五角色随机合法 agent 各 ≥20 局；崩溃率 <0.1%；非法动作率 0。运行 `python -m sts2rl.m0 --root .` 可检查本机先决条件；返回码 2 表示未就绪。
