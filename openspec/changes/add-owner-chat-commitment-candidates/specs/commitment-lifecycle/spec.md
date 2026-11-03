## ADDED Requirements

### Requirement: Owner-Chat Commitment Candidate Production

The Relationship Butler SHALL run a deterministic, model-free reducer that turns qualifying
owner-authored messages from one-to-one WhatsApp and Telegram user-client chats into commitment
candidates in a Relationship-owned candidate store. A message qualifies only when the Switchboard
owner-chat span projection (REQ-butler-switchboard-011) returns it, which requires the
connector's explicit per-message `is_from_me: true` and `forwarded: false` transport facts
(REQ-connector-base-spec-013) on a private chat with at most two participants. The reducer SHALL
run `detect_commitment` on the message text with `now` set to the message's source event time in
the owner's timezone, and SHALL take the counterparty only from the chat's single non-owner
channel identity, resolved through the Relationship channel-identity resolver, never from a
capitalised name in the text. The counterparty SHALL be a known contact: it resolves to a live
entity that is neither the owner nor marked unidentified. A candidate SHALL carry
`direction: "owner_to_other"`, the detector's `kind`, `confidence` and `marker`, an action
description of at most 120 characters, the optional deadline, the commitment fingerprint, the
source channel, the source event time, an immutable source evidence reference (the ingestion
event id and the provider message id) and an untrusted-origin taint stamp. The store SHALL NOT
persist the message text, the matched sentence, or any other message content beyond the action
description. A candidate is not a commitment and SHALL NOT be counted, surfaced or escalated as
one. The reducer SHALL NOT admit a goal, invoke a model, or accept any owner, direction or
counterparty field from model output.

ID: REQ-commitment-lifecycle-011
Source: RFC 0026 §3, §4 and §8; RFC 0038 D2, D4 and D5; bu-4ap4ap release of bu-jv1moa.11 (run 20 move 11, slice S1)
Scope: v1-reserved

#### Scenario: A verified one-to-one owner promise creates one candidate

- **WHEN** the projection returns the owner's message "I'll send you the deck Friday" from a
  private WhatsApp chat whose counterparty JID resolves to Sam's live entity
- **THEN** exactly one candidate exists with `direction: "owner_to_other"`, `kind: "promise"`,
  counterparty Sam's entity id, action "send you the deck", a deadline at the end of the Friday
  following the message's event time in the owner's timezone, and an evidence reference naming
  the ingestion event id and the provider message id
- **AND** no column or JSON field of the candidate store contains the message text

#### Scenario: Group, counterparty-authored and forwarded messages create nothing

- **WHEN** a commitment-shaped message comes from a group or channel chat, is authored by the
  counterparty, is marked `forwarded: true`, or lacks an explicit `is_from_me` or `forwarded` fact
- **THEN** no candidate is created

#### Scenario: An unresolved, unidentified or owner counterparty creates nothing

- **WHEN** the chat counterparty's channel identity resolves to no entity, to an entity marked
  unidentified, or to the owner entity (a note-to-self chat)
- **THEN** no candidate is created

#### Scenario: A hedged, conditional, negated or interrogative message creates nothing

- **WHEN** the owner writes "I should probably send you the deck" or "I'll send it if I find it"
  in a qualifying chat
- **THEN** `detect_commitment` returns no signal and no candidate is created

#### Scenario: An over-long action creates nothing

- **WHEN** the detected action description is longer than 120 characters
- **THEN** no candidate is created; the action is never truncated

#### Scenario: Deadlines anchor to the source event, not processing time

- **WHEN** a message sent on Monday saying "I'll call you tomorrow" is processed on Wednesday
- **THEN** the candidate's deadline is the end of Tuesday in the owner's timezone

### Requirement: Candidate Idempotence and Duplicate Suppression

Each candidate SHALL be keyed by a one-way source key computed from the source channel, endpoint
identity, chat identity, provider message id and direction, and the store SHALL enforce that key
as unique. Re-reading a source message that already has a candidate or tombstone SHALL change
nothing. Before creating a candidate the reducer SHALL compute the commitment fingerprint
(`relationship:commitment` source, counterparty entity id, normalized action) and SHALL create
nothing when an active commitment or a non-terminal candidate already has that fingerprint. The
reducer SHALL advance its source cursor only after the candidates for the returned spans are
committed, and concurrent or restarted runs SHALL converge on the same candidates.

ID: REQ-commitment-lifecycle-012
Source: RFC 0026 §4; REQ-commitment-lifecycle-003; bu-jv1moa.11 behavior matrix (hash of source message and direction)
Scope: v1-reserved

#### Scenario: Replaying a processed message is a no-op

- **WHEN** the reducer re-reads a source message whose candidate exists, is terminal, or has
  been reduced to a tombstone
- **THEN** no candidate is created or modified

#### Scenario: An already-tracked promise is not proposed again

- **WHEN** a qualifying message's fingerprint matches an active commitment, including one the
  routed extraction path of REQ-commitment-lifecycle-007 created
- **THEN** no candidate is created and the active commitment is not modified

#### Scenario: A restated promise does not open a second candidate

- **WHEN** the owner restates the same action to the same counterparty in a second message while
  the first candidate is still non-terminal
- **THEN** only the first candidate exists

#### Scenario: A crash before the cursor write repeats nothing

- **WHEN** the reducer commits candidates for a span page and stops before writing its cursor,
  then runs again, or two runs process the same page concurrently
- **THEN** each qualifying source message has at most one candidate

#### Scenario: A message older than the lapse horizon is never proposed

- **WHEN** the projection returns a message whose source event time is more than 14 days old
- **THEN** no candidate is created

### Requirement: Candidate Card Through the Signed Confirm Transport

A non-terminal candidate SHALL reach the owner only as one signed confirm card created through
the registered deterministic sink path of `notify-confirm-interaction`
(REQ-notify-confirm-interaction-009), with producer `relationship.commitment_candidate`, the
candidate id as producer key and sink reference, the owner entity's primary Telegram binding as
recipient, and exactly three options with values `track`, `done` and `ignore`. The card SHALL
declare no `on_timeout_value`. Before the first card request the reducer SHALL persist, on the
candidate row, a card snapshot: the rendered question, card spans, recipient binding and a
timeout fixed once as the lesser of 86400 seconds and the time then remaining until the
candidate's lapse. Every card request, including a retry after a crash or an ambiguous reply,
SHALL build the prompt only from that snapshot, so a retry presents an identical prompt and
receives the existing confirm. A candidate with less than 30 seconds remaining when the snapshot
would be taken SHALL NOT be carded. Immediately before creating a card the reducer SHALL re-check the fingerprint against
active commitments and SHALL terminate the candidate as `superseded` without a card on a match.
While the counterparty's posture is not `active`, the card SHALL be deferred without losing the
candidate. While the signed-confirm composition is unavailable, the candidate SHALL stay pending.
The card SHALL present the action description only as quoted, inert input. A candidate SHALL
receive at most one card; an expired or undelivered card SHALL NOT be re-sent. Owner quiet and
sleep hours are enforced by the transport's delivery recheck, not bypassed by this producer.

ID: REQ-commitment-lifecycle-013
Source: RFC 0038 D4 (owner decisions arrive only through signed callbacks) and D7; notify-confirm-interaction REQ-009 and REQ-010; add-person-posture REQ-butler-relationship-004; bu-ow5a4
Scope: v1-reserved

#### Scenario: A pending candidate is carded once

- **WHEN** a pending candidate's counterparty is `active`, no active commitment has its
  fingerprint, and the signed-confirm composition is available
- **THEN** exactly one confirm with producer `relationship.commitment_candidate`, options
  `track`, `done` and `ignore`, and no timeout value exists for that candidate
- **AND** repeating the card step returns the same confirm rather than a second one

#### Scenario: A card retry late in the candidate's life reuses the first confirm

- **WHEN** the reducer crashes after the confirm was created but before recording `carded`, and
  retries less than 24 hours before the candidate's lapse
- **THEN** the retry rebuilds the identical prompt from the persisted snapshot and receives the
  existing confirm; no second confirm and no refused or orphaned card results

#### Scenario: Posture defers the card

- **WHEN** the counterparty's posture is `quiet`, `memorial` or `no_contact`
- **THEN** no card is created and the candidate stays pending until it lapses or the posture
  returns to `active`

#### Scenario: A routed commitment created meanwhile supersedes the card

- **WHEN** an active commitment with the candidate's fingerprint appears before the card is
  created
- **THEN** the candidate becomes `superseded` and no card is created

#### Scenario: An unavailable transport keeps the candidate pending

- **WHEN** the signed-confirm composition is not installed or refuses the request
- **THEN** the candidate stays pending and no card or owner message is produced

#### Scenario: An expired card is not re-sent

- **WHEN** a card expires unanswered or its first delivery attempt is lost
- **THEN** no second card is created for that candidate

### Requirement: Track-Only Promotion and Candidate Dispositions

The Relationship sink for `relationship.commitment_candidate` SHALL apply a verified confirm
answer in one Relationship transaction that locks the candidate row, performs the disposition and
records a receipt keyed by `confirm_id`; repeating the same `confirm_id` SHALL return the stored
receipt without a second mutation. `track` SHALL be the only disposition that writes a
commitment: it SHALL call `create_commitment` with the candidate's kind, direction, counterparty,
confidence, action and deadline, and `evidence_opened` carrying `source: "owner_chat_candidate"`,
the channel, the source evidence reference, the source event time, the marker, the candidate id,
the `confirm_id` and the answer time, and SHALL then mark the candidate `tracked` with the
resulting fingerprint. Inside the sink transaction, `track` SHALL first look up an active
commitment with the candidate's fingerprint; when one exists, `track` SHALL NOT call
`create_commitment` and SHALL mark the candidate `tracked` with that fingerprint, leaving the
commitment row, including its `evidence_opened`, unchanged. If a commitment with that fingerprint
commits between the lookup and `create_commitment`, the helper's duplicate semantics apply: the
episode and its `first_detected_at` are kept and `evidence_opened` takes the newest observation,
which is this Track. `done` SHALL mark the candidate
`done` and `ignore` SHALL mark it `ignored`; neither SHALL create, confirm or resolve any
commitment. A candidate that is already terminal SHALL keep its first terminal state; the sink
SHALL record a receipt naming that state and change nothing else. A confirm answer SHALL confer
no approval or other authority beyond this disposition.

ID: REQ-commitment-lifecycle-014
Source: REQ-commitment-lifecycle-001, -002 and -008; RFC 0038 D4 and D13; notify-confirm-interaction REQ-006, REQ-009 and REQ-010; bu-jv1moa.11 (promotion only through the signed owner tap)
Scope: v1-reserved

#### Scenario: Track promotes the candidate once with evidence

- **WHEN** the owner taps Track on a carded candidate
- **THEN** one commitment exists whose `evidence_opened.source` is `owner_chat_candidate` and
  which carries the candidate's evidence reference and `confirm_id`, and the candidate is
  `tracked`
- **AND** a retried application of the same `confirm_id` after a lost reply creates no second
  commitment and returns the same receipt

#### Scenario: Track on an already-tracked promise leaves the commitment alone

- **WHEN** an active commitment with the candidate's fingerprint exists when Track is applied
- **THEN** no owner condition is written, the commitment's `evidence_opened` is unchanged, and
  the candidate is `tracked` with that fingerprint

#### Scenario: Done and Ignore never touch the commitment ledger

- **WHEN** the owner taps Done or Ignore
- **THEN** the candidate becomes `done` or `ignored` and no owner condition is created,
  confirmed or resolved

#### Scenario: The first terminal state wins

- **WHEN** an answer is applied to a candidate that has already lapsed, been superseded or been
  given another disposition
- **THEN** the candidate keeps its first terminal state, no commitment is written, and the
  receipt names that state

#### Scenario: Forged, replayed and wrong-recipient taps change nothing

- **WHEN** a tap carries an invalid signature, comes from a sender or chat other than the bound
  owner recipient, repeats an answered confirm, or arrives after the confirm's deadline
- **THEN** the transport refuses it before the sink and the candidate is unchanged

### Requirement: Candidate Lapse and Content Minimization

A candidate that has no terminal disposition 14 days after its source event time SHALL lapse
silently to `lapsed`, whether or not it was carded, with no owner message. On every terminal
transition (`tracked`, `done`, `ignored`, `superseded`, `lapsed`) the store SHALL clear the
action description, deadline and counterparty, keeping only a content-free tombstone: the source
key, the terminal state, timestamps, the `confirm_id` when carded, and the commitment fingerprint
when tracked. Tombstones SHALL be deleted once their source event time is more than 14 days old.
The Relationship candidate store SHALL retain no candidate content longer than the 14-day
horizon. The card's prompt copy (question and quoted action) in the signed confirm transport's
`public.pending_confirms` row is outside this store and follows that transport's own retention
and backup policy (redaction 14 days after the confirm settles); this requirement does not
claim to purge it.

ID: REQ-commitment-lifecycle-015
Source: bu-jv1moa.11 recorded decisions (unconfirmed candidates lapse silently after 14 days; content retention limited to the action phrase)
Scope: v1-reserved

#### Scenario: An unanswered candidate lapses silently

- **WHEN** 14 days pass after a candidate's source event time without a disposition
- **THEN** the candidate is `lapsed`, its action, deadline and counterparty are cleared, and no
  owner message is sent

#### Scenario: Terminal candidates keep no content

- **WHEN** a candidate is tracked, done, ignored or superseded
- **THEN** only the content-free tombstone fields remain on its row

#### Scenario: Tombstones age out with the horizon

- **WHEN** a tombstone's source event time is more than 14 days old
- **THEN** the tombstone is deleted

### Requirement: Routed Extraction Coexists With Candidates

Owner-chat candidates SHALL be additive to the explicit-owner extraction path of
REQ-commitment-lifecycle-007 in its adopted form. That path SHALL keep auto-creating commitments
from explicit owner statements with `confidence >= 0.8`, and SHALL NOT be routed through, delayed
by or replaced with candidates. Candidates SHALL never auto-create a commitment. When both paths
observe the same promise, at most one active commitment SHALL exist for its fingerprint.

ID: REQ-commitment-lifecycle-016
Source: REQ-commitment-lifecycle-007; RFC 0026 §5 and §8; bu-jv1moa.11 (existing routed explicit-owner extraction remains unchanged)
Scope: v1-reserved

#### Scenario: Routed auto-create still works

- **WHEN** the owner tells Butlers "I'll send Sam that book tomorrow" through the routed owner
  conversation path with Sam resolved
- **THEN** the commitment is created exactly as REQ-commitment-lifecycle-007 requires, with no
  candidate and no card

#### Scenario: A routed commitment supersedes a pending candidate

- **WHEN** a routed owner conversation creates a commitment whose fingerprint matches a pending,
  not yet carded candidate
- **THEN** the candidate becomes `superseded` at its card step, no card is created, and one
  active commitment exists with the routed opening evidence

#### Scenario: Track after a routed commitment on a carded candidate

- **WHEN** a routed owner conversation creates a commitment whose fingerprint matches an
  already carded candidate, and the owner then taps Track
- **THEN** one active commitment exists for that fingerprint, its opening evidence is the routed
  one, and the candidate is `tracked`

### Requirement: No Per-Counterparty Card Cap in S1

The reducer SHALL NOT limit the number of candidate cards per counterparty or per day in slice
S1; every eligible candidate SHALL receive its one card under REQ-commitment-lifecycle-013. The
adopted owner text is: "S1 candidate cards are not capped per counterparty; the cap applies to
counterparty-authored candidates when S2 is specified."

ID: REQ-commitment-lifecycle-017
Source: owner decision 2026-11-02 on bu-jv1moa.43 (option C)
Scope: v1-reserved

#### Scenario: Several promises to one person on one day are all carded

- **WHEN** the owner makes four distinct qualifying promises to the same counterparty within one
  owner-local day, including one just before and one just after owner-local midnight
- **THEN** each of the four candidates receives exactly one card, and none waits, lapses or is
  dropped because of how many cards that counterparty already received
