# Situational Context Bus

## Purpose
Provides shared situational awareness across butlers via a `public.user_context` table. Butlers read and write context signals (traveling, sleeping, meeting, etc.) with TTL-based expiry, confidence scoring, and per-signal write permissions.

## ADDED Requirements

Note on signatures: every context-bus function that takes a connection `pool` (`get_active_context`, `is_user_in_context`, `set_context`, `clear_context`) is implemented as an `async def` and must be awaited (`src/butlers/context_bus.py`). The function-call notation in the scenarios below omits the `async`/`await` keywords for brevity.

### Requirement: User Context Table Schema
The system SHALL maintain a `public.user_context` table in the shared PostgreSQL schema with the following columns: `id` (UUID PK), `signal_type` (TEXT NOT NULL), `value` (TEXT, nullable), `set_by_butler` (TEXT NOT NULL), `set_at` (TIMESTAMPTZ NOT NULL, default now()), `expires_at` (TIMESTAMPTZ NOT NULL), `confidence` (REAL NOT NULL, default 1.0, range 0.0-1.0), `metadata` (JSONB, nullable), and `superseded_at` (TIMESTAMPTZ, nullable). A UNIQUE constraint on `(signal_type, set_by_butler)` SHALL ensure each butler maintains at most one active signal per type. A partial index on `signal_type` WHERE `superseded_at IS NULL AND expires_at > now()` SHALL optimize active-signal queries.

#### Scenario: Table exists after migration
- **WHEN** the core-chain Alembic migration runs
- **THEN** the `public.user_context` table exists with all specified columns and constraints

#### Scenario: Unique constraint prevents duplicate signals
- **WHEN** a butler inserts a signal with a `(signal_type, set_by_butler)` pair that already exists
- **THEN** the insert uses ON CONFLICT DO UPDATE to overwrite the existing row

#### Scenario: Confidence range enforced
- **WHEN** a signal is inserted with confidence less than 0.0 or greater than 1.0
- **THEN** the database rejects the insert with a CHECK constraint violation

### Requirement: Signal Vocabulary
The system SHALL define a fixed vocabulary of context signal types as a Python enum (`ContextSignal`). The vocabulary SHALL include: `traveling`, `sleeping`, `meeting`, `focused`, `exercising`, `sick`, `socializing`, `commuting`, `at_home`, `away`, and `dnd`. Signal types are validated at the application level before database writes.

#### Scenario: Valid signal type accepted
- **WHEN** `set_context()` is called with `signal_type="traveling"`
- **THEN** the signal is written to the database

#### Scenario: Invalid signal type rejected
- **WHEN** `set_context()` is called with `signal_type="partying"`
- **THEN** a `ValueError` is raised listing valid signal types
- **AND** no database write occurs

### Requirement: Write Permissions
The system SHALL enforce per-signal write permissions at the application level. Each signal type SHALL have a defined set of authorized writer butlers. The `set_context()` function SHALL validate `(butler_name, signal_type)` against the permissions table before writing. Unauthorized writes SHALL raise `PermissionError`.

The permission mapping SHALL be:
- `traveling`: travel, general
- `sleeping`: health, general
- `meeting`: general
- `focused`: general
- `exercising`: health
- `sick`: health, general
- `socializing`: relationship, general
- `commuting`: travel, general
- `at_home`: travel, home, general
- `away`: general
- `dnd`: general, switchboard

#### Scenario: Authorized butler writes signal
- **WHEN** the health butler calls `set_context(butler_name="health", signal_type="exercising", ...)`
- **THEN** the signal is written successfully

#### Scenario: Unauthorized butler rejected
- **WHEN** the finance butler calls `set_context(butler_name="finance", signal_type="exercising", ...)`
- **THEN** a `PermissionError` is raised with a message identifying the butler and signal type
- **AND** no database write occurs

#### Scenario: General butler has broad write access
- **WHEN** the general butler calls `set_context()` with any signal type
- **THEN** the write succeeds because general is authorized for all signal types

### Requirement: TTL Semantics
Every context signal SHALL have an `expires_at` timestamp. There SHALL be no indefinite signals. Each signal type SHALL have a default TTL and a maximum TTL. If `expires_at` is not provided, the default TTL SHALL be applied from `set_at`. If the requested TTL exceeds the maximum, it SHALL be clamped to the maximum. A signal is considered **active** when `superseded_at IS NULL AND expires_at > now()`.

Default and maximum TTLs:
- `traveling`: default 24h, max 30d
- `sleeping`: default 8h, max 12h
- `meeting`: default 1h, max 4h
- `focused`: default 2h, max 8h
- `exercising`: default 1h, max 3h
- `sick`: default 24h, max 14d
- `socializing`: default 3h, max 12h
- `commuting`: default 45min, max 3h
- `at_home`: default 12h, max 24h
- `away`: default 12h, max 30d
- `dnd`: default 2h, max 24h

#### Scenario: Default TTL applied when expires_at omitted
- **WHEN** `set_context(signal_type="meeting")` is called without `expires_at`
- **THEN** `expires_at` is set to `now() + 1 hour` (the default TTL for meeting)

#### Scenario: TTL clamped to maximum
- **WHEN** `set_context(signal_type="meeting", expires_at=now()+timedelta(hours=10))` is called
- **THEN** `expires_at` is clamped to `now() + 4 hours` (the max TTL for meeting)

#### Scenario: Signal expires automatically
- **WHEN** a signal's `expires_at` timestamp is in the past
- **THEN** `is_user_in_context()` and `get_active_context()` exclude it from results
- **AND** the row remains in the table for audit purposes

### Requirement: Read Active Context
The system SHALL provide a `get_active_context(pool)` function that returns all currently active context signals (non-expired, non-superseded), ordered by confidence descending then set_at descending. All butlers SHALL be able to call this function. The return type SHALL be a list of `ContextEntry` dataclass instances containing `signal_type`, `value`, `set_by_butler`, `set_at`, `expires_at`, `confidence`, and `metadata`.

#### Scenario: Active signals returned
- **WHEN** there are two active signals (traveling with confidence 1.0, meeting with confidence 0.8) and one expired signal
- **THEN** `get_active_context()` returns a list of two `ContextEntry` objects
- **AND** the traveling signal appears first (higher confidence)

#### Scenario: No active signals
- **WHEN** all signals are expired or superseded
- **THEN** `get_active_context()` returns an empty list

#### Scenario: Expired signals excluded
- **WHEN** a signal has `expires_at < now()` and `superseded_at IS NULL`
- **THEN** it is not included in the result

#### Scenario: Superseded signals excluded
- **WHEN** a signal has `superseded_at IS NOT NULL` and `expires_at > now()`
- **THEN** it is not included in the result

### Requirement: Check Specific Context
The system SHALL provide an `is_user_in_context(pool, signal_type, min_confidence)` function that returns a boolean indicating whether the user is currently in a specific context. The `min_confidence` parameter SHALL default to 0.5. The check SHALL consider a signal active only if it is non-expired, non-superseded, and has confidence >= `min_confidence`.

#### Scenario: User is in context
- **WHEN** there is an active `traveling` signal with confidence 0.9 and `min_confidence=0.5`
- **THEN** `is_user_in_context(pool, "traveling")` returns `True`

#### Scenario: User is not in context
- **WHEN** there is no active `traveling` signal
- **THEN** `is_user_in_context(pool, "traveling")` returns `False`

#### Scenario: Low confidence signal filtered
- **WHEN** there is an active `meeting` signal with confidence 0.3 and `min_confidence=0.5`
- **THEN** `is_user_in_context(pool, "meeting")` returns `False`

#### Scenario: Custom min_confidence threshold
- **WHEN** there is an active `meeting` signal with confidence 0.3 and `min_confidence=0.2`
- **THEN** `is_user_in_context(pool, "meeting", min_confidence=0.2)` returns `True`

### Requirement: Set Context Signal
The system SHALL provide a `set_context(pool, butler_name, signal_type, expires_at, value, confidence, metadata)` function that writes or updates a context signal. The function SHALL validate write permissions, validate the signal type against the vocabulary enum, clamp the TTL to the maximum, and perform an upsert via `INSERT ... ON CONFLICT DO UPDATE`. The `set_at` field SHALL always be set to the current time on upsert. If the signal was previously superseded, the upsert SHALL clear `superseded_at` (set to NULL).

#### Scenario: New signal created
- **WHEN** no existing signal exists for the `(signal_type, set_by_butler)` pair
- **THEN** a new row is inserted with the provided values

#### Scenario: Existing signal updated
- **WHEN** a signal already exists for the `(signal_type, set_by_butler)` pair
- **THEN** the existing row is updated with new `value`, `set_at`, `expires_at`, `confidence`, `metadata`, and `superseded_at = NULL`

#### Scenario: Previously cleared signal re-activated
- **WHEN** a signal was cleared (superseded_at is set) and `set_context()` is called again
- **THEN** the row is updated and `superseded_at` is set to NULL

### Requirement: Clear Context Signal
The system SHALL provide a `clear_context(pool, butler_name, signal_type)` function that explicitly clears a signal before its TTL expires by setting `superseded_at = now()`. A butler SHALL only be able to clear signals it set (matched by `set_by_butler`). The function SHALL be idempotent -- clearing an already-cleared or expired signal is a no-op.

#### Scenario: Active signal cleared
- **WHEN** the health butler calls `clear_context(pool, "health", "exercising")`
- **THEN** the signal's `superseded_at` is set to the current time
- **AND** the signal no longer appears in `get_active_context()` results

#### Scenario: Clearing another butler's signal is a no-op
- **WHEN** the general butler calls `clear_context(pool, "general", "exercising")` but the signal was set by health
- **THEN** no rows are updated (the WHERE clause matches `set_by_butler`)

#### Scenario: Clearing a non-existent signal is a no-op
- **WHEN** `clear_context()` is called for a signal that does not exist
- **THEN** no error is raised and no rows are affected

### Requirement: Context Preamble for LLM Sessions
The system SHALL provide a `format_context_preamble(signals)` function that formats a list of `ContextEntry` objects into a bracketed text string suitable for prepending to LLM session prompts. The format SHALL be: `[User Context: <signal_type> (<value>, <confidence_label>), ...]`. Confidence labels SHALL be: "explicit" for 1.0, "high confidence" for >= 0.8, "medium confidence" for >= 0.5, "low confidence" for < 0.5. If no active signals exist, the function SHALL return an empty string.

#### Scenario: Single active signal formatted
- **WHEN** there is one active signal: traveling, value="Paris", confidence=1.0
- **THEN** `format_context_preamble()` returns `[User Context: traveling (Paris, explicit)]`

#### Scenario: Multiple active signals formatted
- **WHEN** there are two active signals: traveling (Paris, 1.0) and meeting (standup, 0.8)
- **THEN** the preamble includes both signals separated by commas

#### Scenario: Signal without value
- **WHEN** there is one active signal: dnd, value=None, confidence=1.0
- **THEN** `format_context_preamble()` returns `[User Context: dnd (explicit)]`

#### Scenario: No active signals
- **WHEN** there are no active signals
- **THEN** `format_context_preamble()` returns an empty string
