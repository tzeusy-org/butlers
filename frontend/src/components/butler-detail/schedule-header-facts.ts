import type { Schedule } from "@/api/types"

interface ScheduleHeaderFact {
  id: string
  name: string
  nextRunAt: string
  timestampMs: number
}

interface ScheduleHeaderFacts {
  overdue: ScheduleHeaderFact | null
  next: ScheduleHeaderFact | null
}

function compareScheduleFacts(left: ScheduleHeaderFact, right: ScheduleHeaderFact): number {
  return (
    left.timestampMs - right.timestampMs ||
    left.name.localeCompare(right.name) ||
    left.id.localeCompare(right.id)
  )
}

/**
 * Select independently truthful overdue and future schedule facts. Disabled
 * and unparsable rows are intentionally ignored rather than guessed at.
 */
export function getScheduleHeaderFacts(schedules: Schedule[], nowMs: number): ScheduleHeaderFacts {
  let overdue: ScheduleHeaderFact | null = null
  let next: ScheduleHeaderFact | null = null

  for (const schedule of schedules) {
    if (!schedule.enabled || !schedule.next_run_at) continue

    const timestampMs = new Date(schedule.next_run_at).getTime()
    if (!Number.isFinite(timestampMs)) continue

    const fact: ScheduleHeaderFact = {
      id: schedule.id,
      name: schedule.name,
      nextRunAt: schedule.next_run_at,
      timestampMs,
    }

    if (timestampMs <= nowMs) {
      if (!overdue || compareScheduleFacts(fact, overdue) < 0) overdue = fact
    } else if (!next || compareScheduleFacts(fact, next) < 0) {
      next = fact
    }
  }

  return { overdue, next }
}
