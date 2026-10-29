# Owner data export

Settings → Permissions → Export data provides **All data (supported scopes)**,
Memory, Audit log and Config. The palette export action uses the selected scope.
This is a finite export, not a full backup or a consistent global snapshot:
tables are read sequentially without a shared cross-database transaction.

| Wire scope | Included data and archive members |
| --- | --- |
| `memory` | Facts, rules and episodes from every configured memory butler through its own pool and effective memory schema; `<butler>_memory_<table>.ndjson`. |
| `audit` | `public.audit_log`; `public_audit_log.ndjson`. |
| `config` | Each registered butler’s own `runtime_config` through its own pool and effective core schema, ordered by native primary key `butler_name`; rows combine in the legacy logical member `public_runtime_config.ndjson`. Shared `public.model_catalog` and `public.permissions` retain their corresponding NDJSON members. |
| `all` | The union above. `full` remains a wire alias. |

Other domain records, other brain tables, sessions, schedules and credential
stores are excluded. Adding domain scopes requires separate authority.

Owner-authenticated `POST /api/data/export` accepts `{"scope":"all"}` and
returns a signed URL with a 60-minute TTL. Downloading builds a ZIP, then
encrypts it with AES-256-GCM using `DASHBOARD_EXPORT_ENCRYPTION_KEY`: a 12-byte
nonce followed by ciphertext and a 16-byte authentication tag. Follow the
module’s decryption example in `src/butlers/api/routers/data_ops.py` in a
trusted environment, protecting the key and downloaded owner data. Production
requires configured encryption and signing secrets. Session-based unsafe
requests retain owner CSRF checks. Issuing an export records `data.export`
through the existing audit path.

Each member contains native JSON rows, one per line. Generated/large
`embedding`, `search_vector` and `description_embedding` columns are omitted.
An existing empty table produces a named, empty NDJSON member. A configured
butler missing a required pool produces HTTP 503. A failed resolved query
produces HTTP 500 without returning a partial archive. No foreign pool or
shared runtime-table retry is attempted. Legacy unqualified schema resolution
stays on the same source pool; census-free helper callers use their supplied
pool’s local runtime table.

Wipe remains disabled and returns HTTP 503 without destruction. Export changes
neither database grants nor the disabled wipe contract.
