import type { ReactNode } from 'react'
import { CircleDot, Crown, Gem, Heart, Info, ShoppingBag, Skull, Sparkles, Swords, Trophy } from 'lucide-react'
import { outcomeKnown, outcomeLabel, won } from '../format'
import { useI18n } from '../i18n'
import type { Locale } from '../i18n'

export function Panel({ icon, title, extra, tip, children }: {
  icon?: ReactNode; title?: string; extra?: ReactNode; tip?: string; children: ReactNode
}) {
  return (
    <section className="panel">
      {title && <div className="panel-title">{icon}<h2>{title}</h2>{tip && <InfoTip text={tip} />}{extra && <small>{extra}</small>}</div>}
      {children}
    </section>
  )
}

export function StatTile({ label, value, detail, tip, up = false, dim = false }: {
  label: string; value: string; detail?: string; tip?: string; up?: boolean; dim?: boolean
}) {
  return (
    <div className={`tile ${dim ? 'dim' : ''}`}>
      <span>{label}{tip && <InfoTip text={tip} />}</span>
      <strong>{value}</strong>
      {detail && <small className={up ? 'up' : ''}>{detail}</small>}
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
