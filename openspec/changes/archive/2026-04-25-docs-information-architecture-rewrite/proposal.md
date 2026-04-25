## Why

The Butlers documentation is organized around implementation surfaces (`docs/core/`, `docs/modules/`, `docs/roles/`, `docs/connectors/`, `docs/switchboard/`) rather than how a contributor or operator actually learns the system. A new engineer cannot follow a coherent path from "what is this?" through "how do I run it?" to "how do I extend it?" — they must instead hop between spec-grade contract documents, README fragments, scattered draft files, and role definitions that assume deep prior context. The existing docs are individually detailed (the scheduler, memory, and switchboard specs are strong), but collectively they form a reference library, not a learning system. Meanwhile, 20+ Excalidraw diagrams sit in a flat `docs/diagrams/` folder disconnected from the content they explain, six draft module docs (`health_wearable_draft.md`, `home_assistant_draft.md`, etc.) sit alongside normative specs with no status distinction, and operational knowledge (setup flows, credential bootstrapping, deployment topology) is scattered between the README, `docs/connectors/`, and `docs/frontend/`. The project has grown to 10 butlers, 8+ modules, 6+ connectors, a React dashboard, E2E benchmarking, and a full OpenSpec planning system — the documentation structure has not kept pace with this expansion.

## What Changes

- **Replace the current docs taxonomy** with a contributor-mental-model-oriented information architecture: `overview/`, `getting_started/`, `concepts/`, `architecture/`, `runtime/`, `butlers/`, `modules/`, `connectors/`, `frontend/`, `data_and_storage/`, `identity_and_secrets/`, `api_and_protocols/`, `operations/`, `testing/`, `roadmap/`, `diagrams/`, `archive/`.
- **Migrate all existing docs** into the new taxonomy — move, split, merge, rewrite, or archive every current file under `docs/`. No file remains in a legacy location.
- **Establish page-level standards** — every docs page follows a consistent template: purpose statement, audience, prerequisites, key concepts, related pages, operational caveats, verification guidance.
- **Establish a mandatory diagram standard** — require `/excalidraw-diagram`-generated SVG diagrams in dark mode, co-located with content, roughly one per page where diagrams materially improve understanding. Move diagrams from the flat `docs/diagrams/` folder to live alongside their content.
- **Write new content** for gaps that exist in the current docs: system overview for newcomers, getting-started guide, concepts glossary, runtime lifecycle explanation, identity/secrets setup guide, storage topology guide, operations runbook, testing strategy overview.
- **Create a docs index** (`docs/index.md`) that serves as both a linear reading guide and a targeted lookup table.
- **Archive stale content** — move draft docs, reconciliation artifacts, and superseded material to `docs/archive/` with clear archival markers.
- **Define a maintenance contract** — establish rules for when code changes must trigger doc updates, how diagram freshness is maintained, and how docs drift is detected.

## Capabilities

### New Capabilities
- `docs-information-architecture`: Defines the target documentation taxonomy, page template standards, diagram standards, navigation model, migration plan, and long-term maintenance contract for the Butlers project documentation.

### Modified Capabilities
_None. This change creates documentation infrastructure specifications. It does not modify any existing code-behavior specs._

## Impact

- **Files:** Every file under `docs/` will be moved, rewritten, split, merged, or archived. New files will be created for gap areas. `docs/index.md` will be created as the entry point.
- **Diagrams:** Existing Excalidraw/SVG files in `docs/diagrams/` will be redistributed to co-locate with their content. New diagrams will be generated for pages that lack visual explanation.
- **README.md:** The Architecture and documentation-heavy sections of README.md should be thinned to point at the new docs, reducing README bloat.
- **No code changes.** No source, test, migration, or infrastructure files are modified.
- **No breaking changes.** Internal cross-references within OpenSpec specs (`docs/roles/*.md` references) will need updating to new paths, but this is a docs-only change.
- **Dependencies:** None. This is a documentation-only initiative.
