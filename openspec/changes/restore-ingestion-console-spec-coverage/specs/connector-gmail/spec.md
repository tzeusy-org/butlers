## MODIFIED Requirements

### Requirement: Policy Tier Assignment

The implementation SHALL provide the behavior described by this requirement.
The connector assigns policy tiers for Switchboard queue ordering using a `PolicyTierAssigner` with first-match-wins rules.

The known-contact set consumed by the `known_contact` rule is resolved by `GmailPolicyEvaluator`, which owns the read path onto `public.priority_contacts`. The evaluator SHALL load the set with a single query joining `public.priority_contacts` to `relationship.entity_facts` on `ef.subject = pc.entity_id`, restricted to `predicate = 'has-email'`, `object_kind = 'literal'`, `validity = 'active'`, and non-NULL `object` and `entity_id`. It SHALL NOT join through `public.contacts`, which has been dropped, and SHALL NOT read any flat file. Addresses SHALL be normalized on both load and lookup so membership is case- and whitespace-insensitive.

The evaluator SHALL cache that set in-process with a 15-minute TTL measured on a monotonic clock, refreshed lazily when an expired cache is read. The initial load timestamp SHALL be a sentinel that is unconditionally expired rather than zero, so that a process started on a host whose uptime is shorter than the TTL still performs its first refresh. Refresh SHALL fail open: when the query raises, the evaluator SHALL log a warning and retain its previous cache rather than emptying it, so a database blip cannot silently demote every priority contact. Before the first successful load, the set SHALL be empty and no sender SHALL be treated as a known contact.

#### Scenario: Known contact → high priority
- **WHEN** the sender address is in the known-contact set (loaded from `public.priority_contacts` joined to `relationship.entity_facts`, cached in-process with a 15 minute TTL)
- **THEN** `policy_tier="high_priority"` with rule `"known_contact"`

#### Scenario: Reply to outbound mail → high priority
- **WHEN** the `In-Reply-To` header references a message ID from the user's sent items
- **THEN** `policy_tier="high_priority"` with rule `"reply_to_outbound"`

#### Scenario: Direct correspondence → high priority
- **WHEN** the user's address (`GMAIL_USER_EMAIL`) is in `To` or `Cc`, there is no `List-Unsubscribe` header, and no bulk `Precedence` header
- **THEN** `policy_tier="high_priority"` with rule `"direct_correspondence"` (the `interactive` tier is reserved for live chat channels, not asynchronous email)

#### Scenario: Fallback → default
- **WHEN** no priority rule matches
- **THEN** `policy_tier="default"` with rule `"fallback_default"`

#### Scenario: Policy tier telemetry
- **WHEN** a policy tier is assigned
- **THEN** `butlers_connector_gmail_priority_tier_assigned_total` counter is incremented with labels `endpoint_identity`, `policy_tier`, `assignment_rule`

#### Scenario: Evaluator loads priority contacts from the database
- **WHEN** the evaluator refreshes its known-contact set
- **THEN** it issues one query joining `public.priority_contacts` to `relationship.entity_facts` via `entity_id` on the `has-email` predicate
- **AND** it reads no flat file and does not join `public.contacts`

#### Scenario: Cache TTL is 15 minutes
- **WHEN** the known-contact set is read less than 15 minutes after a successful load
- **THEN** the cached set is returned and no query is issued
- **WHEN** it is read 15 minutes or more after that load
- **THEN** the evaluator refreshes from the database first

#### Scenario: First read always refreshes
- **WHEN** the known-contact set is read for the first time in a freshly started process, on a host whose uptime is shorter than the TTL
- **THEN** the cache is treated as expired and a refresh is attempted

#### Scenario: Fail-open on DB error
- **WHEN** the refresh query raises
- **THEN** a warning is logged and the previously loaded set is retained unchanged
- **AND** the load timestamp is not advanced, so the next read retries the refresh

#### Scenario: Empty set before the first successful load
- **WHEN** the very first refresh fails, or no database pool is configured
- **THEN** the known-contact set is empty and the `known_contact` rule matches nothing
- **AND** tier assignment falls through to the remaining rules rather than erroring
