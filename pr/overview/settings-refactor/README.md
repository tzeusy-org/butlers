# settings-refactor/

A self-contained design hand-off package for `/settings` (and the new
top-level `/approvals` route) in the Butlers app.

## Quick start

1. Open `index.html` in a browser. The DesignCanvas shows:
   - **Settings proposals** — three top-level direction options
     (Ledger, Console, Manifest). The user has picked Console.
   - **Settings sub-routes** — the three pages under `/settings/`:
     Models, Spend, Permissions & Data.
   - **Adjacent routes** — `/approvals` (replaces existing), and design
     references that fold into `/butlers/{name}` and `/memory`.
2. Read `PLAN.md` — the guiding prompt for a Claude Code session. It
   lists the decisions, the backend API surface, the phased
   implementation plan, acceptance criteria, and open questions.
3. Read `DESIGN_LANGUAGE.md` — the Dispatch language spec that governs
   every visual choice.

## File map

| File | Role |
|---|---|
| `index.html` | Entry point — open in any browser, no build step. |
| `PLAN.md` | Hand-off prompt for Claude Code. **Read first.** |
| `DESIGN_LANGUAGE.md` | The language spec. Non-negotiable. |
| `settings-redesign.jsx` | Ledger / Console / Manifest proposals + Console renderer + Model catalog. |
| `settings-expanded.jsx` | Sub-route renderers (Spend, Permissions/Data) + `/approvals` page + integration refs (Butlers, Memory). |
| `primitives.jsx` | Palette + shared atoms (`ButlerMark`, `StatusDot`, `Sev`, etc.). |
| `design-canvas.jsx` | Pan/zoom presentation shell — not part of the live app. |

## Routes the live app will end up with

```
/                       Overview            (already shipped)
/butlers                Butlers index       (already shipped)
/butlers/{name}         Butler detail       (existing; fold in ButlersExpanded)
/qa                     QA dossier          (already shipped)
/memory                 Memory page         (existing; fold in MemoryExpanded)
/approvals              Approvals inbox     (REPLACE with ApprovalsPage)
/settings               Settings Console    (NEW)
/settings/models        Model catalog       (NEW)
/settings/spend         Spend dashboard     (NEW)
/settings/permissions   Permissions & data  (NEW)
/secrets                Per-user OAuth      (unchanged)
```
