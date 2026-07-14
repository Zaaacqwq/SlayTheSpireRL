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
