export type MetricRow = Record<string, string | number | null> & { iteration: number; stage?: string }
export type Run = {
  name: string
  config: Record<string, unknown>
  history_count: number
  episode_count: number
  checkpoints: number
  latest: MetricRow
  best: MetricRow
  stats: { wins: number; finished: number; win_rate: number | null; avg_reward: number | null; total_reward: number | null; avg_floor: number | null; errors: number; truncated: number }
  availability: { metrics: boolean; episodes: boolean }
}
export type Episode = {
  episode_id: string; path?: string | null; iteration?: number | null; stage?: string; split?: string
  character?: string | null; outcome?: boolean | string | null; total_reward?: number | null
  final_floor?: number | null; steps: number; truncated?: boolean; error?: string | null
}
export type ReplayStep = {
  step: number; decision?: string; act?: number; act_name?: string; floor?: number; room_type?: string
  round?: number; action?: { action?: string; args?: Record<string, unknown> }; selected?: Record<string, unknown> | null
  reward?: number; logp?: number; value?: number; player?: Record<string, any>; cards?: any[]; options?: any[]
  hand?: any[]; enemies?: any[]; energy?: number; max_energy?: number; player_powers?: any[]; state?: Record<string, unknown>
}
