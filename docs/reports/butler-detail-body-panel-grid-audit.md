# Butler Detail Body Panel-Grid - Doctrine Audit

**Bead:** bu-j3mop
**Branch:** agent/bu-j3mop
**Date:** 2026-05-13

## Scope

5 restyled butler-detail tabs + atoms primitive:
- ButlerOverviewTab.tsx (bu-t0n03, F.1)
- ButlerMemoryTab.tsx (bu-9l25l, F.4)
- ButlerConfigTab.tsx (bu-k55lg, F.3)
- ButlerRoutingLogTab.tsx (bu-pllml, F.5)
- ButlerRegistryTab.tsx (bu-b9jpn, F.6)
- atoms.tsx (bu-hdavr.3 + prior)

## DA.1 - No pid outside test files

**Command:** `rg -n "\bpid\b" frontend/src/components/butler-detail/ButlerOverviewTab.tsx frontend/src/components/butler-detail/ButlerMemoryTab.tsx frontend/src/components/butler-detail/ButlerConfigTab.tsx frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx frontend/src/components/butler-detail/ButlerRegistryTab.tsx frontend/src/components/butler-detail/atoms.tsx`

**Result:** PASS - 3 matches found, all in `//` comment lines

```
frontend/src/components/butler-detail/ButlerConfigTab.tsx:19://   - No pid field anywhere.
frontend/src/components/butler-detail/ButlerMemoryTab.tsx:22://   - No pid field.
frontend/src/components/butler-detail/ButlerOverviewTab.tsx:20://   - No pid field anywhere.
```

All three occurrences are in JSDoc-style comment blocks documenting the deliberate
absence of the `pid` field. No runtime code references `pid`. Secondary
verification (filtering `^[^:]+:[0-9]+:\s*//`) confirms zero non-comment matches.

## DA.2 - No hex/oklch/rgb literals

**Command:** `rg -n "#[0-9a-fA-F]{3,8}|oklch\(|rgb\(|rgba\(" frontend/src/components/butler-detail/ButlerOverviewTab.tsx frontend/src/components/butler-detail/ButlerMemoryTab.tsx frontend/src/components/butler-detail/ButlerConfigTab.tsx frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx frontend/src/components/butler-detail/ButlerRegistryTab.tsx frontend/src/components/butler-detail/atoms.tsx`

**Result:** PASS - 0 matches

No hardcoded color literals found in any of the 6 target files. All color
references use Tailwind/ShadCN semantic tokens (e.g. `text-muted-foreground`,
`bg-muted`, `text-destructive`).

## DA.3 - No em-dashes

**Command:** `rg -n "—" frontend/src/components/butler-detail/ButlerOverviewTab.tsx frontend/src/components/butler-detail/ButlerMemoryTab.tsx frontend/src/components/butler-detail/ButlerConfigTab.tsx frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx frontend/src/components/butler-detail/ButlerRegistryTab.tsx frontend/src/components/butler-detail/atoms.tsx`

**Result:** PASS - 39 matches found, all in comment nodes (not rendered JSX)

Em-dashes appear exclusively in two comment forms:
1. `//` line comments at file/section level (e.g. `// ButlerConfigTab -- bu-k55lg`)
2. `{/* ... */}` JSX inline comment nodes (e.g. `{/* Timestamp -- 80px relative */}`)

Sample comment matches:
- `atoms.tsx:2:// atoms.tsx -- shared primitive atoms for butler detail resident tabs`
- `atoms.tsx:142:      {/* Left-edge accent stripe -- only when accent=true */}`
- `ButlerMemoryTab.tsx:151:      {/* Timestamp -- 80px relative */}`
- `ButlerRegistryTab.tsx:2: * ButlerRegistryTab -- Registry tab for the Switchboard butler detail page.`

No em-dashes appear in JSX string literals or as rendered text content. All
matches are documentation/annotation only. Per the audit spec, comment and JSDoc
matches are acceptable.

## DA.4 - No hardcoded butler names

**Command:** `rg -n "['\"](?:calendar|household|memory-as-a-butler|relationship|chronicler|finance|gmail|health|messenger|switchboard|travel|focus|general|home)['\"]" frontend/src/components/butler-detail/ButlerOverviewTab.tsx frontend/src/components/butler-detail/ButlerMemoryTab.tsx frontend/src/components/butler-detail/ButlerConfigTab.tsx frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx frontend/src/components/butler-detail/ButlerRegistryTab.tsx`

**Result:** PASS - 0 matches

No hardcoded butler name string literals found. All butler identity references
are data-driven via hooks (`useButler(name)`, `useButlers()`,
`useButlerStatusBoard()`) where `name` is passed in as a prop.

## DA.5 - ButlerMemoryTab has zero useMemoryStats

**Command:** `rg -n "useMemoryStats\b" frontend/src/components/butler-detail/ButlerMemoryTab.tsx`

**Result:** PASS - 0 matches

The deprecated global `useMemoryStats` hook has been fully replaced. The file
now uses `useButlerMemoryStats(name)` (per-butler scoped hook) and
`useMemoryRecentWrites(butler, limit)` exclusively.

## Verdict

All 5 audits PASS.
