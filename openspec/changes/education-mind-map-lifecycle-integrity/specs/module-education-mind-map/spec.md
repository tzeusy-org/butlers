## ADDED Requirements

### Requirement: Mind map content invariant for active status

A mind map SHALL NOT hold `status = 'active'` with zero nodes. "Active" means
"there is a curriculum here to learn"; a map with no concepts has nothing to
learn and MUST NOT claim otherwise.

The invariant is a property of the row pair (mind map, its node count), so a
write to *either* table can break it. It therefore has **three** enforcement
points, listed together below, and every one of them SHALL exist. Two guard
the status-write direction and one guards the node-delete direction; a
deployment carrying only the first two enforces a transition guard, not this
invariant.

**Enforcement point 1 — the status-write code path.**
`mind_map_update_status(pool, mind_map_id, status)` is the single application
code path permitted to write `mind_maps.status`. When the target status is
`active`, it MUST count the map's rows in `mind_map_nodes` within the same
transaction as the status write and MUST reject the transition with an error
when that count is zero, before any write occurs.

**Enforcement point 2 — a trigger on `education.mind_maps`.** Because a
cross-table predicate cannot be expressed as a column `CHECK` constraint, the
same invariant SHALL additionally be enforced by a
`BEFORE INSERT OR UPDATE` trigger on `education.mind_maps`, so a direct SQL
statement, a migration, or a future code path that bypasses
`mind_map_update_status()` cannot create the state either. Since
`mind_map_nodes.mind_map_id` is a foreign key to `mind_maps.id`, nodes cannot
exist before their map row; therefore an `INSERT` whose row would have
`status = 'active'` is necessarily zero-node and SHALL be rejected
unconditionally.

**Enforcement point 3 — a trigger on `education.mind_map_nodes`.** Deleting
the last node of an `active` map produces exactly the forbidden state without
any write to `mind_maps`, so neither enforcement point above fires. A trigger
on `education.mind_map_nodes` SHALL therefore re-check the parent map after a
deletion and SHALL raise, aborting the transaction, when the deletion leaves
an `active` mind map with zero remaining nodes. It MAY be an `AFTER DELETE`
row-level trigger or a statement-level trigger that fires once per bulk
delete; both reach the same verdict, since a non-deferred `AFTER ... FOR EACH
ROW` trigger is queued to the end of its statement and so observes the
statement's full effect.

This enforcement is a refusal, not a repair: it MUST NOT silently transition
the map to another status. Emptying a curriculum is a larger act than the
caller requested, and making it happen as a side effect would destroy an
owner's map on a write that did not ask for it.

A caller that intends to empty an `active` mind map SHALL first transition it
out of `active` via `mind_map_update_status()`, and then delete its nodes.

Enforcement point 3 MUST NOT fire when the mind map row itself is being
deleted and its nodes go with it through the
`mind_map_nodes.mind_map_id ... ON DELETE CASCADE` foreign key: a mind map that
no longer exists cannot hold an illegal status. Because that exemption depends
on the order in which the database runs cascade and trigger actions, the
implementation MUST cover it with a test rather than assume it.

The module currently exposes no node-deletion function. If one is added, it
SHALL surface the same refusal as an application-level error before reaching
the database, on the same terms as `mind_map_update_status()`.

The invariant is a property of the stored data, not of any one caller. Any
observation of an `active` zero-node mind map is a data integrity fault. Both
directions of the invariant — the status write and the node delete — have a
named enforcement point above; there is no residual path by which supported
code can produce the state.

#### Scenario: Activating a mind map with no nodes is rejected

- **WHEN** `mind_map_update_status(pool, <draft_map_id>, 'active')` is called on a mind map with zero rows in `mind_map_nodes`
- **THEN** the function MUST raise an error stating that a mind map cannot be activated without at least one node
- **AND** the mind map's `status` MUST remain `'draft'`
- **AND** no write to `mind_maps` MUST occur

#### Scenario: Activating a mind map with at least one node succeeds

- **WHEN** `mind_map_update_status(pool, <draft_map_id>, 'active')` is called on a mind map with one or more rows in `mind_map_nodes`
- **THEN** the mind map's `status` MUST become `'active'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Direct SQL insert of an active mind map is rejected by the database

- **WHEN** a statement outside `mind_map_update_status()` attempts `INSERT INTO education.mind_maps (title, status) VALUES ('Anything', 'active')`
- **THEN** the database trigger MUST raise an exception
- **AND** no row MUST be inserted

#### Scenario: Direct SQL update to active on a zero-node map is rejected by the database

- **WHEN** a statement outside `mind_map_update_status()` attempts `UPDATE education.mind_maps SET status = 'active'` for a map with zero nodes
- **THEN** the database trigger MUST raise an exception
- **AND** the row's `status` MUST be unchanged

#### Scenario: Deleting the last node of an active mind map is rejected

- **WHEN** a statement deletes the only remaining node of an `active` mind map
- **THEN** the node-side trigger MUST raise an exception and the transaction MUST abort
- **AND** the node MUST still exist
- **AND** the mind map MUST still be `active` with one node
- **AND** the mind map's `status` MUST NOT have been changed to any other value

#### Scenario: Deleting a non-final node of an active mind map succeeds

- **WHEN** one node is deleted from an `active` mind map that has three nodes
- **THEN** the deletion MUST succeed
- **AND** the mind map MUST remain `active` with two nodes

#### Scenario: Emptying a mind map is legal once it is no longer active

- **WHEN** an `active` mind map is transitioned to `'abandoned'` via `mind_map_update_status()`
- **AND** all of its nodes are then deleted
- **THEN** both operations MUST succeed
- **AND** the mind map MUST end as `'abandoned'` with zero nodes

#### Scenario: Cascade deletion of a mind map is not blocked by node-side enforcement

- **WHEN** an `active` mind map with nodes is deleted, cascading to its rows in `mind_map_nodes`
- **THEN** the delete MUST succeed
- **AND** the node-side enforcement MUST NOT raise, because the mind map no longer exists to hold an illegal status

---

### Requirement: Legacy transition for existing active zero-node mind maps

Mind maps that already hold `status = 'active'` with zero nodes SHALL be
transitioned out of `active` by a one-time data migration. They MUST NOT be
grandfathered, left for a sweep to notice later, or treated as unspecified
legacy data.

Every such map SHALL be set to `status = 'abandoned'`, with the reason
recorded in the map's audit trail as a legacy-integrity transition rather than
an owner-initiated abandonment, so the record does not misattribute the
decision to the owner.

This migration MUST run **before** the enforcement trigger from "Mind map
content invariant for active status" is installed. Installing the trigger
first does not repair rows already at rest, and leaves the database holding
rows that its own constraint forbids.

The known live instance at the time of writing is the mind map titled
"Systems Programming - SPSC & CPU Pinning", `status = 'active'`, zero nodes,
created 2026-06-21 in the `education` schema. It duplicates the title of a
separate `abandoned` map that holds the 30 real nodes for that topic, so
abandonment — not repair — is its correct resolution.

#### Scenario: The live 34-day phantom is transitioned out of active

- **WHEN** the legacy migration runs against a database containing the `active`, zero-node map titled "Systems Programming - SPSC & CPU Pinning" created 2026-06-21
- **THEN** that map's `status` MUST become `'abandoned'`
- **AND** the transition MUST be recorded as a legacy-integrity transition, not as an owner-initiated abandonment

#### Scenario: Every active zero-node map is transitioned, not only the known one

- **WHEN** the legacy migration runs against a database containing three `active` mind maps, two of which have zero nodes
- **THEN** both zero-node maps MUST become `'abandoned'`
- **AND** the map with nodes MUST remain `'active'`

#### Scenario: Migration ordering is enforced

- **WHEN** the migration sequence is applied to a database
- **THEN** the legacy transition step MUST execute before the enforcement trigger is installed
- **AND** after both steps, no row in `education.mind_maps` MUST have `status = 'active'` with zero nodes

#### Scenario: Legacy migration is idempotent

- **WHEN** the legacy migration runs a second time against a database it has already repaired
- **THEN** it MUST find no `active` zero-node maps
- **AND** it MUST NOT modify any row

---

## MODIFIED Requirements

### Requirement: Mind map creation

The system SHALL provide a `mind_map_create(pool, title)` function that
inserts a new row into the `mind_maps` table with a generated UUID primary
key, the given title, `status = 'draft'`, and NULL `root_node_id`. The
function MUST return the UUID of the created mind map.

`mind_map_create()` MUST NOT create a mind map in any status other than
`draft`, and MUST NOT accept a caller-supplied status. A freshly created mind
map has no nodes by construction, so creating it `active` would violate the
mind map content invariant; `draft` is the concrete representation of the
`creation` lifecycle phase that `module-education-curriculum` already
describes.

#### Scenario: Create a mind map returns its UUID

- **WHEN** `mind_map_create(pool, "Python Fundamentals")` is called
- **THEN** a new row MUST exist in `mind_maps` with `title = 'Python Fundamentals'` and `status = 'draft'`
- **AND** the function MUST return the UUID of the newly created row
- **AND** `root_node_id` MUST be NULL

#### Scenario: Create a mind map sets timestamps

- **WHEN** `mind_map_create(pool, "Algebra Basics")` is called
- **THEN** the created row MUST have `created_at` and `updated_at` set to the current timestamp
- **AND** both timestamps MUST be equal at creation time

#### Scenario: Creation never produces an active mind map

- **WHEN** `mind_map_create()` is called any number of times
- **THEN** no created row MUST have `status = 'active'`
- **AND** a caller that attempts to pass a status to `mind_map_create()` MUST receive an error rather than an active map

---

### Requirement: Mind map listing

The system SHALL provide a `mind_map_list(pool, status=None)` function that
returns a list of mind map dicts. When `status` is provided, only mind maps
with that status SHALL be returned; the accepted values are `draft`, `active`,
`completed`, and `abandoned`. When `status` is `None`, all mind maps SHALL be
returned regardless of status.

#### Scenario: List all mind maps with no filter

- **WHEN** `mind_map_list(pool)` is called and there are four mind maps with statuses `draft`, `active`, `completed`, and `abandoned`
- **THEN** the function MUST return all four mind maps

#### Scenario: List mind maps filtered by status

- **WHEN** `mind_map_list(pool, status='active')` is called
- **AND** there are two `active` mind maps and one `completed` mind map
- **THEN** the function MUST return exactly the two `active` mind maps
- **AND** the `completed` mind map MUST NOT appear in the result

#### Scenario: List mind maps filtered to draft

- **WHEN** `mind_map_list(pool, status='draft')` is called
- **AND** there is one `draft` mind map and two `active` mind maps
- **THEN** the function MUST return exactly the one `draft` mind map

#### Scenario: List returns empty list when no mind maps exist

- **WHEN** `mind_map_list(pool)` is called on an empty database
- **THEN** the function MUST return an empty list

---

### Requirement: Mind map status transitions

The system SHALL provide a `mind_map_update_status(pool, mind_map_id, status)`
function that updates the `status` column for the given mind map and refreshes
`updated_at`. Valid target status values are `active`, `completed`, and
`abandoned`. `draft` is not an accepted target: a mind map enters `draft` only
at creation and never returns to it. The function MUST update `updated_at` to
the current timestamp on every call that changes status.

The permitted transitions are:

- `draft` → `active` (only when the map has at least one node)
- `draft` → `abandoned`
- `active` → `completed`
- `active` → `abandoned`
- `abandoned` → `active` (only when the map has at least one node)
- `completed` → `active` (only when the map has at least one node)

Any other transition MUST be rejected with an error before any database write
occurs. In particular `draft` → `completed` MUST be rejected: a map that never
held a curriculum cannot have completed one.

#### Scenario: Transition active mind map to completed

- **WHEN** `mind_map_update_status(pool, <id>, 'completed')` is called on an `active` mind map
- **THEN** the mind map's `status` MUST be `'completed'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Transition active mind map to abandoned

- **WHEN** `mind_map_update_status(pool, <id>, 'abandoned')` is called on an `active` mind map
- **THEN** the mind map's `status` MUST be `'abandoned'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Transition draft mind map to abandoned

- **WHEN** `mind_map_update_status(pool, <id>, 'abandoned')` is called on a `draft` mind map with zero nodes
- **THEN** the mind map's `status` MUST be `'abandoned'`
- **AND** the transition MUST NOT be blocked by the node-count guard, which applies only to activation

#### Scenario: Transition draft mind map to completed is rejected

- **WHEN** `mind_map_update_status(pool, <id>, 'completed')` is called on a `draft` mind map
- **THEN** the function MUST raise an error indicating the transition is not permitted
- **AND** the mind map's `status` MUST remain `'draft'`

#### Scenario: Draft is not an accepted target status

- **WHEN** `mind_map_update_status(pool, <id>, 'draft')` is called on a mind map in any status
- **THEN** the function MUST raise an error indicating `draft` is not an accepted target
- **AND** the mind map's `status` MUST be unchanged

#### Scenario: Status update on non-existent mind map raises error

- **WHEN** `mind_map_update_status(pool, <random_uuid>, 'completed')` is called
- **THEN** the function MUST raise an error indicating the mind map does not exist

---

### Requirement: Mind map lifecycle — staleness abandonment

The system SHALL transition a mind map out of an unfinished state when it has
gone stale. A scheduled weekly job MUST perform this check and call
`mind_map_update_status` to set `abandoned` where a staleness condition is
met.

The job SHALL enumerate rows in `education.mind_maps` — not teaching-flow
state keys and not node timestamps — as its work list, so that a mind map with
no nodes and no flow state is still reachable by it. Two staleness conditions
apply:

1. **Active maps.** An `active` mind map SHALL be abandoned when more than 30
   days have elapsed since any activity on the map without all nodes being
   mastered. "Activity" is the maximum `updated_at` across all nodes in the
   map.
2. **Stalled drafts.** A `draft` mind map SHALL be abandoned when it still has
   zero nodes and its `created_at` is more than 24 hours in the past. A
   `draft` map that has acquired nodes but never activated SHALL be abandoned
   under the same 30-day node-activity rule as an active map.

Condition 2 exists because the previous design had no sweep that could reach a
zero-node map at all: a node-activity rule computes NULL for a map with no
nodes, and a flow-state rule cannot see a map whose flow state was never
written. Detection latency is bounded by the job's weekly cadence; owner-facing
honesty about a stalled draft does not depend on this job, because
`dashboard-education-ui` computes its copy from `created_at` at render time.

#### Scenario: Mind map abandoned after 30 days of inactivity

- **WHEN** a mind map is `active` and the most recent `updated_at` across all its nodes is more than 30 days ago
- **THEN** the weekly staleness job MUST set the mind map's `status` to `'abandoned'`

#### Scenario: Active mind map with recent activity is not abandoned

- **WHEN** a mind map is `active` and at least one node was updated within the past 30 days
- **THEN** the weekly staleness job MUST NOT change the map's status

#### Scenario: Completed mind map is not subject to staleness check

- **WHEN** a mind map has `status = 'completed'` and its nodes have not been updated in 60 days
- **THEN** the weekly staleness job MUST NOT modify its status

#### Scenario: Stalled zero-node draft is abandoned

- **WHEN** a mind map is `draft` with zero nodes and its `created_at` is more than 24 hours in the past
- **THEN** the weekly staleness job MUST set the mind map's `status` to `'abandoned'`

#### Scenario: Recently created draft is left alone

- **WHEN** a mind map is `draft` with zero nodes and its `created_at` is 2 hours in the past
- **THEN** the weekly staleness job MUST NOT change the map's status

#### Scenario: Draft with no teaching flow state is still reachable by the sweep

- **WHEN** a `draft` mind map older than 24 hours has zero nodes and no `flow:{mind_map_id}` entry in the KV store
- **THEN** the weekly staleness job MUST still evaluate it, because the job's work list is drawn from `education.mind_maps`
- **AND** it MUST set the map's `status` to `'abandoned'`

#### Scenario: Draft with nodes follows the 30-day activity rule

- **WHEN** a mind map is `draft`, has 4 nodes, and the most recent node `updated_at` is more than 30 days ago
- **THEN** the weekly staleness job MUST set the mind map's `status` to `'abandoned'`
