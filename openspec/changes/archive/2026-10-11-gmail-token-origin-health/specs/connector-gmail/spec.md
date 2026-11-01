## MODIFIED Requirements

### Requirement: Aggregated Health Status

The Gmail connector SHALL expose a single aggregated health status covering
every account loop, reporting the worst-case status across accounts together
with per-account detail.
The source-failure classifier SHALL report OAuth reauthorization only when the actual HTTP response is bound to a POST request to exactly `https://oauth2.googleapis.com/token` and its parsed top-level `error` is the literal `invalid_grant` or `unauthorized_client`. Gmail data-API failures, unavailable request provenance, wrong methods/origins, nested error codes and diagnostic text SHALL NOT supply reauthorization authority. Their existing failure remains visible as degraded source health. This does not authorize mutating shared account status, credential fallback, a new token retry, altered backoff or changed payload/privacy fields. A successful source call SHALL retain the existing healthy reset.

ID: REQ-connector-gmail-001
Source: https://github.com/tzeusy-org/butlers/blob/5eb4b330ec56c3e1c2d6db8cb7cdfeb547caefd7/openspec/changes/gmail-token-origin-health/design.md#governing-authority-and-provenance ; https://github.com/tzeusy-org/butlers/blob/5eb4b330ec56c3e1c2d6db8cb7cdfeb547caefd7/openspec/changes/gmail-token-origin-health/design.md#minimal-implementation ; existing canonical connector-gmail Aggregated Health Status
Scope: v1-mandatory

#### Scenario: Exact token revocation remains actionable
- **WHEN** the actual Gmail token POST returns a literal top-level revocation code
- **THEN** only that account's source health reports the existing actionable error
- **AND** sibling account loops and shared credential/account state are unchanged by classification

#### Scenario: Data-origin and ambiguous failures remain visible without revocation authority
- **WHEN** a data-API response, unknown request provenance or a wrong token request method/origin contains the same literal error code
- **THEN** its source failure remains visible as degraded rather than an OAuth reauthorization error
- **AND** the original failed-call metrics, diagnostic format, cursor/cache retention and request exception remain intact

#### Scenario: Ordinary nested errors and source success preserve existing health
- **WHEN** a source fails with an ordinary nested data error and later succeeds
- **THEN** the original degraded state and failure diagnostics remain visible until the successful call
- **AND** success clears the existing failure/auth flags and returns the account's original healthy payload

#### Scenario: Health model (multi-account)
- **WHEN** the Gmail connector's health is queried
- **THEN** it returns: `status` (worst-case across all account loops), `uptime_seconds`, `active_accounts` (count), `account_health` (array of per-account status objects)
- **AND** each per-account status includes: `email`, `endpoint_identity`, `status` (`healthy`/`degraded`/`error`), `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity`, `error` (if any)
