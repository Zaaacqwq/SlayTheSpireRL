/* Backend passes the full flattened history row through /metrics, so numeric
 * columns (train_win_rate, action_*, win_return, approx_kl, …) sit alongside the
 * untouched nested diagnostic blocks (visibility, reward_health, depth). */
export interface VisibilityBlock {
  decisions?: number
  offered_actions?: Record<string, number>
  chosen_actions?: Record<string, number>
  never_offered_actions?: string[]
  never_chosen_actions?: string[]
  candidate_collisions?: number
  pointer_misses?: Record<string, number>
  unknown_state_fields?: Record<string, number>
  unknown_entities?: Record<string, number>
  nonfinite_features?: number
  violations?: unknown[]
}

export interface MetricRow {
  iteration: number
  stage?: string
  visibility?: VisibilityBlock
  [key: string]: unknown
}

export interface RunStats {
  wins: number
  finished: number
  win_rate: number | null
  avg_reward: number | null
  total_reward: number | null
  avg_floor: number | null
  errors: number
  truncated: number
}

export interface Run {
  name: string
  config: Record<string, unknown>
  history_count: number
  episode_count: number
  checkpoints: number
  latest: MetricRow
  best: MetricRow
  stats: RunStats
  availability: { metrics: boolean; episodes: boolean }
}

export interface TrainingControlStatus {
  run_name: string
  state: 'stopped' | 'starting' | 'running'
  running: boolean
  watchdog_pids: number[]
  trainer_pids: number[]
  worker_count: number
  last_iteration: number | null
  checkpoint: string | null
  checkpoint_updated_at: string | null
}

export interface RoutePoint {
  floor: number | null
  room_type: string | null
}

export interface Episode {
  episode_id: string
  path?: string | null
  iteration?: number | null
  stage?: string
  split?: string
  character?: string | null
  outcome?: boolean | string | null
  total_reward?: number | null
  final_floor?: number | null
  final_act?: number | null
  steps: number
  truncated?: boolean
  error?: string | null
  route?: RoutePoint[]
  final_hp?: number | null
  max_hp?: number | null
  gold?: number | null
  deck_size?: number | null
}

export interface TimelineItem {
  iteration: number | null
  split: string
  stage?: string
  episodes: number
  wins: number
  win_rate: number | null
  avg_reward: number | null
  avg_floor: number | null
  errors: number
}

export interface ReplayStep {
  step: number
  decision?: string
  act?: number
  act_name?: string
  floor?: number
  room_type?: string
  round?: number
  action?: { action?: string; args?: Record<string, unknown> }
  selected?: Record<string, any> | null
  legal_actions?: any[]
  reward?: number
  logp?: number
  value?: number
  terminated?: boolean
  outcome?: boolean | string | null
  player?: Record<string, any>
  cards?: any[]
  options?: any[]
  choices?: any[]
  hand?: any[]
  enemies?: any[]
  energy?: number | null
  max_energy?: number | null
  player_powers?: any[]
  state?: Record<string, unknown>
}

export interface LiveWorker {
  worker_id: number
  status: string
  seq: number
  updated_at: string
  timestamp?: string
  iteration?: number
  stage?: string
  split?: string
  episode_id?: string
  seed?: string
  step?: number
  phase?: string
  act?: number
  floor?: number
  round?: number | null
  hp?: number | null
  max_hp?: number | null
  energy?: number | null
  action?: { cmd?: string; action?: string; args?: Record<string, unknown> }
  selected_label?: string | null
  target?: number | null
  reward?: number
  value?: number
  logp?: number
  outcome?: boolean | null
  error?: string | null
  action_rate?: number
}

export interface LiveSnapshot {
  enabled: boolean
  session_id: string | null
  updated_at: string | null
  worker_count: number
  dropped_events: number
  action_rate?: number
  stale: boolean
  age_seconds?: number
  workers: LiveWorker[]
}

export type LiveEvent = LiveWorker & {
  session_id: string
  timestamp: string
  type: 'status' | 'episode_start' | 'action' | 'episode_end' | 'episode_error'
}
