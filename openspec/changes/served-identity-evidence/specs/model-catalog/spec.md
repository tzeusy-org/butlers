## MODIFIED Requirements

### Requirement: Adapter Token Reporting Contract
- All runtime adapters SHALL return `input_tokens` and `output_tokens` in their usage dict from `invoke()`.
- The aggregate invoke tuple and all existing token-bucket rules remain. The no-row sentence in "Adapter cannot determine token counts" applies to the legacy standalone non-attempt path only; an invoked dispatch must retain its linked unmeasurable aggregate row under Logical Session Attempt Orchestration. New model-specific rows are separate model_served_usage evidence with usage_source=provider_breakdown; they never duplicate aggregate ledger quota/cost totals. Each adapter exposes the bounded served evidence record, with missing identity/version/cost unknown and configured identity explicitly separate. The normalized input bucket SHALL remain UNCACHED; independent cache-read/cache-creation buckets MAY exceed it. Inclusive provider prompt/cache checks SHALL precede proven subtraction and SHALL NOT become cached<=UNCACHED validation. Missing cache-overlap semantics SHALL remain unknown; complete totals and cost comparisons SHALL NOT double count.

ID: REQ-model-catalog-005
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

#### Scenario: Adapter reports token usage
- **WHEN** a runtime adapter completes an invocation
- **THEN** it returns a usage dict containing at minimum `{"input_tokens": int, "output_tokens": int}`

#### Scenario: Adapter cannot determine token counts
- **WHEN** a runtime adapter genuinely cannot determine token counts (e.g., CLI process does not expose them)
- **THEN** it returns `{}` or `None` for usage
- **AND** the ledger does not record a row for that invocation

#### Scenario: Known adapters to audit
- **WHEN** the adapter token reporting contract is enforced
- **THEN** the following adapters are verified: `claude`, `codex`, `gemini`, `opencode` (including ollama via opencode), `api` (direct Anthropic Messages API, no subprocess)

### Requirement: Durable Model Resolution Receipt
- Each catalog-backed dispatch attempt SHALL persist the prompt-free model resolution receipt that produced its candidate in `public.model_dispatch_attempts.resolution_receipt`. The receipt SHALL name the policy version, requested and effective intent, winner, ordered candidates, candidate outcomes and exclusions, and tie-break reason. Persisting the receipt MUST NOT change routing eligibility, ordering, or selection.
- The immutable resolution receipt continues to describe the requested/invoked route, not actual serving. The separate served_identity record carries actual-response, CLI-reported, configured and unavailable evidence without overwriting this receipt. requested_not_served requires a complete authoritative response-derived identity set and an exact comparable requested ID; partial, aliases without proven mapping, malformed or request-fallback observations remain comparison unknown. Cold/internal retries retain separate executions and do not become a model failover merely because a reported model differs.

ID: REQ-model-catalog-006
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

#### Scenario: Breaker exclusion is durable

- **WHEN** an otherwise eligible candidate has an open dispatch-outcome breaker
- **THEN** it remains excluded from selection exactly as before
- **AND** the selected attempt's receipt records that candidate with
  `exclusion="breaker_open"`

#### Scenario: Failover attempt explains its predecessor

- **WHEN** attempt zero fails with a classified failure and same-tier attempt one runs
- **THEN** attempt one's receipt names attempt zero and its failure class
- **AND** its winner names the candidate actually invoked for attempt one

#### Scenario: Transparent cold retry is not a failover

- **WHEN** a provider resume handle fails safely and the same catalog candidate is retried cold
- **THEN** the retry receipt retains the predecessor failure class
- **AND** labels the transition as a same-candidate cold retry, not a same-tier failover

#### Scenario: Oversized candidate evidence remains explicit

- **WHEN** a receipt exceeds the bounded storage projection
- **THEN** the ordered candidate list is truncated to a fitting prefix
- **AND** `truncated=true` and the original `candidate_count` are persisted
- **AND** the receipt is not silently dropped
- **AND** the complete persisted JSON projection remains at or below 32 KiB even
  when winner or intent metadata contains oversized catalog-backed strings

#### Scenario: Post-resolution policy override stays coherent

- **WHEN** a spend rule or private-content lane replaces the resolver's winner
- **THEN** the receipt names the final invoked candidate as its sole selected candidate
- **AND** clears stale exclusions on that candidate
- **AND** records the policy override as the winner reason rather than retaining the
  resolver's earlier tie-break reason

#### Scenario: Attempt identity is atomic

- **WHEN** quota skips or runtime retries precede a persisted attempt
- **THEN** the row's `attempt_index` equals its receipt's `attempt_index`
- **AND** no earlier row for that logical dispatch has the same index

#### Scenario: Discretion dispatches retain receipts

- **WHEN** DiscretionDispatcher resolves a catalog model and records quota-skip,
  success, runtime-failure, or suppression provenance
- **THEN** each recorded attempt carries the same bounded receipt contract
- **AND** receipt capture adds no tool-use requirement and preserves the catalog
  eligibility, ordering, and winner used by its legacy `mcp_servers={}` path
- **AND** malformed or forward-version capability envelopes remain eligible exactly
  when the legacy resolver would have selected them

#### Scenario: Read surfaces distinguish historical absence
- **WHEN** either the recorded-receipt or historical-absence read path is evaluated
- **THEN** every corresponding conditional obligation below is preserved:
- **AND** WHEN session detail or a Models dispatch-attempt read returns a recorded receipt
- **AND** THEN the API includes it without re-deriving a current routing decision
- **AND** the session UI discloses why the model won
- **AND** WHEN no receipt was recorded for a historical or static-fallback session
- **AND** THEN the API returns null and the UI says `No receipt recorded.`

## ADDED Requirements

### Requirement: Bounded Serving Evidence Per Attempt
Each invoked dispatch SHALL persist a versioned, bounded, content-free serving record with separate requested/configured, CLI-reported and actual response-derived identities, reported CLI version, scoped cost and closed terminal error evidence. Absent or ambiguous evidence MUST remain unknown; no prompt, response, raw error, denial arguments or provider session identifier is captured. Idempotent attempt replay MUST preserve the first complete receipt and zero-effect refusal on conflict. Every mutable evidence producer SHALL use an invocation-owned worker/collector and SHALL copy its immutable evidence before persistence awaits or worker reuse. Discretion SHALL instantiate a fresh existing create_worker() for each invocation instead of consuming the cached parent's mutable last-call metadata; the spawner SHALL preserve its exclusive pooled worker lifecycle. Competing, cancelled, early-failed and late-final invocations SHALL NOT exchange metadata or refill an unknown record from another call.

ID: REQ-model-catalog-007
Source: bu-s11n0s.6 original outcome; serving protocol P1-P9
Scope: v1-mandatory

#### Scenario: Recorded Claude multi-model metadata preserves scope
- **WHEN** a genuinely recorded pinned init plus final modelUsage contains two model identities
- **THEN** both validated reported IDs and independent usage rows are retained once, their authority/scope is explicit, and init/requested identity is never promoted into actual serving

#### Scenario: Exit-zero terminal failure cannot be successful
- **WHEN** a recorded result has is_error=true, subtype error_max_turns and exit0
- **THEN** the attempt outcome is runtime_failure, the existing route breaker observes it, and max-turns does not automatically authorize a retry

#### Scenario: No model usage remains explicitly unknown
- **WHEN** a recorded result lacks modelUsage or model identity
- **THEN** the original aggregate tokens are retained when measurable, actual serving and absent costs are unknown, and requested model is never copied into served

#### Scenario: Pinned Codex cannot supply omitted identity fields
- **WHEN** the pinned Codex turn.completed has aggregate usage but no model, CLI version or cost
- **THEN** measured tokens survive and each missing identity/version/cost is unknown; free-text banners/messages do not fill fields

#### Scenario: Gemini fallback provenance cannot prove actual serving
- **WHEN** Gemini stats.models can derive from response.modelVersion or req.model without a discriminator
- **THEN** bounded CLI-reported per-model usage is retained with fallback-ambiguous authority and requested_not_served or actual model-quality credit is withheld

#### Scenario: Malformed metadata is bounded and content free
- **WHEN** metadata has URL/path/message/control text, numeric bool/negative/NaN/overflow, excessive models or conflicting finals
- **THEN** the rejected content is not stored/logged/displayed, a closed unknown/malformed/truncated state survives and complete absence/cost claims are prohibited

#### Scenario: Failure and replay preserve one atomic evidence bundle
- **WHEN** same-key replay/concurrency/conflict or any mandatory evidence insert failure occurs
- **THEN** exact replay returns one stable parent and usage bundle, conflict adds no effects, rollback leaves all planted controls unchanged and independent-role readback proves durable state

#### Scenario: Serving truth does not rewrite route authority
- **WHEN** a reported or proven model differs from requested catalog route
- **THEN** the requested receipt/breaker/quota subject remains route reliability, actual serving is attributed only on response evidence, and model mismatch causes no new invocation or capability enablement

#### Scenario: Cumulative costs are not repeated attempt bills
- **WHEN** a resumed result reports a cumulative total with missing or conflicting predecessor scope
- **THEN** the snapshot stays labelled CLI estimate/conversation cumulative and incremental comparison stays unknown rather than charging that total again

#### Scenario: All invoked producers share the same evidence contract
- **WHEN** spawner/discretion/static or an internally retried, timed-out or cancelled adapter is used
- **THEN** every actual execution exposes normalized evidence and no omitted retry/unknown execution disappears; non-invoked provenance has no fabricated usage

