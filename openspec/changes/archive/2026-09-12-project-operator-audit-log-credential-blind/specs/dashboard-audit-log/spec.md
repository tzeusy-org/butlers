## ADDED Requirements

### Requirement: Credential-Target Audit Free Text Is Withheld On Read
Every read surface that publishes a `public.audit_log` row SHALL withhold that
row's free-text columns — `note`, `error`, and `metadata` — when the row's
`target` names a credential. A credential target is any `target` whose scope
segment is a credential-key scope from `core-credentials` §Credential-Key
Normalisation Function: `u:`/`user:`, `s:`/`system:`, or `c:`/`cli:`. Rows with
any other target, or no target, are unaffected and keep publishing their free
text: this is a namespace carve-out, not a blanket gag on operator diagnostics.

This requirement NARROWS the `Audit Log Read API` projection clause ("each
returned `AuditLogEntry` projects `metadata`/`result`/`error` … alongside the
base columns"). Where the two speak about the same row, this one governs. It is
the same rule `dashboard-api` §`Secrets Inventory and Per-Credential Read
Endpoints` already applies to the secrets surfaces, extended to the general
operator log so the two cannot disagree about the same stored text.

The withholding is enforced on the `AuditLogEntry` model rather than in each
route, because the `u:`/`s:`/`c:` audit namespaces have at least four producers
(`_write_credential_audit`, `_write_system_audit`, `_write_cli_audit`,
`routers/oauth.py::_emit_oauth_audit`, `jobs/secrets_lifecycle`) and at least
three readers, and a new one of either can appear at any time. Read-side
projection at a single chokepoint is the only enforcement point that holds.

Server-side evidence is untouched. Writers keep persisting the note and the
error, and the free text still reaches `public.audit_log`,
`public.secret_probe_log`, and the `last_test_message` cache. Content blindness
is about the wire, not about destroying operator forensics — the operator reads
a raw provider string at the database, which is where it belongs.

#### Scenario: Credential-target row is published without its free text
- **WHEN** `GET /api/audit-log` or `GET /api/audit-log/{id}` returns a row whose
  `target` names a credential (e.g. `u:google`, `s:BUTLER_TELEGRAM_TOKEN`,
  `c:claude`)
- **THEN** the serialized entry's `note`, `error`, and `metadata` are `null`
- **AND** `ts`, `actor`, `action`, `target`, `result`, `ip`, and `request_id`
  are published unchanged — the row stays identifiable, attributable, and
  filterable by `?key=`
- **AND** the response body contains no part of the withheld text, including
  the provider failure tail a probe writes into `note` as
  `"Probe failed: <provider text>; probe_status=<token>"`.

#### Scenario: Withholding is visible rather than silent
- **WHEN** a credential-target row that actually carried a `note`, an `error`,
  or `metadata` is published
- **THEN** the entry carries `redacted: true`, so an operator reading a blank
  Note field learns the text was withheld rather than never recorded
- **AND** a credential-target row that carried none of the three publishes
  `redacted: false` — the marker reports a real withholding and is never
  decorative
- **AND** a non-credential row always publishes `redacted: false`.

#### Scenario: Non-credential operator rows keep their diagnostics
- **WHEN** a row whose `target` is absent or names a non-credential resource
  (e.g. `butler:qa`, `rule:7`, a request path) is published
- **THEN** its `note`, `error`, and `metadata` are published verbatim
- **BECAUSE** the general operator audit log has a real forensic claim on its
  own free text; only the credential namespaces are governed by the
  content-blindness rule the secrets surfaces already carry.

#### Scenario: The secrets deep link cannot re-expose what secrets withheld
- **WHEN** an operator follows the `meta.deep_link` that
  `GET /api/secrets/audit/<scope>/<key>` returns
  (`/audit-log?key=<canonical-key>`) and the resulting page reads
  `GET /api/audit-log?key=<canonical-key>`
- **THEN** every row on that page is a credential-target row and is therefore
  published without its free text
- **AND** the deep link remains useful: it is the full reel of a credential's
  audit rows, carrying the same `ts`/`actor`/`action` evidence the secrets
  StampRow shows, plus `result` and the row id
- **BECAUSE** a signposted path that re-publishes exactly what
  `dashboard-api` §`Secrets Audit-History and Breaks-Catalogue Endpoints`
  just stopped publishing would narrow that fix rather than close it.

#### Scenario: The Issues occurrences drill-down is covered by the same chokepoint
- **WHEN** `GET /api/issues/{issue_key}/occurrences` returns
  `PaginatedResponse[AuditLogEntry]` rows and one of them is a credential-target
  row
- **THEN** that row is published under this requirement exactly as it would be
  by `GET /api/audit-log`
- **AND** a future reader that builds an `AuditLogEntry` without going through
  `AuditLogEntry.from_record` is covered too, because the withholding is a
  property of the model rather than of any one route.
