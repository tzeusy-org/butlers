# entity-redesign/

A complete Claude Code implementation pack for the redesign of the
`/entities` surface in the Butlers app.

```
README.md                         ← start here (overview + design language summary + API audit)
prompts/
  00-foundation.md                ← READ FIRST. Data model + API + contacts fold-in.
  01-index.md                     ← /entities tabular landing + curation queue
  02-hop.md                       ← /entities/hop re-centre exploration
  03-columns.md                   ← /entities/columns Finder cascade
  04-concentration.md             ← /entities/concentration balance sheet
  05-detail-editorial.md          ← /entities/:id default detail page
  06-detail-workbench.md          ← /entities/:id?mode=workbench power-user toggle
  07-finder.md                    ← app-wide Cmd-K spotlight
reference/
  DESIGN_LANGUAGE.md              ← canonical Dispatch reference
  IMPLEMENTATION_NOTES.md         ← file layout, Tailwind translation, perf notes
  SAMPLE_DATA.md                  ← what's in the prototype dataset and why
  prototype/                      ← the working visual spec, open Entities.html in a browser
    Entities.html
    data.jsx                      ← canonical sample data; the design's contract w/ backend
    atoms.jsx                     ← Dispatch primitives
    catalog.jsx
    app.jsx
    exp-index.jsx                 ← maps 1:1 to prompts/01-index.md
    exp-hop.jsx                   ← maps to 02
    exp-columns.jsx               ← maps to 03
    exp-concentration.jsx         ← maps to 04
    exp-detail.jsx                ← maps to 05 + 06
    exp-command.jsx               ← maps to 07
```

## Quickstart

```
$ open reference/prototype/Entities.html   # see all surfaces live
$ cat README.md                            # overview + tokens + API audit
$ cat prompts/00-foundation.md             # build this first
```

After 00, prompts 01–07 can run in any order, but the phase plan in
`README.md` is the recommended sequence:

1. **Phase 1** — Foundation (00), Index (01), Finder (07)
2. **Phase 2** — Editorial detail (05)
3. **Phase 3** — Hop (02), Columns (03), Workbench (06)
4. **Phase 4** — Concentration (04)

Hop pays back fastest among the alternate views; build it as soon as
phase 1 is landing.
