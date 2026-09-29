# Fleet and QA-patrol conditions

This runbook covers the independent controller for
`REQ-butler-control-plane-liveness-005` and `REQ-staffer-qa-007/008`
(`src/butlers/core/fleet_conditions.py`). It runs in the Dashboard API process
after each cycle of the supervised receiver observer
(`fleet_shadow_observer`). It does not depend on QA's scheduler or a routable
QA registry row. It writes durable episodes to `public.infra_conditions`.
After both checks, each cycle also asks the runtime-attention producer for one
owner-attention episode per active fleet or `patrol_overdue` condition. See
[Runtime Attention](runtime-attention.md#fleet-and-qa-condition-attention).
`patrol_stopped_by_policy` is an intentional hold and never pages.

## Conditions

| Source | Identity | Opens when | Resolves when |
| --- | --- | --- | --- |
| `control_plane_fleet` | One fixed, versioned fingerprint | At least one expected, non-paused daemon is not receiver-ready | A complete observer cycle finds every non-paused daemon ready |
| `qa_patrol_assurance` | `patrol_overdue` | No qualifying QA patrol completed within twice `[modules.qa].patrol_interval_minutes` | A qualifying patrol is inside that window |
| `qa_patrol_assurance` | `patrol_stopped_by_policy` | The same absence while QA is `paused`, `quarantined`, or `review_required/operator` | As above, or the owner releases QA and it becomes `patrol_overdue` |

- The fleet episode's `metadata.affected` lists each affected daemon with its
  probe category and policy. Thirteen daemons made stale by one observer fault
  are one episode, not thirteen.
- An incomplete or failed cycle adds evidence and never resolves an episode.
  A daemon it could not observe is unknown, not healthy.
- A daemon whose owner policy is `paused` is an intentional exclusion.
  Quarantine and review holds still count as affected.
- If QA's owner policy cannot be read, an overdue patrol is recorded with
  `policy_known=false` and neither QA identity is resolved.

## Qualifying QA patrols

A `public.qa_patrols` row (migration `core_250`) proves current coverage only
when every one of these is true:

- It has `origin='scheduled'` and `discovery_complete=true`.
- Its `status` is `clean`, `findings_dispatched`, or `suppressed`. A suppressed
  patrol completed discovery and then filtered its findings by cooldown or
  severity, so it counts.
- Its `enabled_sources_config_digest` matches the roster's current
  `[modules.qa].enabled_sources`.

The QA module sets `discovery_complete` on completion. It is true only when
every source in the row's own captured snapshot ran and no error was recorded.
These rows never qualify:

- `running`, `error`, and `skipped_overlap` rows.
- Dashboard synthetic placeholders, which have `origin='operator_synthetic'`.
- Rows written under a different enabled-source configuration.
- Legacy rows, which keep null provenance.

The migration does not backfill any of this. Right after upgrading, the
`patrol_overdue` condition opens until the first qualifying patrol completes.

## Fleet-condition handoff

`BUTLERS_FLEET_CONDITION_HANDOFF` defaults to `0` in every Compose service.
Set it to `1` in both the Dashboard API and the daemon process to switch on
the handoff:

- QA's `infra_state` source stops emitting per-butler `ButlerHeartbeatStale`
  findings. Instead it records one `FleetControlCondition` finding whose
  fingerprint is the fleet episode's. Dispatch Gate 5.5 suppresses that
  finding against `control_plane_fleet`, so no per-daemon investigation starts.
  The finding is still written to `qa_findings` even when no model or GitHub
  authority is available.
- Open legacy per-butler episodes stay readable. The fleet episode lists them
  in `metadata.linked_legacy_conditions`.
- QA's own complete snapshot carries those legacy episodes forward instead of
  resolving them by omission. The controller resolves each one only after a
  complete cycle observes that exact roster daemon healthy. It records
  `resolution_reason=complete_receiver_snapshot_healthy`. Switching the flag on
  does not count as recovery.

To roll back, set the flag to `0` in both processes. Condition history is
retained, and the ordinary complete-snapshot lifecycle continues. Do not
delete `control_plane_fleet` or `qa_patrol_assurance` rows to clear a page.
Let a complete, healthy snapshot resolve them.

## Diagnosis

```sql
SELECT source, fingerprint, state, first_detected_at, summary, metadata
FROM public.infra_conditions
WHERE source IN ('control_plane_fleet', 'qa_patrol_assurance')
ORDER BY first_detected_at DESC LIMIT 10;
```

The Dashboard log line `Shadow fleet observation: complete=... expected=...`
shows whether the last cycle could resolve anything. A logged
`Fleet condition controller: ... failed` error means that check wrote nothing
on that cycle. The other check still ran.
