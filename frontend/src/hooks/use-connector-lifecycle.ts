import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, getConnectorDetail } from '@/api/client'
import { pauseConnector, resumeConnector } from '@/api/connector-lifecycle'

type Action = 'pause' | 'resume'
interface Target { type: string; identity: string; action: Action }
interface Operation { pending: boolean; error: string | null }

const targetKey = (type: string, identity: string) => JSON.stringify([type, identity])
// Match the existing ingestion reader keys. Lifecycle writes live separately
// from the analytics/settings hooks and do not change their public exports.
const detailKey = (type: string, identity: string) => ['ingestion', 'connector-detail', type, identity] as const
const summariesKey = ['ingestion', 'connectors-summaries'] as const

function actionAllowed(action: Action, state: string) {
  return action === 'pause' ? state !== 'paused' : state === 'paused'
}

function failureMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 409) {
    return `${error.message} Try the action again to refresh state before another write.`
  }
  const reason = error instanceof Error ? error.message : 'The request could not be confirmed.'
  return `${reason} Try the action again to refresh state before another write; a failed response can follow a committed change.`
}

/** No optimistic state or automatic POST retry; reads remain the state authority. */
export function useConnectorLifecycle(type: string, identity: string) {
  const queryClient = useQueryClient()
  const inFlight = useRef(new Set<string>())
  const needsRead = useRef(new Set<string>())
  const [operations, setOperations] = useState<Record<string, Operation>>({})

  const mutation = useMutation({
    retry: false,
    mutationFn: async (target: Target) => {
      const key = targetKey(target.type, target.identity)
      // A deliberate retry first reads authority, including after an ambiguous
      // transport failure. A failed read never permits another POST.
      if (needsRead.current.has(key)) {
        const detail = await queryClient.fetchQuery({
          queryKey: detailKey(target.type, target.identity),
          queryFn: () => getConnectorDetail(target.type, target.identity),
          staleTime: 0,
          retry: false,
        })
        if (!actionAllowed(target.action, detail.data.state)) return
      }
      return target.action === 'pause'
        ? pauseConnector(target.type, target.identity)
        : resumeConnector(target.type, target.identity)
    },
    onError: (error, target) => {
      const key = targetKey(target.type, target.identity)
      needsRead.current.add(key)
      setOperations((previous) => ({ ...previous, [key]: { pending: true, error: failureMessage(error) } }))
    },
    onSettled: async (_data, error, target) => {
      const key = targetKey(target.type, target.identity)
      try {
        // Await both active reader refreshes on success AND failure, including
        // 409. Inactive detail stays invalidated for its next visit.
        const refreshes = await Promise.allSettled([
          queryClient.invalidateQueries({ queryKey: detailKey(target.type, target.identity), exact: true }, { throwOnError: true }),
          queryClient.invalidateQueries({ queryKey: summariesKey }, { throwOnError: true }),
        ])
        if (refreshes.some((result) => result.status === 'rejected')) throw new Error('Refresh failed')
      } catch {
        needsRead.current.add(key)
        if (!error) {
          setOperations((previous) => ({ ...previous, [key]: {
            pending: true,
            error: 'Change accepted, but status refresh failed. Try the action again to refresh state before another write.',
          } }))
        }
      } finally {
        inFlight.current.delete(key)
        setOperations((previous) => ({ ...previous, [key]: {
          pending: false, error: previous[key]?.error ?? null,
        } }))
      }
    },
  })

  function perform(action: Action, confirmedState: string) {
    const key = targetKey(type, identity)
    if (!type || !identity || inFlight.current.has(key)) return
    if (!actionAllowed(action, confirmedState)) return
    // Synchronous lock covers repeated activation before React can rerender.
    inFlight.current.add(key)
    setOperations((previous) => ({ ...previous, [key]: { pending: true, error: null } }))
    mutation.mutate({ type, identity, action })
  }

  const operation = operations[targetKey(type, identity)]
  return { perform, isPending: operation?.pending ?? false, error: operation?.error ?? null }
}
