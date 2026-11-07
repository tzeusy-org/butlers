## Purpose

Make the public MCP server safe to reach from the internet even if the Cloudflare gateway or anything between it and the server is compromised. Every data call needs a hop bearer and a fresh Worker-signed assertion bound to its exact body; the server is network-confined; and the owner can switch off any domain, contain leaks, cap use and audit every call.

## ADDED Requirements

### Requirement: Hop bearer verification before body parsing
The public server SHALL refuse with HTTP 401, before reading or parsing the request body, any request whose `Authorization` header is not `Bearer <token>` with `SHA-256(token)` equal, by constant-time comparison, to a configured bearer digest. It SHALL hold only bearer digests, file-mounted from a Secret in its own namespace, and SHALL accept at most two digests (current and next) so the bearer can rotate without downtime. A valid bearer SHALL never establish a user identity and SHALL never by itself authorize a `tools/call`.

ID: REQ-public-mcp-authorization-001
Source: RFC 0039 D6; owner decision OD3; craft-and-care/security-and-secrets.md Secret Handling
Scope: v1-mandatory

#### Scenario: Missing bearer is refused before parsing
- **WHEN** a request arrives with no `Authorization` header and a malformed JSON body
- **THEN** the response SHALL be HTTP 401 and the body SHALL not be parsed

#### Scenario: Wrong bearer is refused
- **WHEN** a request carries a bearer whose digest matches neither configured digest
- **THEN** the response SHALL be HTTP 401 and no tool code SHALL run

#### Scenario: Bearer rotation accepts both digests
- **WHEN** the current and next digests are configured and requests arrive with either bearer
- **THEN** both SHALL pass the bearer check, and after the old digest is removed the old bearer SHALL be refused

#### Scenario: Bearer alone cannot call a tool
- **WHEN** a `tools/call` carries a valid bearer and no assertion
- **THEN** it SHALL be refused as unauthorized

### Requirement: Request shape limits
After the bearer check, the server SHALL accept only a single JSON-RPC object (no batch array) of at most 1 MiB, parsed by a strict parser that refuses duplicate object keys and non-standard JSON constants. Any other body SHALL be refused as unauthorized before tool code runs, and the server SHALL serve no SSE `GET` stream.

ID: REQ-public-mcp-authorization-002
Source: RFC 0039 D1 (stateless HTTP) and D7 verification step 1
Scope: v1-mandatory

#### Scenario: Oversized body is refused
- **WHEN** a `POST` body is larger than 1 MiB
- **THEN** it SHALL be refused as unauthorized and no tool code SHALL run

#### Scenario: Batch request is refused
- **WHEN** the body is a JSON array of two `tools/call` requests
- **THEN** it SHALL be refused and neither call SHALL run

#### Scenario: Duplicate keys are refused
- **WHEN** the body contains `"name"` twice in `params`
- **THEN** it SHALL be refused rather than resolved to either value

### Requirement: Assertion required for every data method
Only `initialize`, `notifications/initialized`, `notifications/cancelled`, `ping`, `tools/list` and `server/discover` MAY arrive without an `X-MCP-Assertion` header; they SHALL return static content and SHALL open no database connection. Every other method, `tools/call` included, SHALL be refused as unauthorized without a valid assertion. An assertion present on an exempt method SHALL still be verified.

ID: REQ-public-mcp-authorization-003
Source: RFC 0039 D7 verification step 2
Scope: v1-mandatory

#### Scenario: Handshake works without an assertion
- **WHEN** `initialize` and `tools/list` arrive with a valid bearer and no assertion
- **THEN** both SHALL succeed and no projection connection SHALL be opened

#### Scenario: Tool call without assertion is refused
- **WHEN** `tools/call` for `search` arrives with a valid bearer and no assertion
- **THEN** the response SHALL be JSON-RPC error `-32001` and no projection query SHALL run

#### Scenario: A bad assertion on an exempt method is refused
- **WHEN** `tools/list` arrives with an assertion whose signature does not verify
- **THEN** it SHALL be refused as unauthorized

### Requirement: Assertion signature and identity checks
The server SHALL accept an assertion only in compact three-segment form whose header has exactly the fields `alg`, `kid` and `typ`, with `alg` = `EdDSA` and `typ` = `butlers-mcp-assertion+v1`, where `kid` names a current or retiring key in the verifier keyring, the Ed25519 signature verifies, the payload bytes equal their RFC 8785 canonical re-encoding, `v` is 1, `iss` and `aud` equal configuration, `sub` is in the home-side user allowlist, and `cid` satisfies the client policy. The client policy SHALL accept a `cid` that is an `https` client-metadata URL whose lowercased host equals an entry of the home-side known-domain list (v1: `claude.ai` and `chatgpt.com`), or an opaque registered client id equal to an entry of the home-side registered-client list (empty by default), and SHALL refuse an empty `cid`. The verifier SHALL reuse the `butlers.core.runtime_probe_control` signed codec with a per-protocol exact header field set (`{alg, kid}` for probe capabilities, `{alg, kid, typ}` with `typ` required for assertions), so neither protocol accepts the other's tokens, and SHALL never accept an RFC 0038 broker assertion in place of this one.

ID: REQ-public-mcp-authorization-004
Source: RFC 0039 D7 (format and verification steps 3 and 4); owner decision OD3
Scope: v1-mandatory

#### Scenario: Unknown key id is refused
- **WHEN** an assertion names a `kid` that is not in the keyring
- **THEN** the call SHALL be refused as unauthorized

#### Scenario: Non-canonical payload is refused
- **WHEN** a correctly signed payload has its keys in non-canonical order
- **THEN** the call SHALL be refused as unauthorized

#### Scenario: User outside the home allowlist is refused
- **WHEN** a correctly signed assertion carries a `sub` not in the home-side allowlist
- **THEN** the call SHALL be refused as unauthorized, even though the Worker signed it

#### Scenario: Unknown client is refused
- **WHEN** a correctly signed assertion carries a `cid` on a domain other than `claude.ai` or `chatgpt.com`, or a host such as `claude.ai.example.com`
- **THEN** the call SHALL be refused as unauthorized

#### Scenario: Empty client id is refused
- **WHEN** a correctly signed assertion carries `cid` = `""`
- **THEN** the call SHALL be refused as unauthorized

#### Scenario: A registered client id is accepted
- **WHEN** the registered-client list holds an opaque id and a correctly signed assertion carries exactly that `cid`
- **THEN** the client policy check SHALL pass

#### Scenario: Probe capabilities and assertions are not interchangeable
- **WHEN** a valid runtime-probe capability is presented as an assertion, or a valid assertion is presented to the probe verifier
- **THEN** each verifier SHALL refuse it because the header field set does not match

#### Scenario: Broker assertion is not accepted
- **WHEN** a request carries an RFC 0038 broker assertion instead of a Worker assertion
- **THEN** the call SHALL be refused as unauthorized

### Requirement: Assertion freshness, replay and request binding
The server SHALL refuse an assertion unless `iat` is at most 5 seconds in the future, `exp` has not passed, `exp − iat` is at most 60 seconds, and `iat` is no earlier than 5 seconds before the process started. It SHALL refuse a `jti` already seen, keeping each seen `jti` until its `exp`. It SHALL refuse unless `bsha` equals the base64url SHA-256 of the exact received body bytes, and `mth`, `tool` and `path` equal the parsed method, `params.name` (or `""`) and the received query string (the part after `?`, not decoded, or `""`). The path component SHALL not be compared, because the filter-proxy forwards to its fixed upstream path; `aud` binds it. Because the replay set is in memory, the Deployment SHALL run exactly one replica.

ID: REQ-public-mcp-authorization-005
Source: RFC 0039 D7 (verification steps 5 to 7, known limits)
Scope: v1-mandatory

#### Scenario: Expired assertion is refused
- **WHEN** a request arrives 31 seconds after the `iat` of an assertion with `exp = iat + 30`
- **THEN** it SHALL be refused as unauthorized

#### Scenario: Replayed assertion is refused
- **WHEN** the same assertion and body are sent twice within 30 seconds
- **THEN** the first SHALL be served and the second SHALL be refused as unauthorized

#### Scenario: Assertion from before a restart is refused
- **WHEN** the server restarts and an assertion whose `iat` is more than 5 seconds before the process start, still within its `exp`, is presented
- **THEN** it SHALL be refused as unauthorized

#### Scenario: Assertion inside the restart grace window is accepted once
- **WHEN** an assertion whose `iat` is 3 seconds before the process start, still within its `exp`, is presented twice
- **THEN** the first SHALL pass the freshness check and the second SHALL be refused as a replay

#### Scenario: Query string is bound, path is not
- **WHEN** a valid assertion signed with `path` = `""` arrives at the server's upstream path with a query string `x=1` added
- **THEN** the call SHALL be refused because `path` does not equal the received query string

#### Scenario: Body swap is refused
- **WHEN** a valid assertion for a `search` body is sent with a `finance_ledger` body
- **THEN** the call SHALL be refused because `bsha` and `tool` do not match

### Requirement: Uniform non-disclosing authorization refusal
Every authorization refusal after the bearer check SHALL be the same JSON-RPC error, code `-32001` with data `{"reason":"unauthorized"}`, regardless of cause. The specific cause SHALL be recorded only in the audit log and metrics, and a refused request SHALL execute no tool code and open no projection connection.

ID: REQ-public-mcp-authorization-006
Source: RFC 0039 D7 and D17 (auth failures are JSON-RPC errors); REQ-ambient-projections-002 non-disclosure precedent
Scope: v1-mandatory

#### Scenario: Causes are indistinguishable to the caller
- **WHEN** one call is refused for a replayed `jti` and another for a bad signature
- **THEN** both responses SHALL be byte-identical apart from the JSON-RPC `id`
- **AND** the audit log SHALL record `replay` and `signature` as the respective causes

#### Scenario: Refusal touches no data
- **WHEN** any assertion check fails
- **THEN** the projection SHALL receive no query for that request

### Requirement: Keyring custody and rotation
The verifier keyring (public keys and `kid`s) SHALL be read only from a ConfigMap in the public server's own namespace and SHALL never be read from the `mcp` or `mcp-gateway` namespaces. No Worker private key SHALL exist in the cluster. A key marked `retiring` SHALL still verify; a key removed from the keyring SHALL not. Keyring changes SHALL take effect without rebuilding the image.

ID: REQ-public-mcp-authorization-007
Source: RFC 0039 D7 (key custody); bu-jnhxc5 custody pattern
Scope: v1-mandatory

#### Scenario: Rotation keeps clients working
- **WHEN** a new key is added as `current`, the old key is marked `retiring`, and the Worker switches keys
- **THEN** assertions signed with either key SHALL verify during the overlap

#### Scenario: Dropped key stops verifying
- **WHEN** the retiring key is removed from the keyring ConfigMap
- **THEN** assertions signed with it SHALL be refused as unauthorized

### Requirement: Network confinement of the public server
The public server SHALL run only in namespace `butlers-public-<env>`, never in `butlers-<env>`; its chart `deploy/helm/butlers-public-mcp` SHALL create that namespace and the ExternalSecret that reads the Bitwarden item `MCP_PUBLIC_RO_DB_PASSWORD`, and SHALL mount no other database credential. It SHALL run under NetworkPolicies that deny all ingress and egress except: ingress on the MCP port from pods in namespace `mcp` that carry the `butlers` filter-proxy release label; ingress on a separate metrics port from the monitoring namespace; egress on port 5432 to the `postgres-<env>` instance pods, which hold the projection database and also serve the primary; and egress to cluster DNS on port 53. The pod SHALL run as non-root with a read-only root filesystem, no service-account token, and `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. No Butlers Service SHALL be a traefik route target; the only path from the gateway SHALL be the filter-proxy upstream.

ID: REQ-public-mcp-authorization-008
Source: RFC 0039 D3, D8 and D14 (no network route to a model); owner decisions OD3 and OD10
Scope: v1-mandatory

#### Scenario: The primary database refuses the pod
- **WHEN** the public server's pod connects to a `postgres-<env>` instance on 5432 and requests database `butlers`, even with valid primary credentials
- **THEN** the login SHALL be refused by `pg_hba` (REQ-public-mcp-projection-008); the network layer is not the boundary for this case

#### Scenario: Internet egress is blocked
- **WHEN** the pod attempts an outbound HTTPS connection to any model provider or public address
- **THEN** the connection SHALL fail

#### Scenario: Only the filter-proxy release can call in
- **WHEN** another pod in namespace `mcp` without the `butlers` filter-proxy release label connects to the MCP port
- **THEN** the connection SHALL be refused at the network layer

#### Scenario: Rendered chart is checked for widening
- **WHEN** the chart is rendered and any policy in `butlers-public-<env>` selects the pod with a wider ingress or egress rule
- **THEN** the chart contract check SHALL fail

#### Scenario: A policy outside the chart is detected
- **WHEN** a NetworkPolicy or cluster-wide policy object that the chart did not render selects pods in `butlers-public-<env>`, or a policy in `postgres-<env>` admits the public server's namespace to any pod or port other than the instance pods on 5432
- **THEN** the live policy check, run after each deploy and on a schedule, SHALL fail and name the object

### Requirement: Per-domain kill switches without tiers or unlock
Every published table SHALL belong to exactly one domain (`knowledge`, `relationship`, `time`, `travel`, `finance`, `health`, `proposals`), and memory facts SHALL take the domain of their source schema. Every read function SHALL exclude rows from disabled domains. Single-domain tools SHALL return `domain_disabled` when their domain is disabled. The multi-domain tools `search`, `fetch`, `search_filtered` and `timeline` SHALL drop items from disabled domains: `search_filtered` and `timeline` SHALL list each dropped domain in `sources_degraded` with reason `"disabled"`, `search` SHALL return `{results: []}` when every spanned domain is disabled, and `fetch` of an id whose domain is disabled SHALL return `domain_disabled` naming that domain. `describe` SHALL belong to no domain, and `all` SHALL disable every tool except `describe`. Every domain SHALL be served by the same server and process, with no tier server, per-client tier, time-boxed unlock or elevation step. A domain is disabled by any switch layer: a replicated `mcp_surface.controls` row, the `all` row, or the `disabled_domains` file in the break-glass ConfigMap. The ConfigMap SHALL be re-read at least every 10 seconds and SHALL take effect without replication; it and the in-process latch SHALL affect only the public server. Re-enabling SHALL require no step beyond clearing the switch.

ID: REQ-public-mcp-authorization-009
Source: RFC 0039 D4 (domains per table), D5 and D16; owner decision OD3
Scope: v1-mandatory

#### Scenario: Disabled domain refuses its tools
- **WHEN** the `finance` control row is set to disabled and has replicated
- **THEN** `finance_ledger` SHALL return `domain_disabled` with `domain: "finance"`, while `entity_lookup` still succeeds

#### Scenario: All switch stops every data tool
- **WHEN** the `all` control row is disabled
- **THEN** every tool except `describe` SHALL return `domain_disabled`

#### Scenario: Break-glass works with replication stalled
- **WHEN** replication is stalled and the owner adds `health` to the break-glass ConfigMap
- **THEN** within 20 seconds `health_series` SHALL return `domain_disabled`

#### Scenario: Timeline drops a disabled domain
- **WHEN** `travel` is disabled and `timeline` covers a period with both episodes and trip legs
- **THEN** the result SHALL contain the episodes and no legs, and `sources_degraded` SHALL include `{domain: "travel", reason: "disabled"}`

#### Scenario: Search serves no row of a disabled domain
- **WHEN** `travel` is disabled and a query matches a `travel` memory fact
- **THEN** `search` SHALL not return that fact

#### Scenario: Fetch of a disabled domain's id is refused
- **WHEN** `travel` is disabled and `fetch` is called with the id of a trip
- **THEN** the result SHALL be `domain_disabled` with `domain: "travel"`

#### Scenario: Search with every spanned domain disabled is empty
- **WHEN** `knowledge`, `relationship`, `time` and `travel` are all disabled and `search` is called
- **THEN** the result SHALL be `{results: []}`

#### Scenario: Finance and health need no unlock
- **WHEN** finance and health are enabled and an authorized client calls `finance_summary` with no prior owner step
- **THEN** the call SHALL be served

### Requirement: Response DLP scan
Before serializing any tool result, the server SHALL normalize every text field (skipping numeric money and measurement fields) with NFKC and remove characters of Unicode categories Cc and Cf, then scan it. Credential rules SHALL run on every tool result and SHALL cover: Luhn-valid 13- to 19-digit runs, matched after removing spaces and dashes, which in `finance` and `health` tool results SHALL match only with a card word (`card`, `visa`, `mastercard`, `amex`, `credit`, `debit`, `cvv`, `expiry`) within 32 characters, an uncontexted hit there being returned unredacted and counted in a metric; cloud and service token shapes (`AKIA…`, `ghp_…`, `xox…`, `sk-…`, JWTs); PEM blocks; URLs whose authority carries userinfo with a password (`scheme://user:pass@host`); URLs carrying a query parameter whose name, split on `-`, `_` and camelCase boundaries and compared case-insensitively, has a segment equal to `token`, `code`, `sig`, `signature`, `key`, `pwd`, `passcode`, `password`, `credential` or `auth`, or a path segment of 20 or more token characters; high-entropy tokens of 20 or more base64 or hex characters with at least 3.5 bits of entropy per character; and password-reset or OTP phrasing next to a 4- to 8-digit code. Identifier rules SHALL cover mod-97-valid IBANs, 6-character alphanumeric booking codes next to booking words, passport and national-id shapes next to document words, and 8- to 17-digit account numbers next to account words. Personal-contact rules SHALL cover email addresses and phone numbers in E.164 and common local shapes. Identifier and personal-contact rules SHALL run on every tool result except those of the `finance` and `health` tools, whose fields owner decision OD17 shares. On a `finance` or `health` column the manifest marks `dlp = "identifier"`, and on a `json_fields` entry whose exact `(source_table, path)` the manifest lists in `json_dlp_identifier` (REQ-public-mcp-projection-002), the high-entropy rule and the URL path-segment rule SHALL be skipped, and every other credential rule, the userinfo rule included, SHALL still apply. Every other `json_fields` entry SHALL be scanned with every credential rule. Each match SHALL be replaced by `[redacted:<rule>]`. Credential-rule and identifier-rule matches SHALL be counted, and a result with more than 5 counted redactions SHALL be dropped whole and replaced by the error `blocked`; personal-contact matches SHALL be redacted but not counted toward that threshold.

ID: REQ-public-mcp-authorization-010
Source: RFC 0039 D15 (response DLP; DLP scope for finance and health); owner decision OD17
Scope: v1-mandatory

#### Scenario: A card number is redacted
- **WHEN** a published fact's text contains a Luhn-valid 16-digit number
- **THEN** the returned text SHALL contain `[redacted:luhn]` in place of the number

#### Scenario: Separators and invisible characters do not defeat the scan
- **WHEN** a published text holds a Luhn-valid card number written in full-width digits with spaces and a zero-width joiner inside it
- **THEN** the returned text SHALL contain `[redacted:luhn]` in place of the number

#### Scenario: Contact details are redacted without dropping the response
- **WHEN** a result's text contains 7 email addresses and no secret shape
- **THEN** each address SHALL be replaced by `[redacted:email]` and the result SHALL be served, not `blocked`

#### Scenario: A booking code is redacted
- **WHEN** a published travel fact's text reads "booking ref Q7XK2P"
- **THEN** the code SHALL be replaced by `[redacted:pnr]`

#### Scenario: Shared finance and health identifiers are not redacted
- **WHEN** `finance_ledger` returns a transaction whose `description` holds a mod-97-valid IBAN, and `health_profile` returns a provider whose fields hold a phone number
- **THEN** both values SHALL be returned unredacted and the results SHALL not be `blocked`

#### Scenario: Marked reference columns keep long opaque ids
- **WHEN** `finance_records` returns a transaction whose `external_ref`, marked `dlp = "identifier"`, is a 32-character base64 reference
- **THEN** the reference SHALL be returned unredacted

#### Scenario: Marked JSON paths keep long opaque ids
- **WHEN** `finance_records` returns a `facts` item whose `json_fields` entry `external_ref`, listed in `json_dlp_identifier` for `finance.facts`, is a 32-character base64 reference, and whose unlisted entry `note_blob` holds a 32-character high-entropy token
- **THEN** the listed reference SHALL be returned unredacted and the unlisted token SHALL be redacted and counted

#### Scenario: Signed and userinfo URLs are redacted on marked columns
- **WHEN** a marked `receipt_url` holds an S3 pre-signed URL carrying `X-Amz-Signature=...`, or holds `https://u:p@bridge.example/simplefin`
- **THEN** each SHALL be redacted and counted

#### Scenario: Account numbers that pass Luhn are shared in finance
- **WHEN** `finance_ledger` returns a transaction whose `description` holds a Luhn-valid 16-digit bank reference with no card word within 32 characters, and another whose `notes` read "card 4111 1111 1111 1111"
- **THEN** the reference SHALL be returned unredacted and counted in the uncontexted-Luhn metric, and the card number SHALL be replaced by `[redacted:luhn]`

#### Scenario: Credential shapes are redacted in finance and health too
- **WHEN** a finance transaction's `notes` holds a `ghp_` token, or a marked `receipt_url` carries a `token` query parameter, or a Health calendar event's description holds a meeting link with a `pwd` query parameter
- **THEN** each SHALL be redacted and counted

#### Scenario: A flood of matches blocks the response
- **WHEN** a result would contain 6 redactions
- **THEN** the tool SHALL return `blocked` and none of the result's text

#### Scenario: Amounts are not mistaken for secrets
- **WHEN** `finance_ledger` returns an amount field of `"4111111111111111.00"`
- **THEN** the amount field SHALL be returned unredacted because numeric money fields are not scanned

### Requirement: Canary containment
The server SHALL hold the values of canary tokens planted in excluded sources (a `butler_secrets` row, a secured `entity_info` row, a `pii` memory fact in the `general` schema, a `normal` `contact_phone` memory fact in the `relationship` schema, a `restricted` Chronicler override, a fake token under the credential-named key `api_token` in finance transaction `metadata`, a fake password under the credential-named key `portal_password` in a health fact's `metadata`, a fake token under the camelCase credential key `refreshToken` in a health fact's `metadata`, a fake code in `travel.legs.confirmation_number` and a fake channel value in `public.entities.metadata.source_value`). A response containing any canary value SHALL be blocked with `blocked`, SHALL latch every tool except `describe` off in-process until the pod restarts, and SHALL fire a critical alert.

ID: REQ-public-mcp-authorization-011
Source: RFC 0039 D15 (canaries) and D16 (in-process latch); owner decision OD17 (the finance `metadata` canary moved to a credential-named key)
Scope: v1-mandatory

#### Scenario: Canary in a response latches the surface off
- **WHEN** a tool result contains the `butler_secrets` canary value
- **THEN** that response SHALL be `blocked`
- **AND** every subsequent data tool call SHALL return `blocked` until the pod restarts
- **AND** a critical canary alert SHALL fire

#### Scenario: Restart clears the latch only
- **WHEN** the pod restarts after a canary latch
- **THEN** tools SHALL serve again subject to every other check, and the canary alert record SHALL remain

### Requirement: Per-user and per-tool budgets
The server SHALL enforce token-bucket budgets keyed per client: 60 calls per minute per `(sub, cid)` across all tools; for `finance_ledger`, `finance_records`, `health_series` and `health_records`, 20 calls per minute and 20,000 returned rows per day per `(sub, cid, tool)`; 20 `propose_fact` calls per day per `(sub, cid)`; and 600 calls per minute for the whole server. A call over budget SHALL return `budget_exhausted` with `retryable: true` and `retry_after_s`. Budget values SHALL be configuration.

ID: REQ-public-mcp-authorization-012
Source: RFC 0039 D15 (budgets; residual risk of steered clients)
Scope: v1-mandatory

#### Scenario: Per-client rate is enforced
- **WHEN** one `(sub, cid)` makes 61 calls within a minute
- **THEN** the 61st SHALL return `budget_exhausted` with a positive `retry_after_s`

#### Scenario: Daily row budget caps bulk export
- **WHEN** a `(sub, cid)` has received 20,000 `finance_ledger` rows today
- **THEN** further `finance_ledger` calls from it SHALL return `budget_exhausted` until the day rolls over, while other tools still serve

#### Scenario: One client cannot spend another's budget
- **WHEN** a ChatGPT client of the owner has exhausted its `finance_ledger` row budget
- **THEN** a claude.ai client of the same owner SHALL still be served by `finance_ledger`

#### Scenario: Proposal budget is enforced
- **WHEN** a `(sub, cid)` calls `propose_fact` a 21st time in one day
- **THEN** the call SHALL return `budget_exhausted` and no inbox row SHALL be written

### Requirement: Per-call audit without content
For every request after the bearer check, the server SHALL write exactly one structured JSON log line to stdout with `request_id`, `jti`, `sub`, the `cid` label, `tool`, an argument SHA-256, `outcome` (including the specific refusal cause), `rows`, `bytes`, `redactions`, `latency_ms` and the opaque ids of returned items, and SHALL increment matching Prometheus counters. Every bearer refusal (HTTP 401) SHALL increment a Prometheus counter that records no request content. The log line SHALL never contain bodies, argument values, assertions, bearers, cursors or returned text.

ID: REQ-public-mcp-authorization-013
Source: RFC 0039 D15 (audit); craft-and-care/observability-and-operations.md Logging and Telemetry Discipline
Scope: v1-mandatory

#### Scenario: A served call is audited
- **WHEN** `entity_profile` returns 12 facts
- **THEN** one audit line SHALL record the tool, `outcome: "ok"`, `rows: 12`, the item ids and the argument digest

#### Scenario: Secrets never reach the log
- **WHEN** a call carries a bearer, an assertion, a cursor and a query argument
- **THEN** none of those values SHALL appear in any log line written for that request

#### Scenario: Refusals are audited with their cause
- **WHEN** a call is refused for an expired assertion
- **THEN** one audit line SHALL record `outcome` with cause `expired` and no tool result

#### Scenario: Bearer refusals are counted
- **WHEN** a request is refused with HTTP 401 for a wrong bearer
- **THEN** the bearer-refusal counter SHALL increase by one and no log line SHALL contain the presented bearer

### Requirement: Alerting independent of Butlers
Prometheus alerting rules SHALL be the single source of every alert condition for the public surface. Two recording rules SHALL collapse them into flags: `butlers_public_mcp:critical_ok` and `butlers_public_mcp:warning_ok`, each 1 when no rule of its severity is firing and 0 when any is. The owner's existing Uptime Kuma SHALL deliver them: one `json-query` monitor per flag polls Prometheus' query API for `<flag> == bool 1` with `jsonPath = "data.result[0].value[1]"` and `expectedValue = "1"`, so a 0, an empty result or an unreachable Prometheus turns the monitor DOWN, and Kuma's existing Telegram notification delivers the alert. Alerts SHALL never be sent through the Switchboard, notify or any Butlers daemon, and no ingress into `butlers-public-<env>` SHALL be added for alerting. Kuma's poll interval bounds detection latency, so the critical monitor SHOULD poll every 60 seconds. Critical alerts SHALL fire on a canary hit and while the canary latch holds, on replication-slot retained WAL above 50 % of `max_slot_wal_keep_size`, on any logical replication slot on the primary other than the subscription's slot and its current table-sync slots, or any logical slot whose plugin is not `pgoutput` (REQ-database-security-016 defines the allowance; physical slots never trigger it), and when `butlers_public_mcp_owner_step_pending{change="narrowing"}` has been 1 for more than 24 hours; on dev, where every deploy runs the owner step after its migrations (REQ-public-mcp-projection-003), that critical alert SHALL be kept and signals that the automatic step is failing. Warning alerts SHALL fire while `butlers_public_mcp_owner_step_pending` is 1 with either label (REQ-public-mcp-projection-015), on any DLP drop, more than 20 redactions in an hour, more than 10 assertion failures in 5 minutes, more than 20 bearer refusals in 5 minutes, more than 2,000 finance or health rows served in an hour, one `(sub, cid)` reading more than 25 % of a domain's published rows in a day, replication lag above 10 minutes, a slot inactive for more than 10 minutes, more than 500 undrained inbox rows, and any proposal reaching `apply_failed`. Thresholds SHALL be configuration.

ID: REQ-public-mcp-authorization-014
Source: RFC 0039 D16 (alerting; owner-step pending), D15 (steered clients), D9 (slot safety) and D17 (owner step pending); owner decisions OD11 (Uptime Kuma polls a flag; no new Alertmanager receiver), OD14 (fail closed while the owner step is pending) and OD20 (the 24-hour narrowing critical stays on dev as the failure signal of the automatic step); craft-and-care/observability-and-operations.md When Observability Work Is Mandatory
Scope: v1-mandatory

#### Scenario: A narrowing left pending becomes critical
- **WHEN** `butlers_public_mcp_owner_step_pending{change="narrowing"}` has been 1 for 24 hours and one minute
- **THEN** `butlers_public_mcp:critical_ok` SHALL be 0 and the critical Kuma monitor SHALL deliver its Telegram notification

#### Scenario: A failing automatic owner step on dev turns critical
- **WHEN** each `make deploy-dev` after a narrowing manifest change fails at its owner step for 24 hours and one minute
- **THEN** `butlers_public_mcp:critical_ok` SHALL be 0 and the critical Kuma monitor SHALL deliver its Telegram notification

#### Scenario: A widening left pending stays a warning
- **WHEN** `butlers_public_mcp_owner_step_pending{change="widening"}` has been 1 for 48 hours and no other condition holds
- **THEN** `butlers_public_mcp:warning_ok` SHALL be 0 and `butlers_public_mcp:critical_ok` SHALL stay 1

#### Scenario: Assertion failure burst lowers the warning flag
- **WHEN** 11 calls fail assertion checks within 5 minutes
- **THEN** the warning alert rule SHALL fire, `butlers_public_mcp:warning_ok` SHALL be 0, and the warning Kuma monitor SHALL go DOWN

#### Scenario: Alerts survive a Butlers outage
- **WHEN** every butler daemon is down and a canary hit occurs
- **THEN** `butlers_public_mcp:critical_ok` SHALL be 0 and the critical Kuma monitor SHALL deliver its Telegram notification

#### Scenario: A silent alert path is itself an alarm
- **WHEN** Prometheus is unreachable or returns an empty result for a flag query
- **THEN** that flag's Kuma monitor SHALL go DOWN and notify

#### Scenario: A bulk read raises a warning
- **WHEN** one `(sub, cid)` receives 2,001 finance rows within an hour
- **THEN** `butlers_public_mcp:warning_ok` SHALL be 0, and the underlying alert SHALL carry the domain and the client label, with no row content

## Source References

- Non-Negotiable Rule 1 (owner sovereignty: owner-operated edge is not tenancy), Rule 3 (RFC 0039 named exception) and Rule 7 (transport and auth live in the edge process), `about/heart-and-soul/vision.md`.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D3, D5 to D8 and D14 to D17; owner decisions OD3, OD5, OD10, OD11 and OD17.
- Owner decision OD17 threat-model assumption (RFC 0039 D15): the surface is reachable only by the owner, through the gateway's GitHub-id allowlist and the home-side allowlist; these requirements relax no layer on its account. A security audit of ingress and egress is future work and is not filed yet.
- `about/heart-and-soul/security.md`, partially trusted parties and deployment principles, as amended by the RFC 0039 doctrine sweep.
- `openspec/changes/add-ambient-projection-grants`, REQ-ambient-projections-001 and -002 (digest-only secrets, constant-time comparison, non-disclosing refusal).
