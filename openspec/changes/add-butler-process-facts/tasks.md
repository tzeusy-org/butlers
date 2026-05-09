## 1. Backend Contract

- [ ] 1.1 Add a process facts response model for butler detail data with `container_name`, `port`, `registered_duration_seconds`, and `config_path`; do not add `pid`.
- [ ] 1.2 Extend `GET /api/butlers/{name}` to populate `port` from the existing butler config/connection info and `config_path` as the roster-relative path `roster/<name>/butler.toml`.
- [ ] 1.3 Populate `container_name` from the dashboard MCP host (`BUTLERS_HOST` / connection host), falling back to the switchboard registry endpoint host if needed.
- [ ] 1.4 Populate `registered_duration_seconds` from switchboard registry liveness timestamps, preferring `registered_at` as the running-since source and using heartbeat data for freshness/null handling.
- [ ] 1.5 Add backend tests for all four fields and a regression assertion that no `pid` field appears in the serialized butler detail response.

## 2. Frontend Overview Card

- [ ] 2.1 Update frontend API types/client fixtures for the process facts object without introducing `pid`.
- [ ] 2.2 Add an Overview tab process facts card as a compact key/value grid showing container name, port, liveness duration, and config path.
- [ ] 2.3 Render explicit unavailable placeholders when a process fact is null or missing.
- [ ] 2.4 Add RTL coverage asserting the process facts rows are exactly the expected four labels and no process ID row or label renders.

## 3. Verification

- [ ] 3.1 Run focused backend API tests for the butler detail endpoint.
- [ ] 3.2 Run focused frontend tests for the Overview tab process facts card.
- [ ] 3.3 Run `openspec validate add-butler-process-facts --strict`.
