import type { Episode, MetricRow, ReplayStep, Run, TimelineItem } from './types'

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.error || response.statusText)
  }
  return response.json()
}

export const fetchRuns = () =>
  get<{ items: Run[]; legacy_episode_count: number }>('/api/runs')

export const fetchMetrics = (run: string) =>
  get<{ rows: MetricRow[] }>(`/api/runs/${encodeURIComponent(run)}/metrics`)

export const fetchTimeline = (run: string) =>
  get<{ items: TimelineItem[] }>(`/api/runs/${encodeURIComponent(run)}/timeline`)

export interface EpisodeQuery {
  page?: number
  pageSize?: number
  search?: string
  split?: string
  outcome?: string
  stage?: string
  iteration?: string
}

export const fetchEpisodes = (run: string, query: EpisodeQuery) => {
  const params = new URLSearchParams()
  params.set('page', String(query.page ?? 1))
  params.set('page_size', String(query.pageSize ?? 30))
  if (query.search) params.set('search', query.search)
  if (query.split) params.set('split', query.split)
  if (query.outcome) params.set('outcome', query.outcome)
  if (query.stage) params.set('stage', query.stage)
  if (query.iteration) params.set('iteration', query.iteration)
  return get<{ items: Episode[]; total: number }>(
    `/api/runs/${encodeURIComponent(run)}/episodes?${params}`)
}

export const fetchEpisodeDetail = (run: string, episodeId: string, iteration?: number | null, split?: string) => {
  const params = new URLSearchParams()
  if (iteration !== undefined && iteration !== null) params.set('iteration', String(iteration))
  if (split) params.set('split', split)
  const suffix = params.size ? `?${params}` : ''
  return get<{ meta: Episode; rows: ReplayStep[] }>(
    `/api/runs/${encodeURIComponent(run)}/episodes/${encodeURIComponent(episodeId)}${suffix}`)
}
