import { useState } from 'react'
import { Coins, Crown, FlaskConical, Heart, Layers3, Shield, Zap } from 'lucide-react'
import type { ReplayStep } from '../types'
import { Panel } from './ui'
import { useI18n } from '../i18n'

const assetMisses = new Set<string>()

function renderDescription(card: any): string {
  let text = String(card.description || '')
  const values = { ...(card.vars || {}), ...(card.stats || {}) }
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined) continue
    text = text.replace(new RegExp(`\\{${key}(?::[^}]*)?\\}`, 'gi'), String(value))
  }
  return text.replace(/\{([^}:]+)(?::[^}]*)?\}/g, '$1')
}

export function GameCard({ card, selected = false, option = false }: {
  card: any; selected?: boolean; option?: boolean
}) {
  const { t } = useI18n()
  const asset = card.id || card.name
  const [showArt, setShowArt] = useState(Boolean(asset && !assetMisses.has(asset)))
  const hasCost = !option && card.cost !== null && card.cost !== undefined
  return (
    <div className={`game-card ${selected ? 'chosen' : ''}`}>
      {hasCost && <span className="cost">{card.cost}</span>}
      {showArt && (
        <img className="art" alt="" src={`/api/assets/by-name/${encodeURIComponent(asset)}`}
             onError={() => { assetMisses.add(asset); setShowArt(false) }} />
      )}
      <b>{card.name || card.title || t('replay.option', { index: card.index ?? '' })}{card.upgraded ? '+' : ''}</b>
      <small>{card.rarity || card.type || ''}</small>
      <p>{renderDescription(card)}</p>
      {selected && <em>{t('replay.selected')}</em>}
    </div>
  )
}

export function EnemyCard({ enemy }: { enemy: any }) {
  const { t } = useI18n()
  const ratio = enemy.max_hp ? Math.max(0, Math.min(1, enemy.hp / enemy.max_hp)) : 0
  const intents = (enemy.intents || [])
    .map((intent: any) => `${intent.type}${intent.damage !== null && intent.damage !== undefined ? ` ${intent.damage}` : ''}`)
    .join(' · ')
  return (
    <div className="entity">
      <b>{enemy.name}</b>
      <span className="hp">{enemy.hp} / {enemy.max_hp}{enemy.block ? ` +${enemy.block} ${t('replay.block')}` : ''}</span>
      <span className="hp-track"><span className="hp-fill" style={{ width: `${ratio * 100}%` }} /></span>
      <small>{t('replay.intent', { intent: intents || t('replay.noIntent') })}</small>
      {enemy.powers?.length > 0 && (
        <span className="powers">
          {enemy.powers.map((power: any) => <i key={power.name}>{power.name} {power.amount}</i>)}
        </span>
      )}
    </div>
  )
}

export function PlayerStrip({ step }: { step: ReplayStep }) {
  const { t } = useI18n()
  const player = step.player || {}
  return (
    <div className="player-strip">
      <b>{player.name || t('replay.unknownCharacter')}</b>
      <span><Heart /> {player.hp ?? '—'} / {player.max_hp ?? '—'}</span>
      <span><Shield /> {player.block ?? 0}</span>
      <span><Coins /> {player.gold ?? 0}</span>
      {step.energy !== null && step.energy !== undefined && (
        <span><Zap /> {step.energy} / {step.max_energy ?? '—'}</span>
      )}
      {step.player_powers?.map((power: any) => (
        <span key={power.name} style={{ color: 'var(--ink-3)' }}>{power.name} {power.amount}</span>
      ))}
    </div>
  )
}

export function StateDelta({ step, next }: { step: ReplayStep; next?: ReplayStep }) {
  const { t } = useI18n()
  if (!next) return null
  const enemyHp = (row?: ReplayStep) =>
    row?.enemies?.reduce((sum: number, enemy: any) => sum + (enemy.hp || 0), 0)
  const fields: [string, number | undefined, number | undefined][] = [
    [t('replay.health'), step.player?.hp, next.player?.hp],
    [t('replay.block'), step.player?.block, next.player?.block],
    [t('replay.gold'), step.player?.gold, next.player?.gold],
    [t('replay.energy'), step.energy ?? undefined, next.energy ?? undefined],
    [t('replay.enemyHealth'), enemyHp(step), enemyHp(next)],
  ]
  const changes = fields
    .map(([label, before, after]) =>
      [label, typeof before === 'number' && typeof after === 'number' ? after - before : 0] as const)
    .filter(([, delta]) => delta !== 0)
  if (!changes.length) return null
  return (
    <div className="delta-row">
      <span>{t('replay.actionResult')}</span>
      {changes.map(([label, delta]) => (
        <b key={label} className={(label === t('replay.enemyHealth') ? delta < 0 : delta > 0) ? 'pos' : 'neg'}>
          {label} {delta > 0 ? '+' : ''}{delta}
        </b>
      ))}
    </div>
  )
}

export function DeckRail({ step }: { step: ReplayStep }) {
  const { t } = useI18n()
  const deck = step.player?.deck || []
  const relics = step.player?.relics || []
  const potions = step.player?.potions || []
  const grouped = [...deck.reduce((map: Map<string, { card: any; count: number }>, card: any) => {
    const key = `${card.name}${card.upgraded ? '+' : ''}`
    const current = map.get(key)
    if (current) current.count += 1
    else map.set(key, { card, count: 1 })
    return map
  }, new Map()).values()]
  return (
    <aside className="deck-rail">
      <Panel icon={<Layers3 />} title={t('replay.deck', { count: step.player?.deck_size ?? deck.length })}>
        <div className="deck-list">
          {grouped.map(({ card, count }: any) => (
            <div key={`${card.name}-${card.upgraded}`}>
              <span className="c">{card.cost ?? '·'}</span>
              <b>{card.name}{card.upgraded ? '+' : ''}</b>
              <em>×{count}</em>
            </div>
          ))}
          {!grouped.length && <span style={{ color: 'var(--ink-3)' }}>{t('replay.noDeck')}</span>}
        </div>
      </Panel>
      <Panel icon={<Crown />} title={t('replay.relics')}>
        <div className="token-row">
          {relics.length
            ? relics.map((relic: any, index: number) => <span key={index}>{relic.name ?? String(relic)}</span>)
            : <span style={{ color: 'var(--ink-3)' }}>{t('common.none')}</span>}
        </div>
      </Panel>
      <Panel icon={<FlaskConical />} title={t('replay.potions')}>
        <div className="token-row">
          {potions.length
            ? potions.map((potion: any, index: number) => <span key={index}>{potion.name ?? String(potion)}</span>)
            : <span style={{ color: 'var(--ink-3)' }}>{t('common.none')}</span>}
        </div>
      </Panel>
    </aside>
  )
}
