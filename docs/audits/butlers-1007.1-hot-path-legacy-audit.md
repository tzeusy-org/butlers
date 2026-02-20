# butlers-1007.1: Hot-Path Legacy/Compatibility Branch Audit

Date: 2026-02-20
Owner: worker `agent/butlers-1007.1`
Scope: runtime and startup code on the current dev path (`./dev.sh` -> dashboard/api -> daemon/modules/connectors -> switchboard routing/delivery).

## Inventory

| # | Compatibility branch | File path | Current caller(s) | Hot-path reachability | Proposed action | Rationale |
|---|---|---|---|---|---|---|
| 1 | Dashboard path env alias: `TAILSCALE_PATH_PREFIX` fallback to `TAILSCALE_DASHBOARD_PATH_PREFIX` | `dev.sh:143` | Used by `_tailscale_serve_check` path mapping (`dev.sh:343`, `dev.sh:372`) during startup (`dev.sh:467`) | Direct on default startup (`--skip-tailscale-check` off) | **Remove** | Canonical env var already exists; alias expands startup contract surface without current-path need. |
| 2 | Tailscale CLI syntax fallback (new flags -> legacy positional form) | `dev.sh:277` | `_run_serve_mapping` from `_tailscale_serve_check` (`dev.sh:376`, `dev.sh:388`) | Conditional on `tailscale serve` flag mismatch | **Keep (for now)** | This is compatibility with older local tailscale binaries; remove only after declaring/enforcing minimum tailscale version. |
| 3 | Google credential legacy checks: `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` + `GMAIL_*` aliases in env/file probes | `dev.sh:549` | `_oauth_gate` precheck (`dev.sh:665`, `dev.sh:727`) and Gmail pane bootstrap (`dev.sh:773`) | Direct on every startup | **Remove** | Dev runtime now uses canonical Google keys + shared DB (`butler_secrets`); legacy aliases and JSON blob branch are stale. |
| 4 | Calendar startup step-2 fallback via raw DB pool (`resolve_google_credentials(pool)`) after CredentialStore check | `src/butlers/modules/calendar.py:3018` | `CalendarModule.on_startup` (`src/butlers/modules/calendar.py:3079`) called by daemon module startup (`src/butlers/daemon.py:834`) | Conditional when step-1 CredentialStore lookup misses | **Remove** | Daemon already injects layered `CredentialStore`; step-2 primarily preserves legacy table/raw-pool path. |
| 5 | Google credential helper accepts raw asyncpg conn/pool and dispatches to `_legacy_*` `google_oauth_credentials` helpers | `src/butlers/google_credentials.py:196`, `src/butlers/google_credentials.py:564` | Indirect via calendar step-2 (`src/butlers/modules/calendar.py:3022`) and any raw-conn callers | Conditional; dormant on canonical CredentialStore flow | **Remove** | Canonical runtime persistence is `butler_secrets` via `CredentialStore`; legacy table compatibility is cleanup debt. |
| 6 | Switchboard `deliver` tool accepts legacy positional args (`channel/message/recipient`) instead of requiring `notify_request` envelope | `src/butlers/daemon.py:2425` | MCP `deliver` handler forwards to switchboard deliver (`src/butlers/daemon.py:2438`) | Endpoint is hot; legacy branch is conditional on caller payload | **Remove** | Current outbound path uses `notify.v1` (`src/butlers/daemon.py:2719`); positional shape is backward-compat shim. |
| 7 | Notify shim builds `notify.v1` from legacy args (`_build_notify_request_from_legacy_args`) | `roster/switchboard/tools/notification/deliver.py:87` | Called from `deliver()` legacy branch (`roster/switchboard/tools/notification/deliver.py:374`) | Conditional when `notify_request` missing for non-switchboard callers | **Remove** | Control-plane contract is already `notify.v1`; shim enables stale callers and duplicates validation boundary. |
| 8 | Butler modules endpoint accepts legacy `status().modules` list format | `src/butlers/api/routers/butlers.py:328` | `get_butler_modules` (`src/butlers/api/routers/butlers.py:417`) via dashboard/API calls | On-demand API hot path; fallback branch conditional | **Remove** | Current daemon `status()` returns dict shape (`src/butlers/daemon.py:1564`); legacy list parsing is no longer needed on dev path. |
| 9 | Route call result parser has list-of-block fallback for older MCP response shape | `roster/switchboard/tools/routing/route.py:481` | Routing helper used by route/delivery flows (`src/butlers/daemon.py:2392`, `roster/switchboard/tools/notification/deliver.py:243`) | Always in call path; fallback branch conditional | **Remove** | Runtime is standardized on FastMCP 2.x `result.data`; legacy block parsing is transitional compatibility. |
| 10 | Connector MCP client parser has list-of-block fallback for older MCP response shape | `src/butlers/connectors/mcp_client.py:156` | Used by connectors for `ingest`/heartbeat calls (e.g. `src/butlers/connectors/gmail.py:1170`, `src/butlers/connectors/telegram_bot.py:705`, `src/butlers/connectors/telegram_user_client.py:393`) | Always in connector call path; fallback branch conditional | **Remove** | Same FastMCP compatibility debt as #9; removing aligns connector path with current response contract. |
| 11 | OAuth router credential pool fallback to first butler pool when shared pool unavailable | `src/butlers/api/routers/oauth.py:94` | Used by OAuth endpoints through `_make_credential_store` (`src/butlers/api/routers/oauth.py:246`, `src/butlers/api/routers/oauth.py:445`, `src/butlers/api/routers/oauth.py:737`) | On-demand OAuth/status endpoint path | **Keep (for now)** | Not legacy payload compatibility; acts as resilience fallback when shared pool wiring is unavailable in partial/test startup modes. |

## Follow-on breakdown

- `butlers-1007.2` should cover items **3, 4, 5** (credential/env compatibility removal).
- `butlers-1007.3` should cover items **6, 7, 8** (notify/status compatibility shims).
- New follow-on task needed for items **9, 10** (FastMCP list-of-block compatibility removal).
- Optional cleanup (separate low-risk task): item **1** (`TAILSCALE_PATH_PREFIX` alias) once env contract change is announced.
- Item **2** should remain until a minimum tailscale CLI version policy is documented and enforced.

## Implementation order suggestion

1. Remove credential compatibility paths first (item 3 -> 5) to collapse dual-source behavior.
2. Remove notify/status shims (item 6 -> 8) after updating any straggler callers/tests.
3. Remove MCP list-of-block fallbacks in one change set (item 9 + 10) with switchboard + connector test updates together.
4. Handle startup-script env contract cleanup (item 1) last to minimize operator disruption.
