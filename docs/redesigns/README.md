# Redesigns — briefs and audit dossiers

**Reader:** an agent or maintainer who has landed on a file in `docs/redesigns/`
and needs to tell **live design intent** from **historical audit record**.

This directory is *not* part of the newcomer documentation path (that is
`docs/index.md`). It holds two kinds of working artifact that the redesign and
audit skills write here, and that shipped code, migrations, and specs cite by
path as provenance:

1. **Integration briefs** — the binding design intent for a surface redesign.
   Each is authored by the `butlers-redesign-prompt` skill and is normally
   promoted into an OpenSpec capability spec once its scope is settled; after
   promotion the brief remains as the cited *source of intent* behind that spec.
2. **Pursuit / audit dossiers** — dated, point-in-time UI-maturity audits from
   the `butler-relentless-jarvis-pursuit` skill. Each `<date>-*-pursuit.md` has
   a machine-queryable `-data.json` sibling. These are historical snapshots:
   each board describes only its audit cohort and timestamp; later runs may
   recheck different scopes;
   findings are tracked as beads, not by editing the dossier.

> Nothing here is deleted or relocated on the basis of age alone: many entries
> are cited as provenance by shipped code (`src/butlers/**`, alembic migrations),
> contract tests, and active specs. Retire a file only after its citers are
> repointed. See `openspec/specs/docs-information-architecture/spec.md` for the
> disposition rules.

## Integration briefs

Status is verified against real `bd`/`gh`/spec state, not guessed — see each file's own status
banner for the citation trail. `Active` means a live, unarchived spec still cites the brief as
binding; `Shipped -> ...` means the cited work landed and the citing change is archived;
`Superseded-by -> ...` means a successor doc now carries the binding contract.

| Brief | Surface | Status | Now bound by |
|---|---|---|---|
| [2026-05-17-entity-brief.md](2026-05-17-entity-brief.md) | Entity / relationship pages (v1) | Active (§0, §6b binding) | `openspec/specs/relationship-facts/spec.md` (§6b Amendment 1.1), `openspec/specs/dashboard-relationship/spec.md` (binding §0) |
| [2026-05-25-secrets-brief.md](2026-05-25-secrets-brief.md) | `/secrets` passport surface | Active (binding) | `openspec/specs/butler-secrets/spec.md` (Binding integration brief) |
| [ingestion-handoff.md](ingestion-handoff.md) | Ingestion / Dispatch console | Active (binding) | `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`; `AGENTS.md` (ingestion closure evidence) |

Retired briefs (bodies recoverable from Git history at their original paths):
`2026-06-12-entity-brief-v3.md` (binding constraints carried by the archived change
[2026-06-12-entity-v3-lifecycle-and-depth](../../openspec/changes/archive/2026-06-12-entity-v3-lifecycle-and-depth/design.md)),
`2026-06-20-health-brief.md` (bound by `dashboard-domain-pages`, `butler-health`, and
`proactive-insight-engine` specs), and the `design-language.md` redirect stub (canonical:
[dashboard-design-language](../../openspec/specs/dashboard-design-language/spec.md)).

Two React mocks accompany the ingestion handoff and are cited by the active
change `add-connector-oauth-scope-surface`:
[ingestion-connector-detail.jsx](ingestion-connector-detail.jsx),
[ingestion-connectors-data.jsx](ingestion-connectors-data.jsx).

## Pursuit / audit dossiers

Point-in-time UI-maturity audits, listed by run. A newer date is not live
verification or proof that an earlier finding is resolved. Query the
`-data.json` sibling, e.g.
`jq '.audits[] | select(.page=="<key>")' docs/redesigns/<date>-jarvis-pursuit-data.json`.

| Run | Dossier |
|---|---|
| 14 (2026-09-22) | [2026-09-22-jarvis-pursuit.md](2026-09-22-jarvis-pursuit.md) |
| 13 (2026-09-12) | [2026-09-12-jarvis-pursuit.md](2026-09-12-jarvis-pursuit.md) |
| 12 (2026-09-05) | [2026-09-05-jarvis-pursuit.md](2026-09-05-jarvis-pursuit.md) |
| 11 (2026-09-03) | [2026-09-03-jarvis-pursuit.md](2026-09-03-jarvis-pursuit.md) |
| 10 (2026-09-02) | [2026-09-02-dashboard-chat-pursuit.md](2026-09-02-dashboard-chat-pursuit.md) — dashboard chat lens |
| 09 (2026-09-01) | [2026-09-01-jarvis-pursuit.md](2026-09-01-jarvis-pursuit.md) |
| 08 (2026-08-09) | [2026-08-09-jarvis-pursuit.md](2026-08-09-jarvis-pursuit.md) |
| 07 (2026-07-25) | [2026-07-25-jarvis-pursuit.md](2026-07-25-jarvis-pursuit.md) |
| 06 (2026-07-22) | [2026-07-22-jarvis-pursuit.md](2026-07-22-jarvis-pursuit.md) |
| 05 (2026-07-17) | [2026-07-17-jarvis-pursuit.md](2026-07-17-jarvis-pursuit.md) |
| — (2026-07-12) | [2026-07-12-jarvis-pursuit.md](2026-07-12-jarvis-pursuit.md) |
| — (2026-07-10) | [2026-07-10-jarvis-pursuit.md](2026-07-10-jarvis-pursuit.md) |
| — (2026-07-04) | [2026-07-04-jarvis-pursuit.md](2026-07-04-jarvis-pursuit.md) |
| — (2026-07-28) | [2026-07-28-talk-to-butlers-maturity-pursuit.md](2026-07-28-talk-to-butlers-maturity-pursuit.md) — conversational maturity lens |
| — (2026-07-03) | [2026-07-03-jarvis-audit.md](2026-07-03-jarvis-audit.md) — first full frontend audit |

## Maintenance contract

- **Briefs** are added by `butlers-redesign-prompt` and updated when their
  binding spec's design intent changes; when a brief is fully superseded, add a
  concise successor mapping, move remaining binding clauses into their canonical
  spec, and update live citers before deleting the redundant body. Historical
  provenance belongs in Git or a dated evidence record, not a second live contract.
- **Dossiers** are appended by `butler-relentless-jarvis-pursuit`, one dated pair
  per run. Its output contract (Phase 4, Deliverables) now includes appending the new run's
  row to the table above as part of the same commit that adds the dossier pair — see
  `.claude/skills/butlers-development/subskills/butler-relentless-jarvis-pursuit/SKILL.md`.

Retain briefs while a live spec incorporates their clauses, prototypes while an
acceptance check compares against them, and dated audit pairs while later audits
use their cohort evidence. A finding or proposed move does not become an approved
requirement merely because it appears in a dossier. Implementation and release
status belong in Beads and current verification receipts.
