# 06 · Detail · Workbench — /entities/:id?view=workbench · the console

**A toggle, not a replacement.** The Editorial layout (prompt 05) is the
default. The Workbench is reached by `?view=workbench` or the pill in
the page header. Aimed at sessions where the user is *fixing* the graph,
not reading about a person.

## Hypothesis

> When a user has 200 unidentified rows to merge after an import, the
> editorial layout is too quiet. The workbench compresses the same
> content into a console: rails for context and action, middle for the
> entity, raw RDF visible.

## Why this design

- **Three-column layout.** 240 px / 1fr / 280 px. Left rail is context
  ("top relations", "introduced via", "shares emails with"). Middle is
  the entity, plus a KPI strip and a triples view. Right rail is
  actions and a confidence inspector.
- **Raw RDF triples are *visible*.** Mono, syntax-coloured (subject
  dim, predicate amber, object `--fg`, metadata `--mfg`). This is the
  workbench's signature affordance — when the user is fixing the graph,
  they're sometimes literally checking that a triple says what they
  think.
- **KPI strip.** 4 cells, hairline-divided: relations, touch 90d,
  butlers contributing, contacts (with unverified count meta).
- **Right rail leads with a duplicate-warning panel** (only when this
  entity is a `duplicate-candidate`). Otherwise it just shows the
  vertical stack of curation actions.
- **Confidence inspector at the bottom of the right rail.** Per-fact
  confidence rows with mono labels and 4-px bars. Bars amber when
  below 0.85.

See `reference/prototype/exp-detail.jsx` `DetailWorkbench`.

## Featureset

### 6.1 — Page chrome

Same sub-page tab strip + breadcrumb as Editorial. The workbench toggle
pill in the breadcrumb's right side is now `[ ← editorial ]` and reverts
the layout.

### 6.2 — Left rail (240 px)

```
EYEBROW · top relations
[mark] Name                   ×W
       PREDICATE
…
─────────────────────────────────────
EYEBROW · introduced via
serif italic 12 px:
"First-seen on a thread w/ Lin · 2024-06-22"
─────────────────────────────────────
EYEBROW · shares emails w/
[mark] Name
likely the same person →     ← amber mono link
```

The "shares emails with" row triggers a side suggestion of merge
candidates — if there are none, render one serif-italic em-dash.

### 6.3 — Middle column

```
EYEBROW · entity · {id}
[mark, 28px] Display 32px name   [state pill if any]
serif italic gloss

────────────────────────────────────────────────────
relations    touch · 90d    butlers          contacts
N            N              N · contributors  N · K unverified
────────────────────────────────────────────────────

EYEBROW · triples
:p-tan  :colleague-of  :p-yuk  · ×71 · calendar  ·
:p-tan  :employed-by   :o-ndlm · ×52 · memory    ·
:p-tan  :has-email     "tanvir.ahmed@…"  · conf 0.95 · unverified
…
```

Triples view:

- Mono 11 px, line-height 1.6.
- 4-column syntax colour: `dim · amber · fg · mfg`.
- Contact triples use `--blue` for the predicate to distinguish them
  from relational triples.
- Hairline below each row.

### 6.4 — Right rail (280 px)

Top: if `state === 'duplicate-candidate'`, a bordered panel:

```
┌─────────────────────────────────────┐  ← 1px amber border
│ LIKELY DUPLICATE                    │
│ Serif italic: "Same email + employer│
│  as Tanvir Ahmed."                  │
│ [merge into Tanvir Ahmed]           │  ← commit button
│ [ keep both ]                       │  ← pill
└─────────────────────────────────────┘
```

Below: action list. Each action a left-aligned 13-px sans link with a
right-aligned `→` glyph, hairline below.

```
merge…              →
promote tier        →
demote tier         →
edit aliases        →
edit contacts       →
archive             →
forget              →   ← --red
```

Below that: confidence inspector.

```
EYEBROW · confidence
name resolution                       1.00   ████████████████
identity merge                        0.74   ███████████░░░░░  ← amber
contact has-email                     0.95   ██████████████░
tier classification                   0.86   █████████████░░
```

Bar height 4 px. Bar colour: `--fg` if ≥ 0.85, `--amber` if < 0.85.
Numbers tabular tnum mono.

### 6.5 — Keyboard

Same as Editorial. Plus:

- `t` — focus triples view (jumps middle column scroll).
- `?` — toggle between Workbench and Editorial.

## API

Same endpoints as Editorial, plus:

```
GET /api/entities/:id/triples?limit=20      raw triples + contact-facts for the entity
GET /api/entities/:id/confidence            { facetId: string; conf: number }[]
GET /api/entities/:id/duplicate-candidate   pre-computed candidate entity + reason,
                                            or null
```

The triples endpoint is the main new dependency. It returns relations
and contact-facts in one ordered list so the UI can render them
together without merging client-side.

## Visual reference

`reference/prototype/exp-detail.jsx` — `DetailWorkbench`. Anchor entity:
`p-tan2` (Tan Tanvir, a duplicate-candidate of `p-tan`). Use this
entity to test the duplicate-warning panel.

## Acceptance criteria

- [ ] `?view=workbench` toggles to Workbench from any entity detail
      page; toggle pill round-trips.
- [ ] Left rail shows top relations; middle KPI strip aligns to 4
      columns; right rail shows actions + confidence.
- [ ] If the entity is a `duplicate-candidate`, the warning panel is
      the first thing in the right rail; if not, it's absent.
- [ ] Triples view renders with the 4-colour syntax map.
- [ ] Confidence rows colour amber when below 0.85.
- [ ] Keyboard map per §6.5.

## Anti-patterns to avoid

- **Replacing Editorial outright.** The Workbench is an opt-in. The
  default is read-mode.
- **Adding a "raw JSON" tab.** Triples are the raw view. JSON belongs in
  devtools.
- **Auto-running merge.** Even when confidence is high, the user
  commits. The system never silently merges from the detail page.
- **Confidence bars in green.** The colour vocabulary is amber for
  attention, neutral otherwise. Green is reserved for healthy state.

## Stretch

- A "show provenance graph" affordance — visualise the chain of
  butlers that contributed to a given triple. Out of scope.
- Inline triple editing (edit a predicate, edit an object). Risky —
  defer until the contract for edit-vs-forget is clear.
