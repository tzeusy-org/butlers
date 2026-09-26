## Why

The dashboard has a single Dispatch language but still carries a second container dialect: the
legacy `ui/card` primitive and a narrow overview-only `Section`. That split makes ordinary page
composition look like elevated cards and lets dense status modules choose their own loading and
degraded-state boundaries. It also leaves operational state colors duplicated across connector and
topology surfaces.

## What Changes

- Make the semantic `Section` primitive the default dashboard container. It owns the eyebrow,
  section rhythm, and quiet-state treatment without card chrome.
- Add `Tile` for independently loading or degrading modules in dense status grids. A Tile keeps
  one module's failure from blanking neighboring modules.
- Migrate every production `ui/card` consumer to Section or the narrow Tile role, then delete
  `ui/card.tsx` without a re-export or compatibility alias.
- Route connector, Google Health, and topology state marks through `StateDot` or `stateColorVar`,
  and state text through the same registry's AA-safe `stateTextColorVar` mapping.
- Add narrow ESLint ratchets for retired Card imports and literal CSS-token fallbacks inside
  `var()`. The existing named-color and semantic visual-role guards remain the authorities for
  their existing scopes.

## Scope

This is render-only frontend work. It changes no API, persistence, runtime, or connector behavior.
Existing loading, error, empty, and retry behavior remains owned by each consumer.
