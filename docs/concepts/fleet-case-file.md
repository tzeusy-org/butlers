# Fleet case file

A Fleet case carries one recognized situation across delivery cycles. The insight broker’s candidate clusters remain cycle-scoped; recording a case is an explicit contributor path, not automatic clustering-to-case wiring.

The [canonical capability spec](../../openspec/specs/fleet-case-file/spec.md) defines required behavior. [RFC 0032](../../about/legends-and-lore/rfcs/0032-fleet-case-file.md) retains the durable-case and effective-role RLS decisions. This guide explains the existing component paths rather than defining another contract.

## Components And Flow

| Component | Existing role |
| --- | --- |
| `public.fleet_cases` | Shared durable situation identity, lifecycle and posture, written by Switchboard. |
| `public.fleet_case_evidence` | Contributor-attributed observations inserted through each butler’s own pool. |
| `public.fleet_case_links` | Explicit references to other ledgers, written through Switchboard. |
| `src/butlers/core/fleet_cases.py` | Persistence, case attention, stale lapse and historical backfill helpers. |
| `src/butlers/core_tools/_fleet_cases.py` | Group-gated tools and sanctioned forwarding to Switchboard. |
| `roster/switchboard/api/router.py` | Read-only case list/detail endpoints. |

The `fleet_cases` core group registers seven tools: `find_open_case`, `open_case`, `contribute_case_evidence`, `propose_case_posture`, `close_case`, `record_case_link`, and `read_case`, under the governing runtime tool-surface policy. Non-Switchboard case/link mutations forward through Switchboard’s `route()` path; reads and evidence INSERT use the caller’s own pool. This does not grant direct writes to another butler’s private schema.

Contribution can bind a reserved evidence reference through the sanctioned Switchboard link path. The S7 tool admits `insight_candidate`, `owner_condition` and `attention_record`; generic link storage remains broader. Contribution and posture proposal can link the actual attention-ledger row recorded for a case-scoped urgent bypass. Recording that row does not establish a provider send, and failed optional binding does not erase committed evidence or imply an automatic retry outbox.

## Reading And Maintenance

`GET /api/switchboard/cases` provides the cursor-paginated, state/posture-filtered list. `GET /api/switchboard/cases/{case_id}` returns the case with bounded evidence and links. These existing public reads are not the separately blocked safe Situations dashboard projection.

The Switchboard-owned native `fleet_case_lapse_sweep` source is scheduled daily at 04:10 UTC. Its helper lapses stale silent/routine cases; historical backfill is a separate operator script using resolved owner-condition episodes. Current backfill also repairs missing episode links on old closed cases. These source paths do not authorize a live maintenance operation.

## Current Limits

Seven source slices are present, but runtime conformance is partial. The immutable-evidence obligation is currently unfenced. The list API’s catch-all clean-empty failure response conflicts with [genuine-source honesty](../api_and_protocols/response-conventions.md); it is a known defect, not a trustworthy empty result. Existing tests do not fully prove registered transport, post-migration bootstrap replay, every role, freshness races, actual sweep dispatch, genuine remote references or old-link SQL recovery.

The [archived source design](../../openspec/changes/archive/2026-10-05-document-rfc0032-fleet-case-file/design.md) records precise requirement coverage and owner allocation. Grant posture remains with `bu-yfd9v1`; paired read availability and the larger Situations feature remain with `bu-h40h2b.8`. Source publication adds no producer, UI write, activation, native Fleet backup/export or offline restore authority.
