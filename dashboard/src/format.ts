import type { Locale } from './i18n'

export const num = (value: unknown, digits = 2): string =>
  typeof value === 'number' ? value.toFixed(digits).replace(/\.0+$/, '') : '—'

export const pct = (value: unknown): string =>
  typeof value === 'number' ? `${(value * 100).toFixed(1).replace(/\.0$/, '')}%` : '—'

export const won = (value: unknown): boolean => value === true || value === 'true'

export const outcomeKnown = (value: unknown): boolean => value !== null && value !== undefined

export const outcomeLabel = (value: unknown, locale: Locale = 'zh-CN'): string =>
  !outcomeKnown(value) ? (locale === 'en-US' ? 'Unknown' : '未知')
    : won(value) ? (locale === 'en-US' ? 'Win' : '胜利') : (locale === 'en-US' ? 'Loss' : '失败')

/** Stage display order also drives the band tints on the learning charts. */
export const STAGE_ORDER = ['normal_combat', 'mixed_combat', 'boss_combat', 'act1', 'full_a0']

/* Combat stages are single fights spawned by start_combat: they happen on floor 1
 * and nowhere else, so their avg_floor is the constant 1.0 and their boss funnel is
 * undefined. Charting them beside act1 flattens the only curve that carries signal.
 * Stages are therefore viewed one at a time, and floor/funnel panels are hidden for
 * the ones where those numbers mean nothing. */
export const RUN_STAGES = ['act1', 'full_a0']

export const isRunStage = (stage?: string | null): boolean =>
  !!stage && RUN_STAGES.includes(stage)

export const STAGE_LABELS: Record<string, string> = {
  normal_combat: '普通战斗',
  mixed_combat: '混合战斗',
  boss_combat: 'Boss 战',
  act1: '第一幕全程',
  full_a0: '完整爬塔',
}

export const STAGE_LABELS_EN: Record<string, string> = {
  normal_combat: 'Normal Combat', mixed_combat: 'Mixed Combat', boss_combat: 'Boss Combat',
  act1: 'Full Act 1', full_a0: 'Full A0 Run',
}

export const stageLabel = (stage?: string | null, locale: Locale = 'zh-CN'): string =>
  stage ? ((locale === 'en-US' ? STAGE_LABELS_EN : STAGE_LABELS)[stage] ?? stage) : '—'

const STAGE_TINTS = ['#3987e5', '#199e70', '#e66767', '#c98500', '#9085e9']

export const stageTint = (stage: string): string => {
  const index = STAGE_ORDER.indexOf(stage)
  return STAGE_TINTS[index >= 0 ? index % STAGE_TINTS.length : 0]
}
