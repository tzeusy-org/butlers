## ADDED Requirements

### Requirement: Durable capture precedes routing

General ordinary capture SHALL commit a held, server-attributed receipt before any routing effect. Idempotency SHALL bind principal, source and canonical payload; changed payload reuse SHALL refuse. A routed receipt SHALL reference a verified source-owned target receipt, not caller-supplied schema/table fields.

ID: REQ-general-capture-001
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: Durable capture precedes routing
- **WHEN** a caller or routing process fails after held admission
- **THEN** the held record remains recoverable, a transport retry reuses its identity, and no unverified routed claim or automatic ambiguous resend occurs

### Requirement: Specialist ownership survives destination failure

Capture SHALL preserve specialist ownership independently of tool availability. Missing tooling SHALL yield held/refused with a truthful owning-domain next step, never shadow storage in General. Cross-butler target writes and checks SHALL use Switchboard MCP.

ID: REQ-general-capture-002
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: Specialist ownership survives destination failure
- **WHEN** a recognized specialist destination is unavailable or its write outcome is ambiguous
- **THEN** no General item absorbs the source and no direct peer-schema SQL or fabricated destination tool is used

### Requirement: Ordinary vocabulary preserves private custody compatibility

Ordinary collection/item creation SHALL preserve its current absent-name behavior after private-parent exclusion. Declared ordinary aliases MAY resolve exactly; fuzzy candidates SHALL require selection. Explicit vocabulary declarations SHALL require shape and enforce normalized ordinary uniqueness under concurrency.

ID: REQ-general-capture-003
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: Ordinary vocabulary preserves private custody compatibility
- **WHEN** a name exists only in a custody-private collection
- **THEN** generic creation behaves as for an absent ordinary name and cannot expose, select or conflict on the private collection

### Requirement: Search and catalog remain source-authorized

General keyword/date retrieval SHALL be bounded, deterministically paginated and privacy-filtered before totals. Capture catalog entries SHALL follow the existing sensitivity/source-fetch authority and privacy retirement rules; no captured record becomes public merely because its ledger is in public schema.

ID: REQ-general-capture-004
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: Search and catalog remain source-authorized
- **WHEN** a private parent contains otherwise ordinary siblings or an index is unavailable
- **THEN** private contributions remain absent and any supported fallback remains bounded with explicit degradation

### Requirement: General source versions fence checklist completion

An ordinary General checklist source SHALL have an immutable server-held version and commit-valid owner/read/privacy fence. Source updates, deletion and private enrollment SHALL serialize with receipt finalization. Checklist grants SHALL NOT override custody privacy, and old model-facing receipt projections SHALL recheck current eligibility.

ID: REQ-general-capture-005
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: General source versions fence checklist completion
- **WHEN** private enrollment or source revision change wins before checklist receipt commit
- **THEN** the source is unavailable or stale, no new complete receipt commits, and no protected value leaks through a stored snapshot

### Requirement: Capture entry and held UI are truthful

Supported chat/dashboard intake, routing and the held-capture lane SHALL be wired end-to-end before the General outcome is complete. Held, routed, refused and unavailable SHALL have distinct accessible states and bounded owner actions. Current accepted private custody remains a fixed-link-only conversational surface.

ID: REQ-general-capture-006
Source: butler-general baseline; heart-and-soul/vision.md Rules 1 and 3; adopted personal-item-custody; design.md
Scope: v1-mandatory

#### Scenario: Capture entry and held UI are truthful
- **WHEN** the owner opens the held lane during a source outage or an unfinished routing attempt
- **THEN** the UI reports unavailable or held rather than empty or saved and preserves keyboard access without invoking a hidden provider fallback
