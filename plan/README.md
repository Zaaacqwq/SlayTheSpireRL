# RL v2 计划维护制度

`rl_v2_roadmap.md` 是长期路线、架构决策、阶段状态和验收门槛的权威来源。`rl_v2_current_stage.md` 只记录当前阶段的任务、证据、实验结果、阻塞项和下一步。

任何修改 `rl/**`、训练配置、数据 schema、实验方法、`external/sts2-cli` 固定版本或 RL 引擎适配的 commit，都必须同步更新 `rl_v2_current_stage.md`。阶段开始、完成、范围或验收标准变化时还必须更新 roadmap。完成状态只能在对应测试和验收实际通过后写入。

正式训练结束必须记录项目与 CLI commit、游戏/协议版本、完整配置、步数、所有 seeds 与 split hash、checkpoint、逐 episode 结果、均值/标准差/95% CI、对照组和结论。CI 的 `tools/check_rl_plan_sync.py` 强制执行当前阶段同步规则。
