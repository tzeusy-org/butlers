import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// ---------------------------------------------------------------------------
// Dispatch one-visual-language guards (bu-86c4c.6)
//
// Three enforcement layers, each a `no-restricted-syntax` selector list.
// IMPORTANT: eslint flat config does NOT merge `rules.no-restricted-syntax`
// arrays across config objects that both match the same file — the LAST
// matching object wins outright for that rule key (verified empirically
// while authoring this). So every combination of "which selectors apply to
// this file" below is expressed as ONE complete, non-overlapping config
// object rather than several partial ones layered on top of each other.
// ---------------------------------------------------------------------------

// Pre-existing bu-86c4c.5 guard: hsl(var(--x)) is invalid CSS for this theme
// (tokens are full oklch(...) literals, not HSL components).
const HSL_VAR_SELECTORS = [
  {
    selector: 'Literal[value=/hsla?\\(\\s*var\\(/i]',
    message:
      'hsl(var(--x)) is invalid CSS for this theme (tokens are oklch(...) literals, ' +
      'not HSL components). Use var(--x) directly, or chartColor()/chartColorAlpha() ' +
      'from src/lib/chart-colors.ts for chart series colors.',
  },
  {
    selector: 'TemplateElement[value.raw=/hsla?\\(\\s*var\\(/i]',
    message:
      'hsl(var(--x)) is invalid CSS for this theme (tokens are oklch(...) literals, ' +
      'not HSL components). Use var(--x) directly, or chartColor()/chartColorAlpha() ' +
      'from src/lib/chart-colors.ts for chart series colors.',
  },
]

// bu-86c4c.6, deliverable (a): ban raw Tailwind status-palette classes.
// The dashboard has exactly three state colors (--red, --amber, --green —
// see openspec/specs/dashboard-design-language/spec.md § State Color
// Discipline) plus their theme-aware CSS custom properties in index.css.
// Raw Tailwind shades (bg-red-500, text-emerald-600, border-amber-400, a
// dark: pair of the same, etc.) are a second, drifting dialect for the same
// three signals — this audit found 80+ files using 2-3 different named
// greens for "healthy" alone. Use `var(--red)` / `var(--amber)` (borders,
// fills — dots are the accepted small-glyph exception to "no background
// fills") / `var(--green)` / `var(--amber-text)` (TEXT only — see
// bu-86c4c.16, base --amber fails WCAG AA as text) instead.
//
// Genuinely non-status uses that only coincidentally land on one of these
// Tailwind shades (a fixed categorical/tag palette, not a live health
// signal — e.g. components/chronicles/lane-taxonomy.ts's 9-color activity
// taxonomy, components/general/ComplexityBadge.tsx's 6-tier palette) are
// exempted with a line-level `eslint-disable-next-line no-restricted-syntax`
// and an explanatory comment, not a rule-wide escape hatch.
const STATUS_COLOR_SELECTORS = [
  {
    selector:
      'Literal[value=/\\b(?:bg|text|border|ring|decoration|from|via|to|fill|stroke|outline|divide|caret|accent|shadow)-(?:red|green|emerald|amber|yellow|orange)-(?:50|100|150|200|300|400|500|600|700|800|900|950)\\b/]',
    message:
      'Raw Tailwind status-palette classes are banned (bu-86c4c.6) — this dashboard has ' +
      'exactly three state colors: var(--red), var(--amber) (var(--amber-text) for TEXT — ' +
      'see bu-86c4c.16), var(--green). If this is genuinely NOT a live status/health signal ' +
      '(a fixed categorical/tag palette that coincidentally lands on this shade), leave the ' +
      'class as-is and add a line-level eslint-disable-next-line with a one-line reason.',
  },
  {
    selector:
      'TemplateElement[value.raw=/\\b(?:bg|text|border|ring|decoration|from|via|to|fill|stroke|outline|divide|caret|accent|shadow)-(?:red|green|emerald|amber|yellow|orange)-(?:50|100|150|200|300|400|500|600|700|800|900|950)\\b/]',
    message:
      'Raw Tailwind status-palette classes are banned (bu-86c4c.6) — this dashboard has ' +
      'exactly three state colors: var(--red), var(--amber) (var(--amber-text) for TEXT — ' +
      'see bu-86c4c.16), var(--green). If this is genuinely NOT a live status/health signal ' +
      '(a fixed categorical/tag palette that coincidentally lands on this shade), leave the ' +
      'class as-is and add a line-level eslint-disable-next-line with a one-line reason.',
  },
]

// bu-86c4c.6, deliverable (c): ban raw hex color literals in JSX files.
// "No invented colors" is already a spec requirement (dashboard-design-
// language spec.md § Surface Palette) for oklch(/#/rgb(/hsl( diffs outside
// index.css; this closes the #hex gap specifically for component files
// (.tsx). Scoped to .tsx (not .ts) because a couple of pre-existing .ts data
// modules (lane-taxonomy.ts, entity-model.ts) hold small fixed hex palettes
// that are out of this bead's diff size — component-file enforcement is
// where the actual visual-language leak happens (inline style=, recharts
// fill/stroke, canvas node styles).
// Requires at least one A-F letter among the hex digits (lookahead) so that
// purely-decimal `#NNN`-style IDs (QA short_ids like "#401", "#218", bead/PR
// references) don't false-positive — verified against this codebase's real
// hex-color literals, which (Tailwind palette values like #22c55e, #ef4444,
// #eab308, #3b82f6) always include a letter; a human- or palette-chosen
// color composed of only 0-9 is vanishingly rare in practice.
const HEX_COLOR_SELECTORS = [
  {
    selector: 'Literal[value=/#(?=[0-9a-fA-F]*[a-fA-F])(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\\b/]',
    message:
      'Raw hex color literals are banned in JSX files (bu-86c4c.6). Reference an existing ' +
      'CSS custom property with var(--x) instead (see frontend/src/index.css for the token ' +
      'catalog). If this is a genuinely arbitrary, user-chosen color (e.g. a free-form label- ' +
      'color input, not a themed value), add a line-level eslint-disable-next-line with a ' +
      'one-line reason.',
  },
  {
    selector: 'TemplateElement[value.raw=/#(?=[0-9a-fA-F]*[a-fA-F])(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\\b/]',
    message:
      'Raw hex color literals are banned in JSX files (bu-86c4c.6). Reference an existing ' +
      'CSS custom property with var(--x) instead (see frontend/src/index.css for the token ' +
      'catalog). If this is a genuinely arbitrary, user-chosen color (e.g. a free-form label- ' +
      'color input, not a themed value), add a line-level eslint-disable-next-line with a ' +
      'one-line reason.',
  },
]

// bu-86c4c.6, deliverable (b): ban local re-declarations of the two
// primitives the JARVIS audit found forked 7 times across ui/, three
// Settings pages, QA, and the secrets passport (`function Eyebrow`, a
// second `function Voice`) instead of importing the canonical
// components/ui/Eyebrow.tsx / Voice.tsx. Scoped away from components/ui/
// itself (the canonical home) and from the one documented composition
// wrapper (secrets/passport/atoms.tsx, which imports and wraps the
// canonical components rather than redeclaring their style — see that
// file's Eyebrow/Voice for the accepted pattern).
const PRIMITIVE_REDECLARATION_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^(?:Eyebrow|Voice)$/]',
    message:
      'Do not locally redeclare Eyebrow/Voice (bu-86c4c.6 collapsed 7 forks of these onto ' +
      'components/ui/). Import { Eyebrow } from "@/components/ui/Eyebrow" or ' +
      '{ Voice } from "@/components/ui/Voice" instead. If you need extra behavior, wrap the ' +
      'canonical component (see components/secrets/passport/atoms.tsx for the accepted ' +
      'composition-wrapper pattern) rather than redeclaring its typographic style.',
  },
  {
    selector: 'VariableDeclarator[id.name=/^(?:Eyebrow|Voice)$/]',
    message:
      'Do not locally redeclare Eyebrow/Voice (bu-86c4c.6 collapsed 7 forks of these onto ' +
      'components/ui/). Import { Eyebrow } from "@/components/ui/Eyebrow" or ' +
      '{ Voice } from "@/components/ui/Voice" instead. If you need extra behavior, wrap the ' +
      'canonical component (see components/secrets/passport/atoms.tsx for the accepted ' +
      'composition-wrapper pattern) rather than redeclaring its typographic style.',
  },
]

// bu-qvnce.10: ban hand-rolled fixed-inset overlays. Before this bead,
// EntityFinder/CalendarAgendaView/ButlerManagementTab's ModalBackdrop each
// hand-rolled a `fixed inset-0` scrim div with an inconsistent (or missing)
// subset of dialog semantics — no role, no focus trap, no focus-restore.
// `useModalChoreography` (src/hooks/use-modal-choreography.tsx) is now the
// one overlay contract; a NEW `fixed inset-0` div is almost always either a
// missed opportunity to reuse the canonical Dialog/Sheet primitives
// (components/ui/dialog.tsx, sheet.tsx — Radix-backed, already correct) or a
// hand-rolled overlay that will repeat the same gaps. Scoped to the "fixed
// ... inset-0" idiom specifically (not just "fixed") — that combination is
// the hallmark of a full-viewport scrim; an anchored floating element (e.g.
// FloatingChatWidget's `fixed bottom-20 right-4`) is a different shape
// entirely and does not match.
const HANDROLLED_OVERLAY_SELECTORS = [
  {
    selector: 'Literal[value=/\\bfixed\\b[^"]*\\binset-0\\b/]',
    message:
      'Hand-rolled fixed-inset overlay (bu-qvnce.10). Wire it through useModalChoreography ' +
      '(src/hooks/use-modal-choreography.tsx) for focus-in/Tab-trap/Escape/focus-restore, or ' +
      'use the canonical Dialog/Sheet primitives (components/ui/dialog.tsx, sheet.tsx) which ' +
      'already do. If this is genuinely not a dialog/overlay (no focus semantics apply), add a ' +
      'line-level eslint-disable-next-line with a one-line reason.',
  },
  {
    selector: 'TemplateElement[value.raw=/\\bfixed\\b[^`]*\\binset-0\\b/]',
    message:
      'Hand-rolled fixed-inset overlay (bu-qvnce.10). Wire it through useModalChoreography ' +
      '(src/hooks/use-modal-choreography.tsx) for focus-in/Tab-trap/Escape/focus-restore, or ' +
      'use the canonical Dialog/Sheet primitives (components/ui/dialog.tsx, sheet.tsx) which ' +
      'already do. If this is genuinely not a dialog/overlay (no focus semantics apply), add a ' +
      'line-level eslint-disable-next-line with a one-line reason.',
  },
]

// bu-yykif: ban `animate-pulse` (Tailwind's shimmer/pulse loading utility).
// The Motion Vocabulary in openspec/specs/dashboard-design-language/spec.md §
// Motion Vocabulary explicitly forbids "skeleton-pulse" — only four named
// animations exist program-wide, and this is not one of them. All ~55
// pre-existing sites (loading skeletons, "live" status dots) were migrated to
// static (non-animating) placeholders in the same change that adds this rule
// (bu-qvnce.7 slice 5) — see components/ui/skeleton.tsx and the shared
// skeleton components under components/skeletons/ for the canonical static
// treatment. A background-refetch dim (as opposed to an initial-load
// placeholder) should use FetchingDim (components/ui/fetching-dim.tsx)
// instead of any pulse/shimmer animation.
const ANIMATE_PULSE_SELECTORS = [
  {
    selector: 'Literal[value=/\\banimate-pulse\\b/]',
    message:
      'animate-pulse is forbidden (bu-yykif, bu-qvnce.7 slice 5) — the Motion Vocabulary ' +
      '(dashboard-design-language spec § Motion Vocabulary) explicitly bans skeleton-pulse. ' +
      'Use a static (non-animating) placeholder block for an initial-load skeleton, or ' +
      'FetchingDim (components/ui/fetching-dim.tsx) for a background-refetch dim.',
  },
  {
    selector: 'TemplateElement[value.raw=/\\banimate-pulse\\b/]',
    message:
      'animate-pulse is forbidden (bu-yykif, bu-qvnce.7 slice 5) — the Motion Vocabulary ' +
      '(dashboard-design-language spec § Motion Vocabulary) explicitly bans skeleton-pulse. ' +
      'Use a static (non-animating) placeholder block for an initial-load skeleton, or ' +
      'FetchingDim (components/ui/fetching-dim.tsx) for a background-refetch dim.',
  },
]

// bu-qvnce.14 slice 3: poll-policy lint. A bare numeric refetchInterval
// hides whether an interval is the PRIMARY update path or a safety-net
// reconciliation sweep sitting behind a live bus event -- see
// src/lib/poll-policy.ts. Scoped to exactly the files already migrated onto
// named tokens (POLL_BUS_RECONCILE_MS or an equally-named local constant)
// rather than repo-wide: ~140 other refetchInterval call sites across the
// app (use-health.ts, use-memory.ts, use-finance.ts, use-whatsapp.ts, etc.)
// have not been migrated yet -- broadening this list is tracked as a
// bu-qvnce.14 follow-up, not silently expanded here (a blanket rule would
// break CI on every one of those pre-existing, legitimate intervals).
const POLL_POLICY_FILES = [
  'src/hooks/use-butlers.ts',
  'src/hooks/use-timeline.ts',
  'src/hooks/use-messenger.ts',
  'src/hooks/use-sessions.ts',
  'src/hooks/use-approvals.ts',
  'src/hooks/use-spend.ts',
  'src/hooks/use-issues.ts',
  // bu-01r64.3: the 8th bus-covered hook -- previously had NO refetchInterval
  // at all, now onto the same named-token pattern as the other seven.
  'src/hooks/use-notifications.ts',
  // bu-k8888: ingestionPatch invalidates this hook's query-key prefix; its
  // default interval is therefore a bus-aware reconciliation sweep.
  'src/hooks/use-ingestion-events.ts',
]

const POLL_POLICY_SELECTORS = [
  {
    // Descendant (not direct-child) combinator: also catches a numeric
    // literal nested inside `options?.refetchInterval ?? 30_000` or
    // `5 * 60_000`, not just a bare `refetchInterval: 30_000`.
    selector: 'Property[key.name="refetchInterval"] Literal[value=type(number)][value>=0]',
    message:
      'refetchInterval must use a named poll-policy token (POLL_BUS_RECONCILE_MS from ' +
      'src/lib/poll-policy.ts, or an equally-named local constant), not a raw numeric ' +
      'literal -- a bare number hides whether this interval is a bus-covered reconciliation ' +
      'sweep or the primary update path (bu-qvnce.14 slice 3).',
  },
]

// bu-sd0l7.3: ban local re-declarations of `formatDuration`/`formatCost`.
// A 2026-07-10 audit found 12 formatDuration/formatDurationMs clones and 3
// formatCost clones (plus 2 formatCurrency clones reproducing the exact
// "$0.00" sub-cent bug lib/format-cost.ts's header documents) across
// components/ and pages/ despite src/lib/format-cost.ts and
// src/lib/format-duration.ts already existing (or, for duration, being
// added in the same change) as the canonical home. True duplicates were
// consolidated onto lib/format-duration.ts (formatDurationMs /
// formatDurationCompact / formatDurationTicks — three genuinely distinct
// rendering contracts, each independently duplicated at 2+ sites) and
// lib/format-cost.ts (formatCostUsd / formatCostUsdPrecise). A handful of
// remaining local `formatDuration` declarations are genuinely different
// contracts (relative "X ago" suffix, HH:MM clock, day-scale durations) and
// are exempted with a line-level eslint-disable-next-line + comment rather
// than forced onto one of the lib shapes (which would have silently changed
// their rendered output) -- see lib/format-duration.ts's own header for the
// full rationale. Local wrapper functions that changed shape/signature and
// delegate to a lib helper (e.g. formatSessionSpan, formatEpisodeDuration)
// are intentionally NOT named formatDuration/formatCost, so they don't need
// an exemption.
const FORMAT_CLONE_SELECTORS = [
  {
    selector: 'FunctionDeclaration[id.name=/^(?:formatDuration|formatCost)$/]',
    message:
      'Do not locally redeclare formatDuration/formatCost (bu-sd0l7.3 consolidated 15 clones ' +
      'onto lib/). Import formatDurationMs/formatDurationCompact/formatDurationTicks from ' +
      '"@/lib/format-duration", or formatCostUsd/formatCostUsdPrecise from ' +
      '"@/lib/format-cost". If this local formatter is a genuinely different rendering ' +
      'contract (see lib/format-duration.ts header), add a line-level ' +
      'eslint-disable-next-line with a one-line reason instead of matching one of the lib ' +
      'shapes and silently changing rendered output.',
  },
  {
    selector: 'VariableDeclarator[id.name=/^(?:formatDuration|formatCost)$/]',
    message:
      'Do not locally redeclare formatDuration/formatCost (bu-sd0l7.3 consolidated 15 clones ' +
      'onto lib/). Import formatDurationMs/formatDurationCompact/formatDurationTicks from ' +
      '"@/lib/format-duration", or formatCostUsd/formatCostUsdPrecise from ' +
      '"@/lib/format-cost". If this local formatter is a genuinely different rendering ' +
      'contract (see lib/format-duration.ts header), add a line-level ' +
      'eslint-disable-next-line with a one-line reason instead of matching one of the lib ' +
      'shapes and silently changing rendered output.',
  },
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      // bu-86c4c.16: static a11y gate — catches missing alt text, invalid ARIA
      // attrs/roles, non-interactive elements with click handlers, etc. at
      // lint time instead of relying solely on runtime axe assertions.
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // aria-role by default validates any prop literally named `role`, even
      // on custom (non-DOM) components — e.g. IdentityChip's `role` prop is
      // a domain concept ("owner" | "member" | "unknown"), not the ARIA role
      // attribute. ignoreNonDOM restricts the check to real host elements
      // (lowercase JSX tags), where `role=` genuinely is ARIA.
      'jsx-a11y/aria-role': ['error', { ignoreNonDOM: true }],
      // no-autofocus's own justification is page-load autofocus disorienting
      // a user who didn't ask for it. Every autoFocus in this codebase (~25
      // sites, audited bu-86c4c.16) is inside a Dialog/Sheet/inline-editor
      // that renders in direct response to an explicit user action (opening
      // a dialog, clicking "edit") — moving focus to the primary field there
      // is the WAI-ARIA APG-recommended behavior, not the anti-pattern the
      // rule exists to catch. Disabled repo-wide rather than 25 individual
      // eslint-disable comments; revisit per-site if a genuine page-load
      // autofocus is ever introduced.
      'jsx-a11y/no-autofocus': 'off',
    },
  },
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // Context provider + its accompanying hooks (useRegisterCommands,
    // useCommandMenuActions) are one small, tightly-coupled unit — splitting
    // them into separate files just to satisfy fast-refresh would hurt
    // readability for no real benefit (same tradeoff as src/components/ui above).
    files: ['src/lib/command-registry.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // Same tradeoff as command-registry.tsx above: PageContextProvider plus
    // its two accompanying hooks (usePageContext, usePageContextCapture)
    // are one tightly-coupled unit (bu-p6ey8.4).
    files: ['src/lib/page-context.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // Same tradeoff again: ShortcutRegistryProvider plus its accompanying
    // hooks (useRegisterShortcut, useShortcutHintEntries) and the
    // isShortcutTargetSuspended guard are one tightly-coupled unit
    // (bu-qvnce.11) — mirrors command-registry.tsx's shape deliberately.
    files: ['src/hooks/use-register-shortcut.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // Same tradeoff again: EventBusProvider plus its accompanying hooks
    // (useEventBus, useBusEvent) are one tightly-coupled unit (bu-qvnce.14
    // slice 1) — mirrors command-registry.tsx's shape deliberately.
    files: ['src/lib/event-bus.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  // ---------------------------------------------------------------------------
  // Chart color plumbing guard (bu-86c4c.5) + one-visual-language guards
  // (bu-86c4c.6). Split into three non-overlapping file-sets — see the
  // "IMPORTANT" comment on the selector consts above for why this can't be
  // layered as separate partial config objects.
  // ---------------------------------------------------------------------------
  {
    // Plain .ts files can never contain JSX (TypeScript rejects JSX syntax
    // outside .tsx), so neither the hex-in-JSX guard nor the Eyebrow/Voice
    // redeclaration guard (which specifically targets JSX-returning
    // components) can ever fire here — only the theme-token guards apply.
    files: ['**/*.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
      ],
    },
  },
  {
    // Every .tsx file EXCEPT components/ui/ (the canonical primitive home)
    // and the one documented composition-wrapper exception.
    files: ['**/*.tsx'],
    ignores: ['src/components/ui/**', 'src/components/secrets/passport/atoms.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...HEX_COLOR_SELECTORS,
        ...PRIMITIVE_REDECLARATION_SELECTORS,
        ...HANDROLLED_OVERLAY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
      ],
    },
  },
  {
    // components/ui/ and the passport composition wrapper: still theme-token
    // and hex clean, but exempt from the redeclaration ban (they ARE the
    // canonical declaration / the accepted wrapper pattern).
    files: ['src/components/ui/**/*.tsx', 'src/components/secrets/passport/atoms.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...HEX_COLOR_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
      ],
    },
  },
  {
    // bu-qvnce.14 slice 3: poll-policy token enforcement, .ts hook files.
    // Every one of these files has been fully migrated off raw numeric
    // refetchInterval -- see the POLL_POLICY_FILES comment above for why
    // this is scoped rather than repo-wide. Must repeat the base .ts
    // selectors (HSL/STATUS) here too: flat config's `no-restricted-syntax`
    // does NOT merge across matching blocks for the same file, so this block
    // fully replaces (not adds to) the generic '**/*.ts' block for these files.
    files: POLL_POLICY_FILES,
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
      ],
    },
  },
  {
    // Same poll-policy enforcement for ApprovalsPage.tsx and
    // SettingsConsolePage.tsx (bu-3quv8: its ["settings-console"] query is now
    // bus-covered by header_delta/attention_add/attention_remove, see
    // use-settings-console-live.ts) -- both .tsx files, so they must repeat
    // the general '**/*.tsx' block's full selector set.
    files: ['src/pages/ApprovalsPage.tsx', 'src/pages/SettingsConsolePage.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...HEX_COLOR_SELECTORS,
        ...PRIMITIVE_REDECLARATION_SELECTORS,
        ...HANDROLLED_OVERLAY_SELECTORS,
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
      ],
    },
  },
  // ---------------------------------------------------------------------------
  // No-LLM-Narration Invariant (butler-secrets spec §No-LLM-Narration Invariant)
  //
  // The /secrets surfaces MUST NOT trigger LLM inference. Importing the
  // Anthropic SDK anywhere under the secrets page/component directories would
  // be a clear violation of this binding invariant and the cost guarantee.
  // ---------------------------------------------------------------------------
  {
    files: [
      'src/pages/Secrets/**/*.{ts,tsx}',
      'src/pages/SecretsPage.{ts,tsx}',
      'src/components/secrets/**/*.{ts,tsx}',
    ],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [
          {
            group: ['@anthropic-ai/sdk', '@anthropic-ai/sdk/*'],
            message:
              'LLM SDK imports are forbidden in /secrets surfaces. ' +
              'See butler-secrets §No-LLM-Narration Invariant.',
          },
        ],
      }],
    },
  },
])
