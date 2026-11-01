## 1. Grants S1 and glance S1

- [x] 1.1 Migration `core_291` creates `public.ambient_projections` with a SELECT-only RLS policy and revoked runtime-role writes.
- [x] 1.2 Owner-session mint, list and revoke API; the token is returned once and only its digest is stored.
- [x] 1.3 `OwnerAuthMiddleware` exact-route grant branch: GET only, no body, 404 for unknown or revoked grants.
- [x] 1.4 JSON glance with briefing-classify parity, household redaction, `valid_until` and no model call.

## 2. Deferred

- [x] 2.1 Consumer liveness: one attention item per lapsed grant, derived on read and cleared by the next fetch.
- [ ] 2.2 Settings > Projections UI with the last-fetch column and the revoke verb.
- [x] 2.3 Text and 1-bit PNG glance formats with `valid_until` printed, plus a per-grant response cache.
- [x] 2.4 Home Assistant package example under `docs/` with an edge staleness template.
