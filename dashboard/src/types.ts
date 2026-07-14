export type MetricRow = Record<string, string | number | null> & { iteration: number; stage?: string }

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
