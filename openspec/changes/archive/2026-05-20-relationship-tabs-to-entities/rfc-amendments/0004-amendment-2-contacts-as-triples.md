# RFC 0004 Amendment 2 — Contacts as RDF Triples

**Date:** 2026-05-17
**Status:** Applied — effective 2026-05-19; see PR #1791 (bu-u8xq2)
**Supersedes:** RFC 0004 §3 ("Contacts and Contact Info"); the `public.contacts` and
`public.contact_info` tables defined in §3 are deprecated and dropped per the migration
plan in `openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md`.

## Archive note

This amendment was applied to `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md`
on 2026-05-19 via PR #1791 (bead bu-u8xq2). Cross-reference: Amendment 2 header appears
in the RFC under `**Amended:** 2026-05-19` and the full applied text is recorded in the
`## Amendments Applied` section of that document.

Archival confirmed 2026-06-03 (bu-ixb3p) as part of entity-redesign documentation phase
(tasks.md §12.1).

## Summary

The 3-table identity registry (`public.entities` + `public.contacts` + `public.contact_info`)
collapses to a 2-table model: `public.entities` (unchanged) + `relationship.entity_facts`
(new triple store). `resolve_contact_by_channel()` is re-pointed per
`relationship-facts/spec.md` Requirement: Switchboard `resolve_contact_by_channel()`
re-points to triples.

§3 ("Contacts and Contact Info") of RFC 0004 is marked **Superseded** with a forward
pointer to `openspec/specs/relationship-facts/spec.md` (post-archive path) and the
migration timeline.

`public.entity_info` (RFC 0004 §"public.entity_info", lines 63-82) is **unchanged** —
it holds credentials, which are out of scope for this redesign and remain in `public`.

## What this amendment changed in RFC 0004

The following edits were applied to
`about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` on 2026-05-19 (PR #1791):

1. **§"public.contacts" (formerly §3)** marked `Status: Superseded by RFC 0004 Amendment 2`
   with a forward pointer: "See `openspec/specs/relationship-facts/spec.md` for the
   replacement RDF triple model."
2. **§"resolve_contact_by_channel()"** replaced with the triple-store
   query: queries `relationship.entity_facts` for a fact matching the channel type and value,
   then returns the associated entity with its role information.
3. **§"ResolvedContact dataclass"** lost the `contact_id` field; shape now carries `entity_id` only.
4. **§"build_identity_preamble"** updated to drop `contact_id:` and
   emit `[Source: <name> (entity_id: <uuid>), via <channel>]`.
5. **§"Unknown Sender Handling"** lost the contact-creation step;
   `create_temp_contact()` renamed to `create_temp_entity()` and emits only an
   entity row with `metadata={"unidentified": true}` plus a triple via
   `relationship_assert_fact()`.

The `public.entity_info` credentials table is explicitly **out of scope** for this
amendment and remains as specified in the original RFC 0004.

## Rationale

Contacts as a separate noun is a storage artifact, not a model truth. The data is
naturally a triple: `(entity, has-email, "alice@example.com")` carries the same
information as a `contact_info` row joined through `contacts`, but without the
two-table indirection and with native multi-valuedness, provenance, and verification
status. The triple model also makes the Switchboard's reverse-lookup trivially fast
(`SELECT subject FROM facts WHERE predicate=$1 AND object=$2`) and unblocks
predicate-based UI views (Hop, Columns, Concentration) without a separate query path.

## Migration safety

The migration is governed by the 10-step protocol in
`relationship-facts/spec.md` Requirement: Migration safety — dual-write, parity, cut-over,
tracked as 10 verification beads in the beads graph. Zero data loss is mandatory; the
deprecation timeline is binding.
