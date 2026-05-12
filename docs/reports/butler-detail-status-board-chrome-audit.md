# Butler Detail Status-Board Chrome -- Doctrine Audit

**Bead:** bu-ja5bt.9
**Branch:** agent/bu-ja5bt.9
**Date:** 2026-05-13

## Scope

Three primitives in `frontend/src/components/butler-detail/`:
- SiblingButlerNav.tsx
- ButlerDetailHeader.tsx
- ButlerDetailFooter.tsx

## Audit 1 -- Token-only chrome

**Command:**
```
rg -n "#[0-9a-fA-F]{3,8}|oklch\(|rgb\(|rgba\(|style=\{\{" \
  frontend/src/components/butler-detail/SiblingButlerNav.tsx \
  frontend/src/components/butler-detail/ButlerDetailHeader.tsx \
  frontend/src/components/butler-detail/ButlerDetailFooter.tsx
```

**Result:** PASS -- zero matches

No hex literals, oklch/rgb/rgba function calls, or inline `style={{}}` blocks appear in any of the three files.

**Note:** `SiblingButlerNav.tsx` uses `bg-emerald-500` and `bg-amber-500` Tailwind named utilities in its `toneDotClass` helper for the activity tone dot. These are NOT hex literals and NOT inline styles, so they do not trigger the audit pattern. They are established precedents in the codebase (same classes appear in `ButlerApprovalsTab.tsx`, `EligibilityTimeline.tsx`, and several other components) and represent activity-state signals, not butler-hue identity. The design language non-negotiable 1 bans hex literals and ad-hoc `style={{...}}` -- named Tailwind utilities are permitted.

## Audit 2 -- Em-dashes in JSX

**Command:**
```
rg -n "—" \
  frontend/src/components/butler-detail/SiblingButlerNav.tsx \
  frontend/src/components/butler-detail/ButlerDetailHeader.tsx \
  frontend/src/components/butler-detail/ButlerDetailFooter.tsx
```

**Result:** PASS -- all matches are in `//` comment lines only

Grep found 16 lines containing em-dashes across the three files. Every single match is inside a `//` line comment or `/* */` block comment. None appear in JSX string literals, JSX text nodes, prop values, or any user-visible rendered content.

Breakdown by file:

- `SiblingButlerNav.tsx`: 5 matches -- all in top-of-file `//` comments and inline `//` code comments
- `ButlerDetailHeader.tsx`: 8 matches -- all in top-of-file `//` comments and inline `//` code comments
- `ButlerDetailFooter.tsx`: 3 matches -- all in top-of-file `//` comments

Design language non-negotiable 6 bans em-dashes from "JSX strings, `description` props, `CardDescription`, `EmptyState` descriptions, toast messages, and doc prose." Code comments are not user-visible rendered copy; these matches are acceptable.

The `PLACEHOLDER = "--"` constant (ButlerDetailFooter.tsx line 40) uses two regular hyphens, not an em-dash. This is explicitly documented in the file as intentional: "not an em-dash; intentionally two hyphens."

## Audit 3 -- Butler-hue scope

**Method:** Manual inspection -- verify ButlerMark is the only butler-hue surface.

**Result:** PASS

Butler hue (the per-butler accent color from the categorical palette) appears exclusively on `<ButlerMark>` elements. No other element in any of the three files carries butler-identity color.

Findings by file:

**SiblingButlerNav.tsx:**
- `<ButlerMark name={row.name} size={14} tone={isActive ? "fill" : "neutral"} />` (line 185) -- sole butler-hue surface
- The surrounding `<Link>` uses only `text-foreground`, `text-muted-foreground`, `border-foreground`, `border-transparent`, `border-border`, `hover:text-foreground`, `focus-visible:ring-ring` -- all neutral chrome tokens
- The tone dot uses `bg-emerald-500`, `bg-muted-foreground/40`, `bg-destructive`, `bg-amber-500` -- activity-state signals, not butler-identity hue

**ButlerDetailHeader.tsx:**
- `<ButlerMark name={butler} size={24} tone="fill" />` (lines 105 and 131) -- the two butler-hue surfaces (error state and loaded state)
- All other elements (`h1`, `span`, `div`, `Skeleton`) use only neutral tokens: `border-border`, `text-2xl font-bold`, `text-sm text-muted-foreground`

**ButlerDetailFooter.tsx:**
- No `<ButlerMark>` present -- this file renders KPI cells only
- No butler-hue classes of any kind
- All classes are neutral structural Tailwind utilities and semantic tokens from `atoms.tsx`'s `KpiCell`

## Audit 4 -- Real roster

**Command:**
```
rg -n "['\"](?:calendar|household|memory-as-a-butler|relationship|chronicler|finance|gmail|health|messenger|switchboard|travel|focus|general)['\"]" \
  frontend/src/components/butler-detail/SiblingButlerNav.tsx \
  frontend/src/components/butler-detail/ButlerDetailHeader.tsx \
  frontend/src/components/butler-detail/ButlerDetailFooter.tsx
```

**Result:** PASS -- zero hardcoded butler names in functional code

The grep found 3 matches, all inside JSDoc `@example` annotations in `/** ... */` block comments:

- `ButlerDetailFooter.tsx:56`: inside `@example` JSDoc comment: `<ButlerDetailFooter butler="relationship" />`
- `ButlerDetailHeader.tsx:55`: inside `@example` JSDoc comment: `<ButlerDetailHeader butler="relationship" />`
- `SiblingButlerNav.tsx:79`: inside `@example` JSDoc comment: `<SiblingButlerNav activeButlerName="health" />`

JSDoc example annotations are documentation, not functional code. None of these strings appear in component logic, render paths, conditional branches, or data structures. The components resolve butler lists dynamically:

- `SiblingButlerNav.tsx` iterates `useButlerStatusBoard().rows` (line 151: `{rows.map((row) => ...)}`)
- `ButlerDetailHeader.tsx` uses `useButlerStatusBoard().rows` (line 58) to find the active butler by name from the live data
- `ButlerDetailFooter.tsx` scopes all KPI queries to the `butler` prop passed at runtime, with no static butler list

## Quality Gate

Frontend lint (`npm run lint`) reports 0 errors, 6 warnings -- all pre-existing in unrelated files. None of the three audited components appear in the lint output.

## Verdict

All four audits PASS.

| Audit | Status | Notes |
|---|---|---|
| 1. Token-only chrome | PASS | No hex, oklch, rgb, rgba, or inline styles |
| 2. Em-dashes in JSX | PASS | Em-dashes confined to code comments only |
| 3. Butler-hue scope | PASS | Butler hue on ButlerMark only; all other chrome uses neutral tokens |
| 4. Real roster | PASS | Hardcoded names only in JSDoc examples; runtime iterates live data |
