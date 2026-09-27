/**
 * ConnectorFanoutMatrix — observed cross-butler routing distribution.
 *
 * The Switchboard fanout endpoint is intentionally separate from the
 * DB-sourced connector roster. A usable Prometheus aggregate is the authority
 * for this table; an explicit unavailable response never falls through to a
 * calm empty state or an all-clear count.
 */

import type { ConnectorFanoutRow } from '@/api/types'
import { SourceDegradedNote } from '@/components/ui/query-boundary'
import { useConnectorFanout } from '@/hooks/use-ingestion'

const FANOUT_PERIOD = '7d'

function formatCount(value: number): string {
  return value.toLocaleString()
}

function sourceLabel(row: ConnectorFanoutRow): string {
  return `${row.connector_type} · ${row.endpoint_identity}`
}

/**
 * Renders a compact route table on the active Connectors page.
 *
 * A row-per-route table stays legible on narrow screens while preserving the
 * source → destination boundary that a wide dynamic matrix would obscure.
 */
export function ConnectorFanoutMatrix() {
  const {
    data: fanout,
    isError,
    isLoading,
    refetch,
  } = useConnectorFanout(FANOUT_PERIOD)

  const aggregatesAvailable = fanout?.meta?.aggregates_available === true

  if (isLoading && !fanout) {
    return (
      <section
        className="mt-9"
        aria-labelledby="connector-fanout-heading"
        data-testid="connector-fanout-loading"
      >
        <p id="connector-fanout-heading" className="font-mono text-[10.5px] text-muted-foreground" role="status">
          Loading routing distribution…
        </p>
      </section>
    )
  }

  if (!aggregatesAvailable) {
    return (
      <section className="mt-9" aria-labelledby="connector-fanout-heading">
        <h2
          id="connector-fanout-heading"
          className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground"
        >
          routing distribution · 7d
        </h2>
        <SourceDegradedNote
          label="routing metrics"
          detail={
            isError
              ? 'unavailable, the latest routing distribution could not be loaded'
              : 'unavailable, no routing distribution can be confirmed'
          }
          onRetry={() => void refetch()}
          className="mt-3"
          testId="connector-fanout-unavailable"
        />
      </section>
    )
  }

  const rows = fanout.data

  return (
    <section
      className="mt-9"
      aria-labelledby="connector-fanout-heading"
      data-testid="connector-fanout"
    >
      <div className="flex items-baseline justify-between gap-4 border-b border-border pb-2.5">
        <div>
          <h2
            id="connector-fanout-heading"
            className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground"
          >
            routing distribution · 7d
          </h2>
          <p className="mt-1 font-serif text-[13px] italic text-muted-foreground">
            Observed routes from each connector to its destination butler.
          </p>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground/60">
          Counter observations
        </span>
      </div>

      {isError && (
        <SourceDegradedNote
          label="routing metrics"
          detail="refresh unavailable, showing the last measured routing distribution below"
          onRetry={() => void refetch()}
          className="mt-3"
          testId="connector-fanout-stale"
        />
      )}

      {rows.length === 0 ? (
        <p
          className="py-6 font-serif italic text-[14px] text-muted-foreground"
          data-testid="connector-fanout-empty"
          role="status"
        >
          No routed messages recorded in the last 7 days.
        </p>
      ) : (
        <div className="mt-3">
          <table className="w-full table-fixed border-collapse text-left">
            <caption className="sr-only">
              Connector-to-butler routing distribution for the last 7 days
            </caption>
            <thead>
              <tr className="border-b border-border/50">
                <th
                  scope="col"
                  className="w-[48%] py-2 font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
                >
                  source
                </th>
                <th
                  scope="col"
                  className="w-[32%] py-2 font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
                >
                  destination
                </th>
                <th
                  scope="col"
                  className="w-[20%] py-2 text-right font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
                >
                  messages
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={`${row.connector_type}:${row.endpoint_identity}:${row.target_butler}`}
                  className="border-b border-border/40"
                >
                  <th scope="row" className="py-3 pr-4 align-top font-normal">
                    <span className="block text-[13px] font-medium capitalize text-foreground">
                      {row.connector_type}
                    </span>
                    <span className="block break-words font-mono text-[10px] text-muted-foreground/60">
                      {row.endpoint_identity}
                    </span>
                  </th>
                  <td className="break-words py-3 pr-4 align-top font-mono text-[11px] text-muted-foreground">
                    {row.target_butler}
                  </td>
                  <td className="py-3 text-right align-top font-mono text-[13px] tabular-nums">
                    <span aria-label={`${formatCount(row.message_count)} messages from ${sourceLabel(row)} to ${row.target_butler}`}>
                      {formatCount(row.message_count)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
