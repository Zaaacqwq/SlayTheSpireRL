# STS2 RL v2 路线图

状态：**M0 已完成，M1 进行中**。M2–M6 均未开始。

## 固定架构决策

- 训练后端只使用真实 `sts2.dll`；不重写游戏规则，不提交 DLL 或资产。
- `external/sts2-cli` 是固定 commit 的 Git submodule；当前固定 `d11aa883b582dd68bd39b331f3370746b30d447e`，上游 MIT。
- Python 接口为 `EngineClient.reset(RunConfig) -> DecisionState`、`step(ActionCandidate) -> StepResult`、`close()`；每 worker 一个持久进程，超时后重启。
- 状态使用可变实体编码、phase embedding、GRU 历史、Transformer、动态候选 pointer head 和 value head；未知内容进入 `UNK` 并告警。
- 自定义 PyTorch Recurrent Masked PPO，支持 BC；默认 gamma 0.999、GAE 0.95、clip 0.2、AdamW。
- full-run reward 为通关 +1、死亡 -1、加 `0.2 × potential-based` 楼层进度变化；保留 terminal-only ablation。能力只按未见 seed 通关率评价。
- seed 以 SHA-256 稳定划分 train/development/test，正式 test 不参与选择。

## 阶段与门槛

### M0 — 清理、引擎验证与基准（已完成，2026-07-10）

归档 v1；清空旧 RL；固定并构建 CLI；冻结 schema；五角色各随机合法完成 20 局；确定性重放；1/4/8/16 worker 吞吐、稳定性和恢复测试。实测 8 workers 122.81 decision steps/s，benchmark errors 0，五角色 episode 非法动作/timeout 0。

### M1 — 通用环境与训练基础设施（进行中）

进程池、规范化、Gymnasium 环境、Transformer/GRU pointer policy、BC、PPO/GAE、Parquet trajectory、checkpoint/恢复、统一评估和 TensorBoard。验收为随机 agent 无干预 1,000 局 A0，零非法动作、重复 worker seed 和 episode 污染。

### M2 — Ironclad（未开始）

按普通战斗、混合战斗、Act 1、完整 A0 curriculum 推进。最终 5 个初始化、隔离 1,000 test seeds，A0 平均通关率 ≥40%，95% bootstrap CI 下界超过启发式，非法动作 0、timeout <1%，完成 reward ablation 和拆分报告。

### M3 — Silent、Defect、Necrobinder、Regent（未开始）

依次复用 M2 curriculum。每角色 1,000 未见 test seeds 通关率 ≥40%，显著超过启发式，非法动作 0、timeout <1%。

### M4 — 五角色统一模型（未开始）

角色平衡教师蒸馏及等比例多任务 PPO。每角色 ≥35%，相对独立模型下降 ≤5 个百分点；失败不阻止独立模型交付但必须记录。

### M5 — Ascension A1–A10（未开始）

80% 当前等级、20% 较低等级；300 development seeds 达到 20% 后升级。A5/A10 用 5 个训练 seeds 和 1,000 test seeds；每角色 A10 ≥10% 且超过启发式，A0 回测 ≥35%。

### M6 — 图形游戏部署（未开始）

统一 `PolicyRunner` 支持 headless 和 STS2MCP HTTP backend，两端输出相同状态与候选。逐步 parity；每角色真实图形 A0 20 局；零索引漂移/解析/非法动作；与 headless 差 ≤15 个百分点；导出 TorchScript/ONNX。

## 最终完成标准

五个独立模型 A0 全部 ≥40%，A10 全部 ≥10%；统一模型实验可复现；同一策略可运行两个 backend；干净环境可重建、训练、评估；全部变化同步记录。硬件假设为 Ryzen 7 9800X3D、RTX 5080 16GB，不依赖云；spire-codex 不进入主训练链；LLM 示范可选。
