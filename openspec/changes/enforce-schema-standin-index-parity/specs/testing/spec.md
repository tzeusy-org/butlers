## ADDED Requirements

### Requirement: Schema Stand-In Parity Covers Indexes
A hand-provisioned table stand-in SHALL mirror the indexes its migration chain
creates, and the parity guard SHALL diff them against the real table in both
directions. An index is not decoration: a unique or partial index decides which
rows the real table accepts, so a stand-in missing one produces a table that
accepts writes production rejects while every assertion about it passes.

Foreign keys and triggers SHALL remain excluded, and the reason SHALL remain
documented where an engineer reconciling a stand-in will read it.

#### Scenario: An index the chain has and the stand-in lacks fails the guard
- **WHEN** the migration chain creates an index on a stand-in's table that the
  stand-in does not declare
- **THEN** the parity guard fails and names that index, the chain that creates
  it, and the declaration to reconcile
- **AND** a unique partial index is reported the same way as any other, because
  it is the case where a stale stand-in changes which writes succeed

#### Scenario: An index the stand-in has and the chain lacks fails the guard
- **WHEN** a stand-in declares an index the migration chain does not create
- **THEN** the parity guard fails and names it as extra
- **AND** a declared index whose materialised definition differs from the
  chain's — different columns, order, uniqueness or predicate — is reported as
  mismatched rather than accepted as present

#### Scenario: The index diff is proven able to fail
- **WHEN** a copy of a stand-in is deliberately blinded by removing its declared
  indexes and diffed against the real chain
- **THEN** a test asserts the guard reports the missing index
- **AND** that test fails if the index comparison is removed from the guard, so
  the arm cannot decay into a no-op that reports green

#### Scenario: Foreign keys stay excluded so each table stays creatable alone
- **WHEN** the real chain relates two stand-in tables through a foreign key
- **THEN** the stand-in does not mirror it and the parity guard does not diff it
- **AND** the exclusion's reason is documented: `pending_actions` and
  `approval_rules` reference each other through a DEFERRABLE constraint, so
  mirroring foreign keys would leave neither table independently creatable

#### Scenario: Triggers stay excluded and say why
- **WHEN** the real chain attaches a trigger to a stand-in's table
- **THEN** the stand-in does not mirror it, and a fixture needing that behaviour
  adds it beside the `ddl()` call or takes the real chain
- **AND** the documented reason distinguishes triggers that are foreign keys
  reimplemented in plpgsql — which read a sibling table unqualified and would
  resolve against whatever `search_path` reached first — from self-contained
  ones
