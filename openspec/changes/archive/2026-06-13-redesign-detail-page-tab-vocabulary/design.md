## Context

The current `dashboard-butler-management` spec mandates a full operator tab set
for every butler detail page. The Dispatch redesign proposes a narrower resident
surface. Gate B (`bu-41p8z`) resolved the conflict with option B2: keep the
operator surface intact and make the Dispatch vocabulary the default resident
view.

Current frontend code also exposes a Models tab that is not part of the
spec-mandated 10-tab base list. The spec should not silently bless it as an
eleventh base tab, but implementation cannot ignore it while the route exists.

## Goals

- Make resident mode the default vocabulary for the butler detail page.
- Keep every spec-mandated operator tab reachable without alternate routes.
- Preserve conditional tabs in both modes.
- Make URL deep links deterministic across modes.
- Treat the non-spec Models tab explicitly so implementation has a clean rule.

## Non-Goals

- Do not add new backend APIs or database state for mode persistence.
- Do not specify the internal content of Activity, Logs, Approvals, or Spend
  beyond their presence in the resident tab vocabulary.
- Do not migrate operator-only tabs to alternate top-level routes; that was
  Gate B option B3 and was not chosen.
- Do not make Models a spec-mandated base tab.

## Decisions

1. **Mode storage.** The selected detail-page mode is persisted in browser
   `localStorage` under `butlers.detail.mode`. Missing, invalid, or unsupported
   stored values resolve to `resident`.

2. **Resident tabs.** Resident mode shows the Dispatch vocabulary:
   Overview, Activity, Logs, Approvals, Spend, Config, Memory. These are the
   default visible base tabs for `/butlers/:name`.

3. **Operator tabs.** Operator mode shows the ten spec-mandated base tabs:
   Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM,
   Memory. This preserves the existing operator control plane.

4. **Mode-exclusive deep links.** The `?tab=` value wins over the stored mode
   when it names a tab that only exists in one mode. If it names an
   operator-only tab while the stored mode is resident, the page switches to
   operator mode and renders the requested tab. This applies to Skills,
   Schedules, Trigger, MCP, State, CRM, Sessions, and Models while Models
   exists. If it names a resident-only tab while the stored mode is operator,
   the page switches to resident mode and renders the requested tab. Invalid
   tab values still fall back to Overview without forcing a mode switch.

5. **Conditional tabs.** Conditional tabs are appended after the active mode's
   base tabs and are visible in both resident and operator modes. They do not
   force operator mode because they are butler-specific affordances rather than
   operator-only base controls. This includes switchboard Routing Log and
   Registry, health Health, general Collections and Entities, and education
   Reviews.

6. **Models tab.** Models remains outside the mandated base list. While current
   code exposes it, it is an operator-only extension tab, must be omitted from
   resident mode, and must participate in deep-link auto-promotion. A future
   implementation may remove it or keep it as an explicitly non-spec extension.

## Risks

- Resident tabs named Activity, Logs, Approvals, and Spend may need new or
  remapped tab bodies because current code is still organized around Sessions,
  Skills, Schedules, Trigger, MCP, State, CRM, Memory, and Models.
- Existing bookmarks to mode-exclusive tabs can change stored mode as a side
  effect. This is intentional so valid deep links remain useful.
- Tests must cover both mode-specific tab lists and conditional tabs, otherwise
  future tab consolidation could accidentally remove operator capabilities.
