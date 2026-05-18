# RFC 0004 Amendment 2 — Contacts as RDF Triples

**Date:** 2026-05-17
**Status:** Proposed (lands with `relationship-tabs-to-entities`)
**Supersedes:** RFC 0004 §3 ("Contacts and Contact Info"); the `public.contacts` and
`public.contact_info` tables defined in §3 are deprecated and dropped per the migration
plan in `openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md`.

## Summary

The 3-table identity registry (`public.entities` + `public.contacts` + `public.contact_info`)
collapses to a 2-table model: `public.entities` (unchanged) + `relationship.facts`
(new triple store). `resolve_contact_by_channel()` is re-pointed per
`relationship-facts/spec.md` Requirement: Switchboard `resolve_contact_by_channel()`
re-points to triples.

§3 ("Contacts and Contact Info") of RFC 0004 is marked **Superseded** with a forward
pointer to `openspec/specs/relationship-facts/spec.md` (post-archive path) and the
migration timeline.

`public.entity_info` (RFC 0004 §"public.entity_info", lines 63-82) is **unchanged** —
it holds credentials, which are out of scope for this redesign and remain in `public`.

## What this amendment changes in RFC 0004

When this change archives, the following edits MUST be applied to
`about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md`:

1. **§3 ("Contacts and Contact Info")** is marked `Status: Superseded by RFC 0004 Amendment 2`
   with a forward pointer: "See `openspec/specs/relationship-facts/spec.md` for the
   replacement RDF triple model."
2. **§"resolve_contact_by_channel()" (lines 83-95)** is replaced with the triple-store
   query shown in `relationship-facts/spec.md` Requirement: Switchboard
   `resolve_contact_by_channel()` re-points to triples.
3. **§"ResolvedContact dataclass" (lines 97-104)** loses the `contact_id` field; new
   shape carries `entity_id` only.
4. **§"build_identity_preamble" (lines 119-132)** is updated to drop `contact_id:` and
   emit `[Source: <name> (entity_id: <uuid>), via <channel>]`.
5. **§"Unknown Sender Handling" (lines 108-117)** loses the contact-creation step;
   `create_temp_contact()` is renamed to `create_temp_entity()` and emits only an
   entity row with `metadata={"unidentified": true}` plus a triple via
   `relationship_assert_fact()`.

The `public.entity_info` credentials table (§4) is explicitly **out of scope** for this
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
