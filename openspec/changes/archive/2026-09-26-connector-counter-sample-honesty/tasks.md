## Tasks

- [x] Normalize heartbeat Counter-family samples by exact `*_total` sample
      name and finite numeric validity.
- [x] Return a typed internal unavailable state when any required counter
      field has no usable sample without changing the heartbeat wire schema.
- [x] Harden the Prometheus fanout availability signal with exact-family,
      finite-scalar filtering and an explicit `meta.aggregates_available` state.
- [x] Ground fanout availability in the emitted Switchboard subroute counter,
      using bounded connector provenance plus a required destination label;
      keep exact connector and endpoint dimensions in the DB projection.
- [x] Wire the fanout client, 120-second query hook, and accessible Connectors
      routing distribution so unavailable and measured-empty states stay
      distinct.
- [x] Add strict behavior tests and additive data-state specification
      scenarios.
