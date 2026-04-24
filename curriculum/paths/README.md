# Curriculum Paths

This repository needs one primary prerequisite path. The concept surface is broad, but it fits under the 100-hour cap when provider-specific APIs, WhatsApp internals, and detailed frontend implementation are deferred until relevant contribution work.

| Path | Purpose | Total hours | Progress |
|---|---|---:|---|
| `butlers-prerequisites` | The shortest path from system fundamentals to safe first contributions. | 57 | [ ] |

## Path Split Rationale

No separate path is needed yet. The repo has many domains, but most of them share the same core prerequisites: MCP/tool boundaries, async runtime semantics, PostgreSQL isolation, trust boundaries, scheduling, observability, and tests.

Future splits may be useful if:

- The frontend grows into an independently large product surface.
- Provider-specific connector work becomes a major contributor track.
- The WhatsApp Go sidecar becomes central rather than narrow.
- Memory/retrieval grows beyond the current fact/provenance/vector-search foundation.

## Caveats

- [ ] I understand that provider-specific APIs are intentionally not taught exhaustively here.
- [ ] I understand that OpenSpec/doctrine workflow appears as contribution context, not as a technical fundamentals module.
- [ ] I understand that deployment details should be rechecked against `docs/operations/` before operational changes.
