# Autonomy Tracker — Delta

## MODIFIED Requirements

### Requirement: Pattern Fingerprint Computation

The system SHALL compute a deterministic `pattern_fingerprint` for every tool
invocation as SHA-256 over a canonical JSON string (keys sorted alphabetically,
values JSON-serialized) of the fingerprint basis. The fingerprint basis is
version-defined:

- **Version 2 (current):** `(tool_name, {arg: value for each module-declared
  safety-critical argument})`, per the tool's declared arg sensitivities — so
  invocations that agree on safety-critical args (e.g. recipient) but differ on
  non-critical args (e.g. message text) produce the **same** fingerprint. Tools
  that declare no safety-critical arguments fall back to hashing all args.
- **Version 1 (legacy):** `(tool_name, all args)`.

Every recorded fingerprint MUST carry its `fingerprint_version`
(`autonomy_approval_history.fingerprint_version`,
`autonomy_suggestions.fingerprint_version`). Approval counts, frequency
queries, and promotion-threshold detection MUST aggregate only within a single
fingerprint version. Promotion suggestions minted from a version-2 fingerprint
MUST pin exactly the fingerprinted safety-critical args as rule constraints, so
a suggested rule is never broader than what its fingerprint held constant.

#### Scenario: Same safety-critical args with varying text produce identical fingerprints

- **WHEN** `send_telegram` declares `chat_id` safety-critical and
  `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})`
  and `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "running late!"})`
  are computed
- **THEN** both MUST return the same version-2 fingerprint

#### Scenario: Different safety-critical arg values produce different fingerprints

- **WHEN** `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})`
  and `compute_fingerprint("send_telegram", {"chat_id": "dad_456", "text": "hello"})`
  are computed
- **THEN** the two fingerprints MUST be different

#### Scenario: Tool without declared sensitivities falls back to all-args

- **WHEN** a tool declares no safety-critical arguments and two invocations
  differ in any argument value
- **THEN** their fingerprints MUST be different (all-args basis)

#### Scenario: Tool name is part of the fingerprint

- **WHEN** `compute_fingerprint("send_telegram", {"to": "mom"})` and
  `compute_fingerprint("send_email", {"to": "mom"})` are computed with `to`
  safety-critical on both tools
- **THEN** the two fingerprints MUST be different

#### Scenario: Arg key order does not affect fingerprint

- **WHEN** the same invocation's args are supplied in different key orders
- **THEN** the computed fingerprints MUST be identical

#### Scenario: Version segregation of counts

- **WHEN** `autonomy_approval_history` contains version-1 rows for a pattern
  and new version-2 approvals accumulate for the equivalent pattern
- **THEN** frequency queries and promotion-threshold detection count only the
  version-2 rows toward a version-2 suggestion

#### Scenario: Suggested rule pins the fingerprinted args

- **WHEN** a promotion suggestion is created from a version-2 fingerprint
- **THEN** confirming it mints a rule whose constraints pin exactly the
  fingerprinted safety-critical args as exact matches

#### Scenario: Same tool and args produce identical fingerprint

- **WHEN** `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})` is called twice
- **THEN** both calls MUST return the same SHA-256 hex digest

#### Scenario: Different arg values produce different fingerprints

- **WHEN** `compute_fingerprint("send_telegram", {"chat_id": "mom_123", "text": "hello"})` is computed
- **AND** `compute_fingerprint("send_telegram", {"chat_id": "dad_456", "text": "hello"})` is computed
- **THEN** the two fingerprints MUST be different
