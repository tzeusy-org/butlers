## Tasks

- [x] Normalize heartbeat Counter-family samples by exact `*_total` sample
      name and finite numeric validity.
- [x] Return a typed internal unavailable state when any required counter
      field has no usable sample without changing the heartbeat wire schema.
- [x] Harden the Prometheus-backed fanout projection with the same filtering
      and an explicit `meta.aggregates_available` state.
- [x] Add strict behavior tests and additive data-state specification
      scenarios.
