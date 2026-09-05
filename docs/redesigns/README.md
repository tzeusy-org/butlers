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
   the **newest run is the live tier board**; earlier runs are superseded record;
   findings are tracked as beads, not by editing the dossier.

> Nothing here is deleted or relocated on the basis of age alone: many entries
> are cited as provenance by shipped code (`src/butlers/**`, alembic migrations),
> contract tests, and active specs. Retire a file only after its citers are
> repointed. See `openspec/specs/docs-information-architecture/spec.md` for the
> disposition rules.

## Integration briefs

| Brief | Surface | Now bound by |
|---|---|---|
| [2026-05-17-entity-brief.md](2026-05-17-entity-brief.md) | Entity / relationship pages (v1) | `openspec/specs/relationship-facts/spec.md` (§6b Amendment 1.1), `openspec/specs/dashboard-relationship/spec.md` (binding §0) |
| [2026-06-12-entity-brief-v3.md](2026-06-12-entity-brief-v3.md) | Entity pages (v3; supersedes v1/v2 for later scope) | latest entity brief; prior v2 scope shipped (epics bu-lh4ol, bu-ao6uh, bu-uhjxr, bu-m8gb6) |
| [2026-05-25-secrets-brief.md](2026-05-25-secrets-brief.md) | `/secrets` passport surface | `openspec/specs/butler-secrets/spec.md` (Binding integration brief) |
| [2026-06-20-health-brief.md](2026-06-20-health-brief.md) | Health domain pages | `openspec/specs/dashboard-domain-pages/spec.md` |
| [ingestion-handoff.md](ingestion-handoff.md) | Ingestion / Dispatch console | `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`; `AGENTS.md` (ingestion closure evidence) |
| [design-language.md](design-language.md) | Dispatch design language | **graduated stub** → `openspec/specs/dashboard-design-language/spec.md` (canonical) |

Two React mocks accompany the ingestion handoff and are cited by the active
change `add-connector-oauth-scope-surface`:
[ingestion-connector-detail.jsx](ingestion-connector-detail.jsx),
[ingestion-connectors-data.jsx](ingestion-connectors-data.jsx).

## Pursuit / audit dossiers

Point-in-time UI-maturity audits (newest first is the live board). Query the
`-data.json` sibling, e.g.
`jq '.audits[] | select(.page=="<key>")' docs/redesigns/<date>-jarvis-pursuit-data.json`.

| Run | Dossier |
|---|---|
| 11 (2026-09-03) | [2026-09-03-jarvis-pursuit.md](2026-09-03-jarvis-pursuit.md) — **current board** |
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
  banner at its top pointing at the successor (the `design-language.md` graduated
  stub is the pattern) rather than deleting it while citers remain.
- **Dossiers** are appended by `butler-relentless-jarvis-pursuit`, one dated pair
  per run. That skill does not yet append its run to this index; wiring that into
  its output contract (and back-filling per-file status banners) is tracked in
  **bu-hw39n**. Until then, add the new run's row to the table above by hand.
