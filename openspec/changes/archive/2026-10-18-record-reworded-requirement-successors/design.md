## Context

Each pair was read side by side (archived body from the archived change, successor from
`openspec/specs/<capability>/spec.md`). Every successor either restates the predecessor or replaces
it with later, authoritative behavior. Predecessor bodies describe retired designs and are not restored.

## Decision

Use one `RENAMED`-only delta. OpenSpec 1.9.0 treats a rename whose source is absent and whose target
is already present as an already-synced no-op, so the archive records provenance without rewriting
any canonical requirement (the #4051 precedent).

Left frozen on purpose: pairs whose wording overlap is only coincidental (for example
"Core vs Domain Butlers" versus "Staffers vs Domain Butlers", the per-connector "Ingest Event Shape"
requirements versus "Wellness Ingest Event Shape"), and "MCP Tool Surface (13 Tools)" versus
"(17 Tools)", which this slice does not adjudicate.

Also left frozen: three requirements that the archived change MODIFIED rather than ADDED
(`Default recipient`, `Missing channel identifier fallback`, `Audit Log Filter by Credential Key`).
For a MODIFIED block the guard compares the block's prose and scenarios with the successor, so a
rename alone would surface new unfrozen findings; they need a body-level review in a later slice.
