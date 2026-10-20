import { apiFetch } from './client'
import type { ApiResponse } from './types'

interface LifecycleResult {
  connector_type: string
  endpoint_identity: string
  state: 'paused' | 'unknown'
}

function actionPath(type: string, identity: string, action: 'pause' | 'run-now') {
  return `/ingestion/connectors/${encodeURIComponent(type)}/${encodeURIComponent(identity)}/${action}`
}

/** The existing server route commits pause and attempts its best-effort audit. */
export function pauseConnector(type: string, identity: string) {
  return apiFetch<ApiResponse<LifecycleResult>>(actionPath(type, identity, 'pause'), { method: 'POST' })
}

/** Resume only: the server sets unknown until heartbeat, without forcing a poll. */
export function resumeConnector(type: string, identity: string) {
  return apiFetch<ApiResponse<LifecycleResult>>(actionPath(type, identity, 'run-now'), { method: 'POST' })
}
