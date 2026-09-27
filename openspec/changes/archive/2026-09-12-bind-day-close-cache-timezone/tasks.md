## 1. Tuple contract and API boundary

- [x] 1.1 Add a single exact `(date, timezone)` day-close cache-key helper and
  route all writer, reader, staleness, rate-limit, and editorial cache lookups
  through it.
- [x] 1.2 Require and validate `tz` on day-close GET and refresh before cache
  work, without a timezone default or a legacy-row fallback.
- [x] 1.3 Preserve legacy date-only rows unchanged while ensuring tuple-keyed
  reads treat them as cache misses.
- [x] 1.4 Add a collision-safe exact-tuple writer lock that cannot make two
  different accepted timezone strings contend through a hash collision.

## 2. Dashboard and documentation contract

- [x] 2.1 Require timezone in the typed day-close frontend read/refresh clients
  and query keys, include it in both requests, and regenerate only the selected
  stale tuple.
- [x] 2.2 Update the backend/frontend API contract with the tuple-key format,
  mandatory timezone, collision-safe lock behavior, and preserved legacy-row
  transition.

## 3. Regression coverage and verification

- [x] 3.1 Add focused RED/GREEN coverage for tuple cache identity, exact
  timezone validation/OpenAPI, legacy misses, collision-safe writer locks,
  staleness, and refresh rate-limit isolation.
- [x] 3.2 Add focused frontend client/query-key and stale-regeneration
  regressions for timezone propagation and same-date isolation.
- [x] 3.3 Run strict OpenSpec validation, targeted Chronicler and frontend
  tests, and the right-sized repository quality gates.
