import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'

export type Locale = 'zh-CN' | 'en-US'
type Params = Record<string, string | number>

const EN: Record<string, string> = {
  'brand.name': 'Spire RL', 'brand.subtitle': 'Training Observatory',
  'nav.overview': 'Overview', 'nav.episodes': 'Episodes', 'nav.replay': 'Replay', 'nav.live': 'Live Workers',
  'title.overview': 'Training Overview', 'sub.overview': 'Learning curves, curriculum stages, and recorded games',
  'title.episodes': 'Episode Archive', 'sub.episodes': 'Results, routes, and metrics for every recorded game',
  'title.replay': 'Episode Replay', 'sub.replay': 'Step-by-step cards, choices, and combat playback',
  'title.live': 'Live Workers', 'sub.live': 'Watch all samplers or inspect one worker console',
  'common.refresh': 'Refresh', 'common.connecting': 'Connecting…', 'common.updated': 'Synced {time}',
  'common.autoRefresh': 'Auto-refresh every 5 seconds', 'common.rawJson': 'Raw JSON',
  'common.none': 'None', 'common.unknown': 'Unknown', 'common.win': 'Win', 'common.loss': 'Loss',
  'common.iteration': 'Iteration {value}', 'common.noData': 'No data',
  'run.label': 'Training run', 'run.group': 'Training runs', 'run.legacy': 'Historical trajectories',
  'run.notFound': 'No training records found. Check rl/runs.',
  'overview.stage': 'Current stage', 'overview.latestTrain': 'Latest training win rate',
  'overview.bestDev': 'Best validation win rate', 'overview.recorded': 'Recorded episodes',
  'overview.iterationCount': 'Iteration {iteration} / {count} records', 'overview.avgFloor': 'Average floor {value}',
  'overview.noMetrics': 'No metrics', 'overview.noDev': 'No validation yet',
  'overview.checkpoints': '{count} checkpoints', 'overview.winChart': 'Learning curve · win rate',
  'overview.stageBands': 'Background bands = curriculum stages', 'overview.floorChart': 'Reached floor',
  'overview.loss': 'Total loss', 'overview.valueLoss': 'Value loss', 'overview.entropy': 'Policy entropy',
  'overview.curriculum': 'Curriculum progress', 'overview.resumed': 'resumed',
  'overview.iterations': '{count} iterations', 'overview.recordedBatches': 'Recorded episode batches',
  'overview.clickBatch': 'Click to inspect this batch', 'overview.noRecorded': 'No recorded episodes. Backfill with tools/m2_record_episodes.py.',
  'overview.config': 'Experiment configuration', 'overview.rawConfig': 'Raw configuration JSON',
  'chart.trainWin': 'Training win rate', 'chart.devWin': 'Validation win rate',
  'chart.avgFloor': 'Average reached floor', 'chart.devFloor': 'Validation average floor',
  'chart.noMetrics': 'No training metrics',
  'tip.iteration': 'One iteration collects a batch of episodes, then updates PPO. This run uses 48 episodes and 4 PPO update epochs per iteration. Validation runs periodically.',
  'tip.trainWin': 'Fraction of exploratory training episodes won in the current iteration. It is on-policy and naturally noisy.',
  'tip.devWin': 'Greedy win rate on fixed development seeds. No learning happens during validation, so it is more comparable across iterations within a stage.',
  'tip.loss': 'Policy loss + value coefficient × value loss − entropy coefficient × policy entropy. Read it with the component losses; lower alone does not guarantee stronger play.',
  'tip.policyLoss': 'The PPO clipped objective. It raises probabilities of beneficial actions while limiting how far one update can move the policy.',
  'tip.valueLoss': 'Mean squared error between the value head prediction and the computed return target.',
  'tip.entropy': 'Uncertainty of the action distribution. Higher means more exploration; lower means the policy is more decisive.',
  'tip.avgFloor': 'Mean final floor reached by episodes in this iteration. It reveals progress even when full-run wins are still rare.',
  'tip.reward': 'Training signal assigned to an action or episode, including terminal outcome and optional floor-progress shaping.',
  'tip.logp': 'Natural log of the probability assigned to the selected action. Values closer to zero indicate a more likely action.',
  'tip.value': 'The model’s estimate of expected future return from the current state.',
  'tip.checkpoint': 'A saved model and optimizer snapshot that can be evaluated or resumed later.',
  'tip.stage': 'The curriculum task currently used to collect experience, from isolated combats to full runs.',
  'episodes.search': 'Search seed', 'episodes.allIterations': 'All iterations', 'episodes.allSources': 'All sources',
  'episodes.training': 'Training', 'episodes.validation': 'Validation', 'episodes.replay': 'Replay',
  'episodes.allResults': 'All results', 'episodes.count': '{count} episodes', 'episodes.result': 'Result',
  'episodes.id': 'Episode / SEED', 'episodes.stage': 'Stage', 'episodes.route': 'Route',
  'episodes.floor': 'Floor', 'episodes.hp': 'HP', 'episodes.reward': 'Reward', 'episodes.steps': 'Steps',
  'episodes.empty': 'No matching episodes', 'episodes.page': 'Page {page} / {pages}',
  'episodes.previous': 'Previous page', 'episodes.next': 'Next page',
  'replay.checkpoint': 'Checkpoint', 'replay.allIterations': 'All iterations', 'replay.episode': 'Episode',
  'replay.route': 'Route', 'replay.step': 'Step {step}', 'replay.round': 'Round {round}',
  'replay.act': 'Act {act}', 'replay.floor': 'Floor {floor}', 'replay.previous': 'Previous step',
  'replay.next': 'Next step', 'replay.progress': 'Replay progress', 'replay.enemies': 'Enemies and intents',
  'replay.hand': 'Hand', 'replay.cardPool': 'Card choices', 'replay.options': 'Options',
  'replay.routes': 'Available routes', 'replay.actions': 'Legal actions', 'replay.noEnemies': 'No enemies',
  'replay.actual': 'Selected action', 'replay.reward': 'Reward', 'replay.value': 'Value estimate',
  'replay.rawState': 'Raw state JSON', 'replay.draw': 'Draw pile {count}', 'replay.discard': 'Discard pile {count}',
  'replay.ended': 'Episode ended · {outcome}', 'replay.noReplay': 'No replay data',
  'replay.combat': 'Combat', 'replay.cardReward': 'Card reward', 'replay.cardSelect': 'Card selection',
  'replay.event': 'Event choice', 'replay.map': 'Map route', 'replay.rest': 'Rest site', 'replay.decision': 'Decision',
  'replay.target': 'target {target}', 'replay.deck': 'Deck ({count})', 'replay.relics': 'Relics',
  'replay.potions': 'Potions', 'replay.noDeck': 'No deck information', 'replay.option': 'Option {index}',
  'replay.selected': 'Selected', 'replay.intent': 'Intent: {intent}', 'replay.noIntent': 'none',
  'replay.unknownCharacter': 'Unknown character', 'replay.actionResult': 'Action result',
  'replay.health': 'HP', 'replay.block': 'Block', 'replay.gold': 'Gold', 'replay.energy': 'Energy',
  'replay.enemyHealth': 'Enemy HP',
  'live.disabledTitle': 'Live telemetry is not enabled for this run',
  'live.disabledBody': 'This training process loaded older code. Live actions will appear after the next checkpoint resume.',
  'live.workers': '{active}/{total} workers active', 'live.rate': '{rate} actions/s',
  'live.dropped': '{count} telemetry events dropped', 'live.stale': 'Training stream disconnected',
  'live.worker': 'Worker {id}', 'live.inspect': 'Open console', 'live.back': 'All workers',
  'live.seed': 'Seed', 'live.floorRound': 'Floor {floor} · Round {round}', 'live.step': 'Step {step}',
  'live.lastAction': 'Last action', 'live.waiting': 'Waiting for an episode…',
  'live.pause': 'Pause display', 'live.resume': 'Resume display', 'live.clear': 'Clear view',
  'live.autoscroll': 'Auto-scroll', 'live.rawCommand': 'Raw CLI command', 'live.noEvents': 'No events from this worker yet.',
  'status.idle': 'Idle', 'status.starting': 'Starting', 'status.running': 'Playing',
  'status.updating': 'Updating PPO', 'status.finished': 'Finished', 'status.truncated': 'Truncated',
  'status.error': 'Error', 'status.stopped': 'Stopped',
}

const ZH: Record<string, string> = {
  'brand.name': '尖塔 RL', 'brand.subtitle': '训练观测台',
  'nav.overview': '训练总览', 'nav.episodes': '对局档案', 'nav.replay': '对局复盘', 'nav.live': '实时操作',
  'title.overview': '训练总览', 'sub.overview': '学习曲线、课程阶段与已录对局',
  'title.episodes': '对局档案', 'sub.episodes': '每一局的结果、路线与数据',
  'title.replay': '对局复盘', 'sub.replay': '逐步回放：卡池、选择与战斗过程',
  'title.live': '实时操作', 'sub.live': '查看全部采样器，或进入单个 worker 控制台',
  'common.refresh': '刷新', 'common.connecting': '连接中…', 'common.updated': '已同步 {time}',
  'common.autoRefresh': '每 5 秒自动刷新', 'common.rawJson': '原始 JSON',
  'common.none': '无', 'common.unknown': '未知', 'common.win': '胜利', 'common.loss': '失败',
  'common.iteration': '迭代 {value}', 'common.noData': '暂无数据',
  'run.label': '训练实验', 'run.group': '训练实验', 'run.legacy': '历史轨迹',
  'run.notFound': '未发现训练记录：确认 rl/runs 下有实验目录',
  'overview.stage': '当前阶段', 'overview.latestTrain': '最新训练胜率',
  'overview.bestDev': '最佳验证胜率', 'overview.recorded': '已记录对局',
  'overview.iterationCount': '迭代 {iteration} / 共 {count} 次记录', 'overview.avgFloor': '平均楼层 {value}',
  'overview.noMetrics': '暂无指标', 'overview.noDev': '尚无验证评估',
  'overview.checkpoints': '{count} 个 checkpoint', 'overview.winChart': '学习曲线 · 胜率',
  'overview.stageBands': '背景色带 = 课程阶段', 'overview.floorChart': '到达楼层',
  'overview.loss': '总损失', 'overview.valueLoss': '价值损失', 'overview.entropy': '策略熵',
  'overview.curriculum': '课程阶段推进', 'overview.resumed': '重返',
  'overview.iterations': '{count} 次迭代', 'overview.recordedBatches': '已录制的对局批次',
  'overview.clickBatch': '点击查看该批对局', 'overview.noRecorded': '该实验还没有对局记录，可用 tools/m2_record_episodes.py 补录',
  'overview.config': '实验配置', 'overview.rawConfig': '原始配置 JSON',
  'chart.trainWin': '训练胜率', 'chart.devWin': '验证胜率',
  'chart.avgFloor': '平均到达楼层', 'chart.devFloor': '验证平均楼层',
  'chart.noMetrics': '暂无训练指标',
  'tip.iteration': '一次迭代会先收集一批对局，再更新 PPO。当前配置每次收集 48 局并执行 4 个 PPO 更新 epoch；验证按设定周期运行。',
  'tip.trainWin': '当前迭代中带探索的训练对局胜率。它来自 on-policy 采样，因此自然会有较大波动。',
  'tip.devWin': '在固定 development seeds 上使用贪心策略得到的胜率。验证过程不学习，因此同一课程阶段内更适合跨迭代比较。',
  'tip.loss': '策略损失 + 价值系数 × 价值损失 − 熵系数 × 策略熵。需要结合分项观察，单纯下降不等于策略一定更强。',
  'tip.policyLoss': 'PPO 的 clipped objective：提高有利动作概率，同时限制一次更新改变策略的幅度。',
  'tip.valueLoss': '价值头预测回报与计算出的回报目标之间的均方误差。',
  'tip.entropy': '动作概率分布的不确定性。越高表示探索更多，越低表示策略更确定。',
  'tip.avgFloor': '本次迭代各局最终到达楼层的平均值；完整通关仍很少时，它能反映中间进展。',
  'tip.reward': '分配给动作或对局的训练信号，包括终局胜负和可选的楼层进度 shaping。',
  'tip.logp': '模型给所选动作概率的自然对数；越接近 0，表示该动作在策略中越可能。',
  'tip.value': '模型对当前状态未来累计回报的估计。',
  'tip.checkpoint': '保存的模型和优化器快照，可用于后续评估或续训。',
  'tip.stage': '当前用于收集经验的课程任务，从单独战斗逐步过渡到完整爬塔。',
  'episodes.search': '搜索 seed', 'episodes.allIterations': '全部迭代', 'episodes.allSources': '全部来源',
  'episodes.training': '训练', 'episodes.validation': '验证', 'episodes.replay': '回放',
  'episodes.allResults': '全部结果', 'episodes.count': '{count} 场对局', 'episodes.result': '结果',
  'episodes.id': '对局 / SEED', 'episodes.stage': '阶段', 'episodes.route': '路线',
  'episodes.floor': '楼层', 'episodes.hp': '血量', 'episodes.reward': '奖励', 'episodes.steps': '步数',
  'episodes.empty': '没有匹配的对局', 'episodes.page': '第 {page} / {pages} 页',
  'episodes.previous': '上一页', 'episodes.next': '下一页',
  'replay.checkpoint': 'checkpoint', 'replay.allIterations': '全部迭代', 'replay.episode': '对局',
  'replay.route': '路线', 'replay.step': '第 {step} 步', 'replay.round': '回合 {round}',
  'replay.act': '第 {act} 幕', 'replay.floor': '{floor} 层', 'replay.previous': '上一步',
  'replay.next': '下一步', 'replay.progress': '复盘进度', 'replay.enemies': '敌人与意图',
  'replay.hand': '手牌', 'replay.cardPool': '可选卡池', 'replay.options': '可选项',
  'replay.routes': '可选路线', 'replay.actions': '可选动作', 'replay.noEnemies': '无敌人',
  'replay.actual': '实际选择', 'replay.reward': '奖励', 'replay.value': '价值估计',
  'replay.rawState': '原始状态 JSON', 'replay.draw': '抽牌堆 {count}', 'replay.discard': '弃牌堆 {count}',
  'replay.ended': '本局结束 · {outcome}', 'replay.noReplay': '暂无可复盘数据',
  'replay.combat': '战斗', 'replay.cardReward': '卡牌奖励（卡池选择）', 'replay.cardSelect': '选牌',
  'replay.event': '事件抉择', 'replay.map': '地图路线选择', 'replay.rest': '休息点', 'replay.decision': '决策',
  'replay.target': '目标 {target}', 'replay.deck': '卡组（{count}）', 'replay.relics': '遗物',
  'replay.potions': '药水', 'replay.noDeck': '无卡组信息', 'replay.option': '选项 {index}',
  'replay.selected': '已选择', 'replay.intent': '意图：{intent}', 'replay.noIntent': '无',
  'replay.unknownCharacter': '未知角色', 'replay.actionResult': '动作结果',
  'replay.health': '生命', 'replay.block': '格挡', 'replay.gold': '金币', 'replay.energy': '能量',
  'replay.enemyHealth': '敌方生命',
  'live.disabledTitle': '这个 run 尚未启用实时事件',
  'live.disabledBody': '当前训练进程加载的是旧代码；下次从 checkpoint 续训后会开始显示动作直播。',
  'live.workers': '{active}/{total} 个 worker 活跃', 'live.rate': '{rate} 动作/秒',
  'live.dropped': '已丢弃 {count} 条观测事件', 'live.stale': '训练事件流已断开',
  'live.worker': 'Worker {id}', 'live.inspect': '进入控制台', 'live.back': '全部 Worker',
  'live.seed': 'Seed', 'live.floorRound': '{floor} 层 · {round} 回合', 'live.step': '第 {step} 步',
  'live.lastAction': '最近动作', 'live.waiting': '等待新对局…',
  'live.pause': '暂停显示', 'live.resume': '继续显示', 'live.clear': '清空视图',
  'live.autoscroll': '自动滚动', 'live.rawCommand': '原始 CLI 命令', 'live.noEvents': '这个 worker 还没有事件。',
  'status.idle': '空闲', 'status.starting': '启动中', 'status.running': '对局中',
  'status.updating': '更新 PPO', 'status.finished': '已结束', 'status.truncated': '已截断',
  'status.error': '错误', 'status.stopped': '已停止',
}

interface I18nValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: string, params?: Params) => string
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<Locale>(() =>
    localStorage.getItem('sts2-rl-locale') === 'en-US' ? 'en-US' : 'zh-CN')
  const value = useMemo<I18nValue>(() => ({
    locale,
    setLocale: next => { localStorage.setItem('sts2-rl-locale', next); updateLocale(next) },
    t: (key, params = {}) => {
      let text = (locale === 'en-US' ? EN : ZH)[key] ?? EN[key] ?? key
      for (const [name, replacement] of Object.entries(params)) {
        text = text.replaceAll(`{${name}}`, String(replacement))
      }
      return text
    },
  }), [locale])
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside I18nProvider')
  return value
}
