import { useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, CircleDot, Crown, Gem, Heart, Info, Moon, ShoppingBag, Skull,
  Sparkles, Sun, Swords, Trophy,
} from 'lucide-react'
import { outcomeKnown, outcomeLabel, stageLabel, stageTint, won } from '../format'
import { useI18n } from '../i18n'
import type { Locale } from '../i18n'

export function Panel({ icon, title, extra, tip, tone, children }: {
  icon?: ReactNode; title?: string; extra?: ReactNode; tip?: string
  tone?: 'critical' | 'good'; children: ReactNode
}) {
  return (
    <section className={`panel ${tone ? `panel-${tone}` : ''}`}>
      {title && (
        <div className="panel-title">
          {icon}<h2>{title}</h2>{tip && <InfoTip text={tip} />}
          {extra && <small>{extra}</small>}
        </div>
      )}
      {children}
    </section>
  )
}

export function StatTile({ label, value, detail, tip, tone, dim = false }: {
  label: string; value: string; detail?: string; tip?: string
  tone?: 'good' | 'warn' | 'critical'; dim?: boolean
}) {
  return (
    <div className={`tile ${dim ? 'dim' : ''}`}>
      <span>{label}{tip && <InfoTip text={tip} />}</span>
      <strong className="num">{value}</strong>
      {detail && <small className={tone ? `tone-${tone}` : ''}>{detail}</small>}
    </div>
  )
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip">
      <button type="button" aria-label="Info"><Info /></button>
      <span role="tooltip">{text}</span>
    </span>
  )
}

/** Progress meter for the curriculum gate: fill vs a target threshold marker. */
export function Meter({ value, target, tone = 'accent' }: {
  value: number; target?: number; tone?: 'accent' | 'good' | 'warn'
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="meter">
      <span className={`meter-fill tone-${tone}`} style={{ width: `${pct}%` }} />
      {target !== undefined && (
        <span className="meter-target" style={{ left: `${Math.min(100, target * 100)}%` }} />
      )}
    </div>
  )
}

/** A small status pill with a state-colored dot — used for training + live state. */
export function StatusPill({ state, label, sub }: { state: string; label: string; sub?: string }) {
  return (
    <span className={`status-pill status-${state}`}>
      <i />
      <span><b>{label}</b>{sub && <small>{sub}</small>}</span>
    </span>
  )
}

export function AlertBanner({ level, title, body }: {
  level: 'critical' | 'warn'; title: string; body?: string
}) {
  return (
    <div className={`alert-banner alert-${level}`} role="alert">
      <AlertTriangle />
      <div><b>{title}</b>{body && <span>{body}</span>}</div>
    </div>
  )
}

const THEME_KEY = 'sts2-rl-theme'
export type Theme = 'light' | 'dark'

/** Explicit light/dark override stamped on <html data-theme>. When unset the OS
 *  preference wins (theme.css handles both). */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])
  return [theme, () => setTheme(current => (current === 'dark' ? 'light' : 'dark'))]
}

export function ThemeToggle() {
  const [theme, toggle] = useTheme()
  return (
    <button className="icon-btn" onClick={toggle} title={theme === 'dark' ? 'Light' : 'Dark'} aria-label="Toggle theme">
      {theme === 'dark' ? <Sun /> : <Moon />}
    </button>
  )
}

/** Pick one curriculum stage to look at. Mixing them corrupts every chart: a
 *  combat stage's avg_floor is the constant 1.0 and its win rate is a different
 *  quantity entirely from a full run's. */
export function StagePicker({ stages, value, onChange, allLabel }: {
  stages: string[]
  value: string
  onChange: (stage: string) => void
  allLabel: string
}) {
  const { locale } = useI18n()
  if (stages.length < 2) return null
  return (
    <div className="stage-picker" role="tablist">
      <button role="tab" aria-selected={value === ''} className={value === '' ? 'active' : ''}
              onClick={() => onChange('')}>{allLabel}</button>
      {stages.map(stage => (
        <button key={stage} role="tab" aria-selected={value === stage}
                className={value === stage ? 'active' : ''}
                style={value === stage ? { borderColor: stageTint(stage), color: stageTint(stage) } : undefined}
                onClick={() => onChange(stage)}>
          {stageLabel(stage, locale)}
        </button>
      ))}
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>
}

export function OutcomeBadge({ outcome }: { outcome: unknown }) {
  const { locale } = useI18n()
  const known = outcomeKnown(outcome)
  const kind = !known ? 'unknown' : won(outcome) ? 'win' : 'loss'
  const Icon = !known ? CircleDot : won(outcome) ? Trophy : Skull
  return <span className={`outcome ${kind}`}><Icon />{outcomeLabel(outcome, locale)}</span>
}

export function RoomIcon({ room }: { room?: string | null }) {
  const value = room?.toLowerCase() || ''
  if (value.includes('boss')) return <Crown />
  if (value.includes('elite')) return <Skull />
  if (value.includes('monster') || value.includes('combat')) return <Swords />
  if (value.includes('rest')) return <Heart />
  if (value.includes('shop') || value.includes('merchant')) return <ShoppingBag />
  if (value.includes('treasure') || value.includes('chest')) return <Gem />
  if (value.includes('event')) return <Sparkles />
  return <CircleDot />
}

export const ROOM_LABELS: Record<string, string> = {
  Monster: '战斗', Elite: '精英', Boss: 'Boss', Rest: '休息', RestSite: '休息',
  Shop: '商店', Merchant: '商店', Treasure: '宝箱', Chest: '宝箱', Event: '事件',
}

export const ROOM_LABELS_EN: Record<string, string> = {
  Monster: 'Combat', Elite: 'Elite', Boss: 'Boss', Rest: 'Rest', RestSite: 'Rest',
  Shop: 'Shop', Merchant: 'Shop', Treasure: 'Treasure', Chest: 'Treasure', Event: 'Event',
}

export const roomLabel = (room?: string | null, locale: Locale = 'zh-CN'): string =>
  room ? ((locale === 'en-US' ? ROOM_LABELS_EN : ROOM_LABELS)[room] ?? room) : '—'
