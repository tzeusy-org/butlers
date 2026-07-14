# Chronicler memory write-back loop (doctrine-amended)

## Why

The chronicler IEA redesign (epic bu-jc6htw) reframed the chronicler around
Intent / Evidence / Activity and shipped everything except section 8 of its
tasks: the doctrine-amended memory write-back loop. That section was split out
of the archived `chronicler-intent-evidence-activity` change (see that change's
Archive Note) because it was blocked on an architectural decision, bu-w6jca:
enabling the shared memory module for the chronicler runs the memory module's
own `episodes` table into the chronicler schema, where it name-collides with
the chronicler's pre-existing domain `chronicler.episodes` table.

bu-w6jca is now decided (owner, option 1): the chronicler routes its memory
module to a dedicated private schema `chronicler_mem`, while its domain code
keeps using `chronicler.episodes`. This change re-introduces the section 8 spec
delta and ships the implementation on that decision.

## What Changes

- **Memory module gains a per-butler `memory_schema` override.** When
  `[modules.memory]` declares `memory_schema = "<name>"`, the module's Alembic
  migration chain and its runtime storage/search/consolidation pool target that
  private schema instead of the butler's own schema. Left unset by every other
  butler, so their memory keeps living in their own schema.
- **The chronicler enables the memory module routed to `chronicler_mem`.** This
  is a bounded, module-private schema the chronicler owns; it is NOT generic
  cross-schema access. The domain `chronicler.episodes` table is preserved.
- **Day-close memory write-back.** After the day-close summary, a deterministic
  completion-hook write-back synthesizes durable insights (sleep debt, lane
  skew, social cadence) into `chronicler_mem` with `source=chronicler`
  provenance, confidence, and decay; marks low-confidence blocks with
  self-reminders for re-reconciliation; and proposes recurring-companion
  enrichment to the relationship butler over MCP (never a direct cross-schema
  write). The write-back adds no new owner-facing message.
- **Doctrine amendment.** `about/heart-and-soul/v1.md`, the chronicler
  `MANIFESTO.md`, and `AGENTS.md` record the narrow own-schema write-back plus
  the single sanctioned once-daily retrospective day-close summary carve-out.

## Impact

- Affected specs: `chronicler-intent-evidence-activity` (ADDED: Memory
  Write-Back Within Own Schema), `butler-chronicler` (MODIFIED:
  Retrospective-Only Scope).
- Affected code: `src/butlers/modules/memory/__init__.py` (`memory_schema`
  override + dedicated pool), `src/butlers/lifecycle.py` (per-module migration
  schema), `src/butlers/daemon.py` (write-back wiring via the memory pool),
  `src/butlers/chronicler/writeback.py` + `day_close_writer.py` (deterministic
  synthesis + hook), `roster/chronicler/butler.toml` (`memory_schema`).
- No core-chain migration is added; the memory chain auto-creates
  `chronicler_mem` via the Alembic env when first migrated there.
- Regression coverage: schema-matrix coexistence (`chronicler.episodes` and
  `chronicler_mem.episodes` coexist, memory tables never leak into the
  chronicler domain schema) plus a real-Postgres write-path proof that memory
  facts land in `chronicler_mem`.
