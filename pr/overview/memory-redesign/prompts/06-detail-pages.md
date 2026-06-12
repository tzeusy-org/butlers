# 06 · Detail pages (fact / rule / episode)

> Phase G. End state: the three detail routes share one editorial page
> shape — heading + state, dense KV band, the decay arithmetic line,
> provenance chain, cross-references.

## Shared page shape

All three pages (`/memory/facts/:id`, `/memory/rules/:id`,
`/memory/episodes/:id`) use the same skeleton, in order:

1. **Eyebrow**: kind + short id — `FACT · 7A3F21C9` (mono 10px).
2. **Heading**: the content itself, sans 24px/500. The memory is the
   headline; no "Fact Details" title chrome.
3. **State line**: one mono 11px line stating lifecycle state in the
   API's words — e.g. `active · standard permanence · scope health`.
   Dimmed (`--dim`) throughout when the fact is fading.
4. **KV band**: dense two-column hairline grid (mono keys, sans
   values) — created, last referenced, reference count, scope, tags,
   source butler, sensitivity. Omit empty keys entirely.
5. **Kind-specific section** (below).
6. **Provenance & cross-references** (below).
7. **Commit footer** (facts only, below).

## Kind-specific sections

### Fact

The decay arithmetic, stated honestly in one mono line:

```
confidence 0.94 · decays 0.002/day · last confirmed 12d ago · effective 0.92
```

Entity anchors as underlined links: `subject → /entities/:entity_id`,
`object → /entities/:object_entity_id` when set. A superseded fact links
both directions (`supersedes` / `superseded by`).

### Rule

Full directive text (serif? no — sans 16px; serif is reserved for the
system's voice, and a rule is system data). Then the outcome record:

```
applied 41 · helpful 38 · harmful 1 · effectiveness 0.86
last applied 2026-06-10 · last evaluated 2026-06-11
```

`harmful` fragment red only when > 0.

### Episode

Full content (sans 14px, readable measure 65ch), session id (mono, link
to the session log page if one exists), importance, retention class,
consolidation status glyph + word in mono (`◦ pending`) — the detail
page is the one place the glyph gets its word.

## Provenance & cross-references

Mono eyebrow `PROVENANCE`, then a chain list:

- **Fact page**: `↳ derived from episode <short-id>` (link) when
  `source_episode_id` set; otherwise the section is omitted.
- **Episode page**: facts derived from this episode (reverse lookup —
  if the API lacks it, list nothing rather than fake it; flag
  `GET /facts?source_episode_id=` as a cheap backend delta).
- **Rule page**: `↳ derived from episode <short-id>` when set.

## Commit footer (fact page only)

Per `VISION.md`: the only mutations on the entire surface, gated on the
backend deltas (`POST /facts/:id/confirm`, `POST /facts/:id/retract`).

- `Confirm` — commit pill (fg-on-bg). Serif gloss beside it:
  *"Re-inks the fact: resets decay from today."*
- `Retract` — secondary pill (bordered, not colored). Serif gloss:
  *"Marks the record incorrect; agents stop retrieving it."* Requires a
  one-step confirm (the pill becomes `Retract — confirm?` for 5s; no
  modal).
- At most one commit-class button per surface: `Confirm` is the commit;
  `Retract` stays secondary.
- If the endpoints are not shipped, the footer is absent — not
  disabled.

## Acceptance for this phase

- [ ] All three pages share visibly identical skeletons (eyebrow,
      heading, state line, KV band).
- [ ] The fact arithmetic line matches the format above, mono tabular.
- [ ] A fading fact's page renders its heading and state line dimmed.
- [ ] Provenance links round-trip: fact → episode → (derived facts) →
      back.
- [ ] With confirm/retract endpoints absent, no dead buttons render.
- [ ] No "Details" / "Overview" heading chrome anywhere.
