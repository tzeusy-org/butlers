> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Import migration to the canonical useTimezone / AppTimezoneProvider; the alias was retired in the context module.
> **Successor:** `frontend/src/components/ui/timezone-context.tsx`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Frontend Timezone Alias Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the app-level timezone-context migration by moving every same-repository consumer to `AppTimezoneProvider` and `useTimezone`, then deleting the retired Chronicles aliases.

**Architecture:** The existing `@/components/ui/timezone-context` module remains the single timezone-context implementation and `App.tsx` remains its sole application-level mount. Production Chronicles components read that context directly through `useTimezone`; isolated render tests inject it directly through `AppTimezoneProvider`. This is a behavior-preserving import migration with no new compatibility layer.

**Tech Stack:** React 18, TypeScript 5.9, Vitest 3, Vite 7, knip 6.

## Global Constraints

- Do not change timezone derivation, default timezone behavior, provider mounting, rendered output, or public API behavior.
- Do not add a replacement alias, re-export, wrapper, fallback, or alias-absence test.
- Keep the change inside `frontend/src` apart from this implementation plan.
- Run `npm run knip` before build and tests during final verification, matching frontend CI order.

---

### Task 1: Replace production hook aliases with the canonical hook

**Files:**

- Modify: `frontend/src/components/chronicles/GanttSwimlaneInner.tsx`
- Modify: `frontend/src/components/chronicles/CorrectionPromptsPanel.tsx`
- Modify: `frontend/src/components/chronicles/EpisodeDrawer.tsx`

**Interfaces:**

- Consumes: `useTimezone(): string` from `@/components/ui/timezone-context`
- Produces: unchanged owner-timezone strings supplied to existing Chronicles formatting functions

- [ ] **Step 1: Confirm the protected behavior passes before changing imports**

Run:

```bash
cd frontend
npm exec -- vitest run --configLoader runner src/components/chronicles/timezone-rendering.test.tsx src/components/ui/time.test.tsx
```

Expected: both files pass, with 105 tests passing in total.

- [ ] **Step 2: Migrate all three production consumers**

In each file, replace the local compatibility import:

```ts
import { useChroniclesTimezone } from "./use-chronicles-timezone"
```

with the canonical import, preserving each file's existing semicolon style:

```ts
import { useTimezone } from "@/components/ui/timezone-context"
```

Replace every `useChroniclesTimezone()` call with `useTimezone()`. Do not move the hook calls or change how their returned timezone strings are used.

- [ ] **Step 3: Re-run the focused behavior test**

Run:

```bash
cd frontend
npm exec -- vitest run --configLoader runner src/components/chronicles/timezone-rendering.test.tsx
```

Expected: 1 file and 16 tests pass; rendered Singapore and Los Angeles labels remain unchanged.

### Task 2: Migrate tests and stale documentation to canonical names

**Files:**

- Modify: `frontend/src/components/chronicles/timezone-rendering.test.tsx`
- Modify: `frontend/src/components/ui/time.test.tsx`
- Modify: `frontend/src/components/ui/time-app-timezone.test.tsx`
- Modify: `frontend/src/hooks/use-time-window.ts`
- Modify: `frontend/src/components/system/BackupTile.test.tsx`
- Modify: `frontend/src/components/system/ButlerHeartbeatTile.test.tsx`
- Modify: `frontend/src/components/system/DriftTile.test.tsx`
- Modify: `frontend/src/components/system/EgressCatalogTile.test.tsx`
- Modify: `frontend/src/components/system/VersionTile.test.tsx`

**Interfaces:**

- Consumes: `AppTimezoneProvider({ timezone, children })` and `useTimezone()` from `@/components/ui/timezone-context`
- Produces: the same isolated timezone test contexts and accurate comments naming the canonical app-level interface

- [ ] **Step 1: Replace provider imports and JSX names**

Replace the compatibility imports with:

```ts
import { AppTimezoneProvider } from "@/components/ui/timezone-context"
```

Replace each opening and closing `ChroniclesTimezoneProvider` JSX tag with `AppTimezoneProvider`. Keep all `timezone` props and child trees unchanged.

- [ ] **Step 2: Remove stale alias terminology from comments**

Update comments to name `AppTimezoneProvider` or `useTimezone()` directly. In `time-app-timezone.test.tsx`, remove the obsolete contrast against `ChroniclesTimezoneProvider` while retaining the assertion that owner timezone comes from the app-level provider. In system tile tests, describe the mocked `<Time>` dependency as avoiding timezone-context/date-formatting setup without naming the removed provider alias.

- [ ] **Step 3: Re-run both protected behavior suites**

Run:

```bash
cd frontend
npm exec -- vitest run --configLoader runner src/components/chronicles/timezone-rendering.test.tsx src/components/ui/time.test.tsx
```

Expected: 2 files and 105 tests pass with the same assertions as baseline.

### Task 3: Delete the retired modules and prove migration completeness

**Files:**

- Delete: `frontend/src/components/chronicles/use-chronicles-timezone.ts`
- Delete: `frontend/src/components/chronicles/timezone-context.tsx`

**Interfaces:**

- Consumes: the canonical imports established in Tasks 1 and 2
- Produces: one timezone-context interface with no same-repository alias path

- [ ] **Step 1: Delete both compatibility-only modules**

Remove both files after all imports have moved. Do not leave forwarding modules or tombstone comments.

- [ ] **Step 2: Prove retired identifiers and paths are absent**

Run:

```bash
rg -n 'useChroniclesTimezone|ChroniclesTimezoneProvider|components/chronicles/(use-chronicles-timezone|timezone-context)|\./(use-chronicles-timezone|timezone-context)' frontend/src --glob '*.{ts,tsx}'
```

Expected: exit status 1 with no matches.

- [ ] **Step 3: Review the scoped diff**

Run:

```bash
git diff --check
git diff --stat
git diff -- frontend/src
```

Expected: only direct import/name/comment migrations and deletion of the two alias modules; no provider-mounting or behavior changes.

### Task 4: Run final frontend verification and commit

**Files:**

- Verify: all files changed in Tasks 1-3
- Commit: `docs/archive/superpowers/plans/2026-08-28-frontend-timezone-alias-cleanup.md` and the scoped frontend changes

**Interfaces:**

- Consumes: repository frontend scripts from `frontend/package.json`
- Produces: a committed, buildable alias-free migration

- [ ] **Step 1: Run CI-ordered static and build gates**

Run from `frontend/`, in this order:

```bash
npm run knip
npm run build
```

Expected: both commands exit 0.

- [ ] **Step 2: Run focused and full frontend test gates**

Run from `frontend/`:

```bash
npm exec -- vitest run --configLoader runner src/components/chronicles/timezone-rendering.test.tsx src/components/ui/time.test.tsx
npm test
```

Expected: the focused 105 tests and the complete frontend test gate pass. Any pre-existing server-render `useLayoutEffect` warnings must remain warnings, not failures.

- [ ] **Step 3: Self-review and commit**

Run:

```bash
git diff --check
git status --short
git diff --cached --check
```

Stage only the plan and scoped frontend source changes, then commit with:

```bash
git commit -m "refactor(frontend): finish timezone context migration"
```

Expected: the commit succeeds and `git status --short` is empty apart from ignored local `frontend/node_modules`.
