# RL v2 CLI 协议状态机设计

RL v2 使用自己的 `sts2-cli` fork。训练协议必须把游戏等待玩家选择表示为显式 pending 状态，不允许游戏线程同步等待 stdin。

每个 action 请求只产生一个 response。需要连续选择时，状态序列必须是：

```text
decision(card_select, pending_id=N)
  -> action(select_cards, pending_id=N)
  -> decision(card_select, pending_id=N+1)
```

pending selector 具有唯一 ID、候选卡列表、min/max select 和创建阶段。选择结果只能提交给当前 ID；过期或重复提交返回结构化 error。CLI 主循环负责推进 action executor，worker watchdog 只重启当前进程。状态中保留 engine/protocol/fork commit 和 state hash，所有 nested-selector seed 都进入回放回归测试。

在该状态机完成并通过 Necrobinder-32、Regent-29、Regent-74 回放前，不允许把 M1 标记完成或开始正式 PPO。
