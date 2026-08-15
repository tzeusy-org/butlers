import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

import {
  DYNAMIC_VALUE_MARKER,
  TAILWIND_COLOR_UTILITY_SPELLINGS,
  findPrivateIdentityReferences,
} from './scripts/visual-role-css-guard.mjs'

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

// bu-qvnce.14 slice 3 / bu-ep4ks.15: poll-policy lint. A bare numeric
// refetchInterval hides whether an interval is the PRIMARY update path or a
// safety-net reconciliation sweep sitting behind a live bus event -- see
// src/lib/poll-policy.ts. Originally scoped to only the 8 files already
// migrated onto named tokens (~140 other call sites deferred as a follow-up)
// -- bu-ep4ks.15 closed that gap by migrating every remaining refetchInterval
// site onto a named token (either POLL_BUS_RECONCILE_MS/an equally-named
// shared token for bus-covered surfaces, or a locally-declared *_POLL_MS
// constant for surfaces with no fleet-bus event type -- see e.g.
// use-butlers.ts's BUTLERS_POLL_MS, use-health.ts's HEALTH_POLL_MS) and
// applying POLL_POLICY_SELECTORS repo-wide via the base '**/*.ts' / '**/*.tsx'
// blocks below instead of a hand-maintained file allowlist.
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
      'sweep or the primary update path (bu-qvnce.14 slice 3, repo-wide since bu-ep4ks.15). ' +
      'If this is a test asserting the actual resolved cadence (not the hook echoing its ' +
      'own constant back), use a line-level eslint-disable-next-line with a one-line reason ' +
      'instead of importing the constant, which would make the assertion tautological.',
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

// Keyboard handling belongs to the two registries: the app-wide shell map
// and the page-scoped shortcut registry. A third listener would drift from
// their shared editable-field, dialog, and pending-chord protections.
const KEYDOWN_LISTENER_SELECTORS = [
  {
    selector:
      'CallExpression[callee.property.name="addEventListener"][arguments.0.value=/^key(?:down|up)$/]',
    message:
      'Raw keydown listeners are forbidden outside use-keyboard-shortcuts.ts and ' +
      'use-register-shortcut.tsx. Register the binding through the appropriate keyboard registry ' +
      'so editable-field and dialog suspension stay consistent.',
  },
]

// bu-ep4ks.11 / bu-3dp0c: ban bare window.confirm (and global confirm())
// repo-wide. window.confirm cannot show a pending state, cannot carry
// evidence, and visually diverges from the fleet's AlertDialog everywhere
// else. Originally scoped to the four sites bu-ep4ks.11 cited
// (NO_WINDOW_CONFIRM_FILES, since retired) because two pre-existing call
// sites -- pages/EntityDetailPage.tsx, components/butler-detail/
// ButlerFinanceFinancesTab.tsx -- were out of that bead's scope and a
// repo-wide ban would have broken CI on them. bu-3dp0c migrated both onto
// ConfirmDialog, so every known call site is gone and the ban is now applied
// via the shared '**/*.ts' / '**/*.tsx' blocks below (plus every per-file
// override block, since flat config replaces rather than merges
// no-restricted-syntax for a file matched by more than one block -- see the
// IMPORTANT comment atop this file).
const NO_WINDOW_CONFIRM_SELECTORS = [
  {
    selector:
      'CallExpression[callee.object.name="window"][callee.property.name="confirm"]',
    message:
      'window.confirm is banned in this file (bu-ep4ks.11). Use ConfirmDialog ' +
      '(components/ui/confirm-dialog.tsx) instead -- it shows a pending state, can carry ' +
      'evidence, and matches the fleet\'s AlertDialog visual language.',
  },
  {
    selector: 'CallExpression[callee.name="confirm"]',
    message:
      'window.confirm is banned in this file (bu-ep4ks.11). Use ConfirmDialog ' +
      '(components/ui/confirm-dialog.tsx) instead -- it shows a pending state, can carry ' +
      'evidence, and matches the fleet\'s AlertDialog visual language.',
  },
]

// bu-ep4ks.15: ban var(--category-N), a private Butler identity token, from
// standing in for a live STATUS color. StateDot.tsx's exported
// TONE_COLORS/STATE_COLORS registry is the canonical status-color source
// (green/amber/red/neutral only, per dashboard-design-language spec § State
// Color Discipline). Identity tokens belong exclusively to ButlerMark and
// never authorize a topology or status rendering.
//
// Scoped to NO_CATEGORICAL_STATUS_FILES (the two files this bead's registry
// consolidation touches) rather than repo-wide: a broader audit of the ~17
// files found using raw blue/purple/etc Tailwind shades for various badges
// and banners (rule-promotion-banner.tsx, ingestion/StatusBadge.tsx,
// education/QuizHistoryList.tsx, etc.) is a separate, larger sweep -- most of
// those are informational-tone banners or fixed categorical tags, not all a
// "healthy" status collision, and need per-file judgment this bead's scope
// doesn't cover. Follow-up, not silently expanded here (mirrors the
// POLL_POLICY_FILES scoping precedent above; NO_WINDOW_CONFIRM_FILES, an
// earlier instance of the same pattern, was retired once bu-3dp0c migrated
// its last two call sites and the ban went repo-wide).
const NO_CATEGORICAL_STATUS_FILES = [
  'src/components/ui/StateDot.tsx',
  'src/components/topology/TopologyGraph.tsx',
]

const NO_CATEGORICAL_STATUS_SELECTORS = [
  {
    selector: 'Literal[value=/var\\(--category-\\d+\\)/]',
    message:
      'var(--category-N) is a private Butler identity token, not a live status color. Use ' +
      'StateDot\'s exported TONE_COLORS/STATE_COLORS (var(--red)/var(--amber)/var(--green) ' +
      'or neutral) for operational state; Butler identity stays inside ButlerMark.',
  },
]

// bu-d3z0t: blue/purple shades are not an operational-status dialect. This
// guard is deliberately scoped to the post-bu-ep4ks.15 audit population,
// rather than promoted repo-wide before every remaining informational and
// categorical use has received the same semantic review. A documented
// line-level exemption is allowed only for a fixed category or informational
// prompt; live process state must use StateDot's exported registry/tokens.
const BLUE_PURPLE_STATUS_AUDIT_FILES = [
  'src/components/approvals/approval-teaching-digest.tsx',
  'src/components/approvals/autonomy-panel.tsx',
  'src/components/approvals/autonomy-suggestions-banner.tsx',
  'src/components/approvals/rule-promotion-banner.tsx',
  'src/components/approvals/rule-promotion-stats.tsx',
  'src/components/butler-detail/ButlerQaInvestigationsTab.tsx',
  'src/components/education/CurriculumActions.tsx',
  'src/components/education/QuizHistoryList.tsx',
  'src/components/ingestion/StatusBadge.tsx',
  'src/components/ingestion/TimelineTab.tsx',
  'src/components/ingestion/timeline/HourFlameStrip.tsx',
  'src/components/qa/PRPanel.tsx',
  'src/components/relationship/ContactChannelCard.tsx',
  'src/components/schedules/ScheduleTable.tsx',
  'src/components/timeline/TimelineLedger.tsx',
  'src/pages/ApprovalsPage.tsx',
]

const BLUE_PURPLE_STATUS_SELECTORS = [
  {
    selector:
      'Literal[value=/\\b(?:bg|text|border|ring|decoration|from|via|to|fill|stroke|outline|divide|caret|accent|shadow)-(?:sky|cyan|blue|indigo|violet|purple|fuchsia)-(?:50|100|150|200|300|400|500|600|700|800|900|950)\\b/]',
    message:
      'Raw blue/purple Tailwind status classes are banned in audited files (bu-d3z0t). ' +
      'Use StateDot\'s exported state/tone registry for live operational state. A fixed ' +
      'category or informational prompt may retain its shade only with a line-level ' +
      'eslint-disable-next-line and one-line semantic reason.',
  },
  {
    selector:
      'TemplateElement[value.raw=/\\b(?:bg|text|border|ring|decoration|from|via|to|fill|stroke|outline|divide|caret|accent|shadow)-(?:sky|cyan|blue|indigo|violet|purple|fuchsia)-(?:50|100|150|200|300|400|500|600|700|800|900|950)\\b/]',
    message:
      'Raw blue/purple Tailwind status classes are banned in audited files (bu-d3z0t). ' +
      'Use StateDot\'s exported state/tone registry for live operational state. A fixed ' +
      'category or informational prompt may retain its shade only with a line-level ' +
      'eslint-disable-next-line and one-line semantic reason.',
  },
]

// bu-6jv4m.15: semantic role guard. Butler identity tokens belong only to
// ButlerMark; every other local taxonomy must use the dedicated categorical
// ramp. This is intentionally repo-wide (the old file allowlists certified
// unaudited consumers by path).
//
// Private CSS-variable detection is a custom semantic rule instead of an
// ESTree raw-text selector. CSS custom properties can legally contain escape
// sequences, and var() permits whitespace/comments before the property name;
// Tailwind v4 also supports both utility-(--token) and
// utility-(color:--token). The owned normalizer understands that bounded
// grammar and compares canonical property names. The remaining selectors
// enforce the separately typed public API boundary.
const VISUAL_ROLE_GUARD_PLUGIN = {
  rules: {
    'no-private-identity-token': {
      meta: {
        docs: {
          description:
            'forbid private ButlerMark identity custom properties outside ButlerMark',
        },
        messages: {
          privateIdentity:
            'Butler identity token {{property}} is private to ButlerMark (bu-6jv4m.15). Use a typed ' +
            'semantic role helper instead of embedding an identity token.',
        },
        schema: [],
        type: 'problem',
      },
      create(context) {
        function scopeVariable(node) {
          let scope = context.sourceCode.getScope(node)

          while (scope) {
            const variable = scope.set.get(node.name)
            if (variable) return variable
            scope = scope.upper
          }

          return null
        }

        function constInitializer(node) {
          const variable = scopeVariable(node)
          const [definition] = variable?.defs ?? []
          if (
            !variable ||
            variable.defs.length !== 1 ||
            definition?.type !== 'Variable' ||
            definition.parent?.kind !== 'const' ||
            definition.node.id.type !== 'Identifier' ||
            definition.node.id.name !== node.name ||
            !definition.node.init
          ) {
            return null
          }
          return { initializer: definition.node.init, variable }
        }

        function destructuringPath(pattern, bindingName, path = []) {
          pattern = unwrapStaticExpression(pattern)
          if (pattern.type === 'AssignmentPattern') {
            const nested = destructuringPath(pattern.left, bindingName, path)
            if (!nested?.length) return nested
            const lastIndex = nested.length - 1
            const last = nested[lastIndex]
            nested[lastIndex] = {
              fallback: pattern.right,
              property: typeof last === 'object' ? last.property : last,
            }
            return nested
          }
          if (pattern.type === 'Identifier') {
            return pattern.name === bindingName ? path : null
          }
          if (pattern.type === 'ObjectPattern') {
            for (const property of pattern.properties) {
              if (property.type !== 'Property' || property.kind !== 'init' || property.method) {
                continue
              }
              const propertyName = property.computed
                ? stringConstructionValue(property.key)
                : memberPropertyName({ computed: false, property: property.key })
              if (typeof propertyName !== 'string') continue
              const nested = destructuringPath(
                property.value,
                bindingName,
                [...path, propertyName],
              )
              if (nested) return nested
            }
          }
          if (pattern.type === 'ArrayPattern') {
            for (const [index, element] of pattern.elements.entries()) {
              if (!element || element.type === 'RestElement') continue
              const nested = destructuringPath(element, bindingName, [...path, index])
              if (nested) return nested
            }
          }
          return null
        }

        function constDestructuredBinding(node) {
          const variable = scopeVariable(node)
          const [definition] = variable?.defs ?? []
          if (
            !variable ||
            variable.defs.length !== 1 ||
            definition?.type !== 'Variable' ||
            definition.parent?.kind !== 'const' ||
            !['ObjectPattern', 'ArrayPattern'].includes(definition.node.id.type) ||
            !definition.node.init
          ) {
            return null
          }

          const path = destructuringPath(definition.node.id, node.name)
          return path ? { initializer: definition.node.init, path, variable } : null
        }

        function memberPropertyName(node, resolvingVariables = new Set(), localValues = new Map()) {
          if (node.computed) {
            if (node.property.type === 'Literal') return node.property.value
            const value = stringConstructionValue(node.property, resolvingVariables, localValues)
            return value === DYNAMIC_VALUE_MARKER ? null : value
          }
          return node.property.type === 'Identifier' ? node.property.name : null
        }

        function isUnshadowedGlobalIdentifier(node, name) {
          if (node.type !== 'Identifier' || node.name !== name) return false
          const variable = scopeVariable(node)
          return !variable || !variable.defs.length
        }

        function resolvesImmutableAlias(node, predicate, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (predicate(node)) return true
          if (node.type !== 'Identifier') return false

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return false

          resolvingVariables.add(binding.variable)
          const resolved = resolvesImmutableAlias(
            binding.initializer,
            predicate,
            resolvingVariables,
          )
          resolvingVariables.delete(binding.variable)
          return resolved
        }

        function isGlobalFunctionReference(node, name) {
          return resolvesImmutableAlias(node, (candidate) =>
            isUnshadowedGlobalIdentifier(candidate, name),
          )
        }

        function isGlobalMemberReference(node, objectName, propertyName) {
          return resolvesImmutableAlias(
            node,
            (candidate) =>
              candidate.type === 'MemberExpression' &&
              memberPropertyName(candidate) === propertyName &&
              isUnshadowedGlobalIdentifier(candidate.object, objectName),
          )
        }

        function isGetComputedStyleReference(node) {
          return (
            isGlobalFunctionReference(node, 'getComputedStyle') ||
            isGlobalMemberReference(node, 'window', 'getComputedStyle') ||
            isGlobalMemberReference(node, 'globalThis', 'getComputedStyle')
          )
        }

        function isPrototypeMethodCall(node, constructorName, methodName) {
          return (
            node.type === 'CallExpression' &&
            node.callee.type === 'MemberExpression' &&
            memberPropertyName(node.callee) === 'call' &&
            node.callee.object.type === 'MemberExpression' &&
            memberPropertyName(node.callee.object) === methodName &&
            node.callee.object.object.type === 'MemberExpression' &&
            memberPropertyName(node.callee.object.object) === 'prototype' &&
            isUnshadowedGlobalIdentifier(node.callee.object.object.object, constructorName)
          )
        }

        // The private-token boundary is about the resolved operation, not its
        // surface spelling. Keep this allowlist deliberately small: these are
        // the only prototype methods whose static result we model, plus the
        // two Function meta-invokers needed to follow a known method through
        // call/apply. Unknown dynamic functions remain outside this evaluator.
        const STATIC_RESOLVER_METHODS = {
          Array: {
            join: 'array-join',
            reduce: 'array-reduce',
          },
          CSSStyleDeclaration: {
            getPropertyValue: 'cssom',
          },
          Function: {
            apply: 'function-apply',
            call: 'function-call',
          },
          String: {
            concat: 'string-concat',
            replace: 'string-replace',
            replaceAll: 'string-replace-all',
          },
          StylePropertyMapReadOnly: {
            get: 'typed-om',
          },
        }

        const INSTANCE_RESOLVER_METHODS = {
          concat: 'string-concat',
          get: 'typed-om',
          getPropertyValue: 'cssom',
          join: 'array-join',
          reduce: 'array-reduce',
          replace: 'string-replace',
          replaceAll: 'string-replace-all',
        }

        const AMBIGUOUS_RESOLVER_TARGET = 'ambiguous-resolver'

        // These operations neither return element references nor invoke
        // element callbacks/coercion hooks. Shallow copies, iterators that
        // expose values, spreads, and callback-bearing operations require a
        // separate proof that every element is a primitive value.
        const NON_ALIASING_CONTAINER_METHODS = new Set([
          'includes',
          'indexOf',
          'keys',
          'lastIndexOf',
        ])

        const SHALLOW_ALIASING_CONTAINER_METHODS = new Set([
          'at',
          'concat',
          'entries',
          'flat',
          'slice',
          'toReversed',
          'toSorted',
          'toSpliced',
          'values',
          'with',
        ])

        const readOnlyBindingCache = new WeakMap()

        function declaredVariables(node) {
          return context.sourceCode.getDeclaredVariables(node)
        }

        function transparentParent(node) {
          let current = node
          while (
            current.parent &&
            (current.parent.type === 'TSAsExpression' ||
              current.parent.type === 'TSTypeAssertion' ||
              current.parent.type === 'TSNonNullExpression' ||
              current.parent.type === 'TSSatisfiesExpression' ||
              current.parent.type === 'ChainExpression') &&
            current.parent.expression === current
          ) {
            current = current.parent
          }
          return current
        }

        function aliasDeclarationIsReadOnly(valueNode, resolvingVariables) {
          const value = transparentParent(valueNode)
          const declarator = value.parent
          if (
            declarator?.type !== 'VariableDeclarator' ||
            declarator.init !== value ||
            declarator.parent?.kind !== 'const'
          ) {
            return null
          }
          const aliases = declaredVariables(declarator)
          return aliases.length > 0 && aliases.every((variable) =>
            bindingReferencesAreReadOnly(variable, resolvingVariables),
          )
        }

        // A const binding freezes only the binding, not the referenced object.
        // Prove every reachable alias is read-only before interpreting an
        // object/array initializer as a stable resolver registry. Unknown
        // escapes fail closed; cycles are rejected by the shared visited set.
        function bindingReferencesAreReadOnly(variable, resolvingVariables = new Set()) {
          if (readOnlyBindingCache.has(variable)) return readOnlyBindingCache.get(variable)
          if (resolvingVariables.has(variable)) return false

          resolvingVariables.add(variable)
          const readOnly = variable.references.every((reference) => {
            if (reference.init) return true
            if (reference.isWrite()) return false
            if (!reference.isRead()) return true
            return referenceUseIsReadOnly(reference.identifier, resolvingVariables)
          })
          resolvingVariables.delete(variable)
          readOnlyBindingCache.set(variable, readOnly)
          return readOnly
        }

        function referenceUseIsReadOnly(identifier, resolvingVariables) {
          const directAlias = aliasDeclarationIsReadOnly(identifier, resolvingVariables)
          if (directAlias !== null) return directAlias

          let current = identifier
          const memberPath = []
          while (current.parent?.type === 'MemberExpression' && current.parent.object === current) {
            current = current.parent
            memberPath.push(memberPropertyName(current))
          }

          const alias = aliasDeclarationIsReadOnly(current, resolvingVariables)
          if (alias !== null) return alias

          const parent = current.parent
          if (
            (parent?.type === 'AssignmentExpression' && parent.left === current) ||
            (parent?.type === 'UpdateExpression' && parent.argument === current) ||
            (parent?.type === 'UnaryExpression' && parent.operator === 'delete')
          ) {
            return false
          }

          if (parent?.type === 'SpreadElement') {
            return referenceReceiverIsDeeplyImmutableArray(
              identifier,
              memberPath,
              resolvingVariables,
            )
          }
          if (parent?.type !== 'CallExpression' || parent.callee !== current || !memberPath.length) {
            return memberPath.length > 0 &&
              (parent?.type === 'BinaryExpression' || parent?.type === 'TemplateLiteral')
          }

          const methodName = memberPath.at(-1)
          if (methodName === 'call' || methodName === 'apply' || methodName === 'bind') {
            return memberPath.length > 1
          }
          if (NON_ALIASING_CONTAINER_METHODS.has(methodName)) {
            return referenceReceiverIsStaticArray(
              identifier,
              memberPath.slice(0, -1),
              resolvingVariables,
            )
          }
          return SHALLOW_ALIASING_CONTAINER_METHODS.has(methodName) &&
            referenceReceiverIsDeeplyImmutableArray(
              identifier,
              memberPath.slice(0, -1),
              resolvingVariables,
            )
        }

        function referenceReceiverIsStaticArray(identifier, path, resolvingVariables) {
          const binding = constInitializer(identifier)
          const destructured = binding ? null : constDestructuredBinding(identifier)
          let value
          if (binding) {
            value = resolvedImmutableProperty(binding.initializer)
          } else if (destructured) {
            value = staticImmutablePath(
              destructured.initializer,
              destructured.path,
              resolvingVariables,
              new Map(),
            )
          } else {
            return false
          }
          if (value.status !== 'resolved') return false
          const receiver = path.length
            ? staticImmutablePath(value.value, path, resolvingVariables, new Map())
            : value
          return receiver.status === 'resolved' &&
            constArrayElements(receiver.value, resolvingVariables, new Map()) !== null
        }

        function immutableArrayElementCannotAliasMutableState(
          node,
          resolvingVariables = new Set(),
        ) {
          node = unwrapStaticExpression(node)
          if (node.type === 'Literal') {
            return node.value === null ||
              ['string', 'number', 'boolean', 'bigint'].includes(typeof node.value)
          }
          if (node.type !== 'Identifier') return false

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return false
          if (!bindingReferencesAreReadOnly(binding.variable, resolvingVariables)) return false
          resolvingVariables.add(binding.variable)
          const immutable = immutableArrayElementCannotAliasMutableState(
            binding.initializer,
            resolvingVariables,
          )
          resolvingVariables.delete(binding.variable)
          return immutable
        }

        function referenceReceiverIsDeeplyImmutableArray(
          identifier,
          path,
          resolvingVariables,
        ) {
          const binding = constInitializer(identifier)
          const destructured = binding ? null : constDestructuredBinding(identifier)
          let value
          if (binding) {
            value = resolvedImmutableProperty(binding.initializer)
          } else if (destructured) {
            value = staticImmutablePath(
              destructured.initializer,
              destructured.path,
              resolvingVariables,
              new Map(),
            )
          } else {
            return false
          }
          if (value.status !== 'resolved') return false
          const receiver = path.length
            ? staticImmutablePath(value.value, path, resolvingVariables, new Map())
            : value
          if (receiver.status !== 'resolved') return false
          const elements = constArrayElements(receiver.value, resolvingVariables, new Map())
          return elements !== null && elements.every((element) =>
            immutableArrayElementCannotAliasMutableState(element, resolvingVariables),
          )
        }

        function resolverContainerExpressionIsReadOnly(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (node.type === 'ArrayExpression' || node.type === 'ObjectExpression') return true
          if (node.type === 'Identifier') {
            const binding = constInitializer(node)
            if (
              !binding ||
              resolvingVariables.has(binding.variable) ||
              !bindingReferencesAreReadOnly(binding.variable)
            ) {
              return false
            }
            resolvingVariables.add(binding.variable)
            const readOnly = resolverContainerExpressionIsReadOnly(
              binding.initializer,
              resolvingVariables,
            )
            resolvingVariables.delete(binding.variable)
            return readOnly
          }
          if (node.type === 'MemberExpression') {
            return resolverContainerExpressionIsReadOnly(node.object, resolvingVariables)
          }
          if (node.type !== 'CallExpression' || node.callee.type !== 'MemberExpression') {
            return false
          }
          const methodName = memberPropertyName(node.callee)
          if (!SHALLOW_ALIASING_CONTAINER_METHODS.has(methodName)) return false
          const elements = constArrayElements(
            node.callee.object,
            resolvingVariables,
            new Map(),
          )
          return elements !== null && elements.every((element) =>
            immutableArrayElementCannotAliasMutableState(element, resolvingVariables),
          ) && resolverContainerExpressionIsReadOnly(
            node.callee.object,
            resolvingVariables,
          )
        }

        function resolverMethodKind(constructorName, methodName) {
          return STATIC_RESOLVER_METHODS[constructorName]?.[methodName] ?? null
        }

        function prototypeConstructorName(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (
            node.type === 'MemberExpression' &&
            memberPropertyName(node) === 'prototype' &&
            node.object.type === 'Identifier' &&
            isUnshadowedGlobalIdentifier(node.object, node.object.name)
          ) {
            return node.object.name
          }
          if (node.type !== 'Identifier') return null

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return null

          resolvingVariables.add(binding.variable)
          const constructorName = prototypeConstructorName(binding.initializer, resolvingVariables)
          resolvingVariables.delete(binding.variable)
          return constructorName
        }

        function directPrototypeResolverTarget(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (node.type !== 'MemberExpression') return null
          const constructorName = prototypeConstructorName(node.object, resolvingVariables)
          const methodName = memberPropertyName(node, resolvingVariables)
          return constructorName && typeof methodName === 'string'
            ? resolverMethodKind(constructorName, methodName)
            : null
        }

        function reflectedPrototypeResolverTarget(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (
            node.type === 'CallExpression' &&
            isGlobalMemberReference(node.callee, 'Reflect', 'get') &&
            node.arguments.length === 2 &&
            node.arguments.every((argument) => argument.type !== 'SpreadElement')
          ) {
            const constructorName = prototypeConstructorName(node.arguments[0], resolvingVariables)
            const methodName = stringConstructionValue(node.arguments[1], resolvingVariables)
            return constructorName && methodName !== DYNAMIC_VALUE_MARKER
              ? resolverMethodKind(constructorName, methodName)
              : null
          }

          if (
            node.type !== 'MemberExpression' ||
            memberPropertyName(node, resolvingVariables) !== 'value' ||
            node.object.type !== 'CallExpression' ||
            !isGlobalMemberReference(node.object.callee, 'Object', 'getOwnPropertyDescriptor') ||
            node.object.arguments.length !== 2 ||
            node.object.arguments.some((argument) => argument.type === 'SpreadElement')
          ) {
            return null
          }

          const constructorName = prototypeConstructorName(node.object.arguments[0], resolvingVariables)
          const methodName = stringConstructionValue(node.object.arguments[1], resolvingVariables)
          return constructorName && methodName !== DYNAMIC_VALUE_MARKER
            ? resolverMethodKind(constructorName, methodName)
            : null
        }

        function staticResolverTarget(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)

          const atElement = staticImmutableArrayAtElement(node, resolvingVariables)
          if (atElement.status === 'resolved') {
            return staticResolverTarget(atElement.value, resolvingVariables)
          }
          if (atElement.status === 'ambiguous') return AMBIGUOUS_RESOLVER_TARGET

          const prototypeTarget = directPrototypeResolverTarget(node, resolvingVariables)
          if (prototypeTarget) return prototypeTarget

          const reflectedTarget = reflectedPrototypeResolverTarget(node, resolvingVariables)
          if (reflectedTarget) return reflectedTarget

          if (node.type === 'MemberExpression') {
            const property = memberPropertyName(node, resolvingVariables)
            if (property === null) return AMBIGUOUS_RESOLVER_TARGET

            const immutableProperty = staticImmutableProperty(
              node.object,
              property,
              resolvingVariables,
            )
            if (immutableProperty.status === 'resolved') {
              return staticResolverTarget(immutableProperty.value, resolvingVariables)
            }
            if (immutableProperty.status === 'ambiguous') {
              return AMBIGUOUS_RESOLVER_TARGET
            }

            const instanceTarget = INSTANCE_RESOLVER_METHODS[property]
            if (instanceTarget) return instanceTarget
          }

          if (node.type !== 'Identifier') return null

          const destructured = constDestructuredBinding(node)
          if (destructured && !resolvingVariables.has(destructured.variable)) {
            resolvingVariables.add(destructured.variable)
            const target = destructuredResolverTarget(
              destructured.initializer,
              destructured.path,
              resolvingVariables,
            )
            resolvingVariables.delete(destructured.variable)
            if (target) return target
          }

          const binding = constInitializer(node)
          if (!binding) return null
          if (resolvingVariables.has(binding.variable)) return AMBIGUOUS_RESOLVER_TARGET

          resolvingVariables.add(binding.variable)
          const target = staticResolverTarget(binding.initializer, resolvingVariables)
          resolvingVariables.delete(binding.variable)
          return target
        }

        function destructuredResolverTarget(
          initializer,
          path,
          resolvingVariables = new Set(),
        ) {
          initializer = unwrapStaticExpression(initializer)
          if (!path.length) return staticResolverTarget(initializer, resolvingVariables)

          const [segment, ...remaining] = path
          const property = typeof segment === 'object' ? segment.property : segment
          if (typeof property === 'string') {
            if (
              property === 'prototype' &&
              initializer.type === 'Identifier' &&
              isUnshadowedGlobalIdentifier(initializer, initializer.name)
            ) {
              if (!remaining.length) return null
              const [methodName, ...methodRemainder] = remaining
              if (typeof methodName !== 'string') return null
              const methodTarget = resolverMethodKind(initializer.name, methodName)
              if (!methodTarget) return null
              if (methodRemainder.length === 0) return methodTarget
              return methodRemainder.length === 1 && methodRemainder[0] === 'call'
                ? 'function-call'
                : null
            }

            const constructorName = prototypeConstructorName(initializer, resolvingVariables)
            if (constructorName) {
              const methodTarget = resolverMethodKind(constructorName, property)
              if (!methodTarget) return null
              if (remaining.length === 0) return methodTarget
              return remaining.length === 1 && remaining[0] === 'call'
                ? 'function-call'
                : null
            }
          }

          const immutableProperty = staticImmutableProperty(
            initializer,
            property,
            resolvingVariables,
          )
          if (
            segment?.fallback &&
            (immutableProperty.status === 'missing' ||
              (immutableProperty.status === 'resolved' &&
                isStaticallyUndefined(immutableProperty.value)))
          ) {
            return destructuredResolverTarget(
              segment.fallback,
              remaining,
              resolvingVariables,
            )
          }
          if (immutableProperty.status === 'resolved') {
            return destructuredResolverTarget(
              immutableProperty.value,
              remaining,
              resolvingVariables,
            )
          }
          if (immutableProperty.status === 'ambiguous') {
            return AMBIGUOUS_RESOLVER_TARGET
          }

          if (typeof property !== 'string') return null

          const initializerTarget = staticResolverTarget(initializer, resolvingVariables)
          return initializerTarget && property === 'call' && remaining.length === 0
            ? 'function-call'
            : null
        }

        function staticBoundResolver(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (node.type === 'Identifier') {
            const binding = constInitializer(node)
            if (!binding || resolvingVariables.has(binding.variable)) return null

            resolvingVariables.add(binding.variable)
            const bound = staticBoundResolver(binding.initializer, resolvingVariables)
            resolvingVariables.delete(binding.variable)
            return bound
          }

          if (
            node.type !== 'CallExpression' ||
            node.callee.type !== 'MemberExpression' ||
            memberPropertyName(node.callee, resolvingVariables) !== 'bind' ||
            node.arguments.length < 1 ||
            node.arguments.some((argument) => argument.type === 'SpreadElement')
          ) {
            return null
          }

          const target = staticResolverCallable(node.callee.object, resolvingVariables)
          if (!target) return null

          return {
            arguments: [...target.arguments, ...node.arguments.slice(1)],
            kind: target.kind,
            receiver: target.receiver ?? node.arguments[0],
          }
        }

        function staticResolverCallable(node, resolvingVariables = new Set()) {
          const bound = staticBoundResolver(node, resolvingVariables)
          if (bound) return bound

          const kind = staticResolverTarget(node, resolvingVariables)
          return kind ? { arguments: [], kind, receiver: null } : null
        }

        function normalizeStaticResolverInvocation(
          invocation,
          resolvingVariables = new Set(),
          depth = 0,
        ) {
          if (depth > 8) return null
          if (invocation.kind !== 'function-call' && invocation.kind !== 'function-apply') {
            return invocation
          }
          if (!invocation.receiver) return null

          const target = staticResolverCallable(invocation.receiver, resolvingVariables)
          if (!target) return null

          if (invocation.kind === 'function-call') {
            if (invocation.arguments.length < 1) return null
            return invokeStaticResolver(
              target,
              invocation.arguments[0],
              invocation.arguments.slice(1),
              resolvingVariables,
              depth + 1,
            )
          }

          if (invocation.arguments.length !== 2) return null
          const appliedArguments = constArrayElements(
            invocation.arguments[1],
            resolvingVariables,
          )
          if (!appliedArguments) return null
          return invokeStaticResolver(
            target,
            invocation.arguments[0],
            appliedArguments,
            resolvingVariables,
            depth + 1,
          )
        }

        function invokeStaticResolver(
          callable,
          receiver,
          callArguments,
          resolvingVariables = new Set(),
          depth = 0,
        ) {
          return normalizeStaticResolverInvocation(
            {
              arguments: [...callable.arguments, ...callArguments],
              kind: callable.kind,
              receiver: callable.receiver ?? receiver,
            },
            resolvingVariables,
            depth,
          )
        }

        function staticResolverInvocation(node, resolvingVariables = new Set()) {
          if (node.type !== 'CallExpression' || node.arguments.some((argument) => argument.type === 'SpreadElement')) {
            return null
          }

          if (isGlobalMemberReference(node.callee, 'Reflect', 'apply')) {
            if (node.arguments.length !== 3) return null
            const callable = staticResolverCallable(node.arguments[0], resolvingVariables)
            const appliedArguments = constArrayElements(node.arguments[2], resolvingVariables)
            return callable && appliedArguments
              ? invokeStaticResolver(
                  callable,
                  node.arguments[1],
                  appliedArguments,
                  resolvingVariables,
                )
              : null
          }

          if (node.callee.type === 'MemberExpression') {
            const property = memberPropertyName(node.callee, resolvingVariables)
            const callable = staticResolverCallable(node.callee.object, resolvingVariables)
            if (property === 'call' && callable && node.arguments.length >= 1) {
              return invokeStaticResolver(
                callable,
                node.arguments[0],
                node.arguments.slice(1),
                resolvingVariables,
              )
            }
            if (property === 'apply' && callable && node.arguments.length === 2) {
              const appliedArguments = constArrayElements(node.arguments[1], resolvingVariables)
              return appliedArguments
                ? invokeStaticResolver(
                    callable,
                    node.arguments[0],
                    appliedArguments,
                    resolvingVariables,
                  )
                : null
            }
          }

          const callable = staticResolverCallable(node.callee, resolvingVariables)
          if (!callable) return null
          if (callable.receiver) {
            return invokeStaticResolver(callable, null, node.arguments, resolvingVariables)
          }

          // A directly invoked reflected or descriptor CSSOM method supplies
          // its receiver as the first argument. Treat that known boundary
          // conservatively even though arbitrary unbound prototype functions
          // remain outside the static evaluator.
          if (
            (callable.kind === 'cssom' || callable.kind === 'typed-om') &&
            node.arguments.length >= 2
          ) {
            return invokeStaticResolver(
              callable,
              node.arguments[0],
              node.arguments.slice(1),
              resolvingVariables,
            )
          }

          return null
        }

        function unwrapStaticExpression(node) {
          while (
            node &&
            (node.type === 'TSAsExpression' ||
              node.type === 'TSTypeAssertion' ||
              node.type === 'TSNonNullExpression' ||
              node.type === 'TSSatisfiesExpression' ||
              node.type === 'ChainExpression')
          ) {
            node = node.expression
          }
          return node
        }

        function syntheticStringLiteral(value) {
          return { type: 'Literal', value }
        }

        const IMMUTABLE_PROPERTY_MISSING = Object.freeze({ status: 'missing' })
        const IMMUTABLE_PROPERTY_AMBIGUOUS = Object.freeze({ status: 'ambiguous' })

        function resolvedImmutableProperty(value) {
          return { status: 'resolved', value }
        }

        function isStaticallyUndefined(node) {
          node = unwrapStaticExpression(node)
          return (
            isUnshadowedGlobalIdentifier(node, 'undefined') ||
            (node.type === 'UnaryExpression' && node.operator === 'void')
          )
        }

        function canonicalArrayIndex(propertyName) {
          if (
            typeof propertyName === 'number' &&
            Number.isInteger(propertyName) &&
            propertyName >= 0
          ) {
            return propertyName
          }
          if (
            typeof propertyName === 'string' &&
            /^(?:0|[1-9]\d*)$/.test(propertyName)
          ) {
            const index = Number(propertyName)
            return Number.isSafeInteger(index) ? index : null
          }
          return null
        }

        function staticImmutableArrayAtElement(
          node,
          resolvingVariables = new Set(),
          localValues = new Map(),
        ) {
          node = unwrapStaticExpression(node)
          if (
            node.type !== 'CallExpression' ||
            node.callee.type !== 'MemberExpression' ||
            memberPropertyName(node.callee, resolvingVariables, localValues) !== 'at' ||
            node.arguments.length !== 1 ||
            node.arguments[0].type === 'SpreadElement'
          ) {
            return IMMUTABLE_PROPERTY_MISSING
          }

          const receiver = unwrapStaticExpression(node.callee.object)
          if (!resolverContainerExpressionIsReadOnly(receiver, resolvingVariables)) {
            return IMMUTABLE_PROPERTY_AMBIGUOUS
          }

          const elements = constArrayElements(
            receiver,
            resolvingVariables,
            localValues,
          )
          const requestedIndex = staticNumberValue(
            node.arguments[0],
            resolvingVariables,
            localValues,
          )
          if (!elements || requestedIndex === null) return IMMUTABLE_PROPERTY_AMBIGUOUS

          const index = requestedIndex < 0 ? elements.length + requestedIndex : requestedIndex
          return index >= 0 && index < elements.length
            ? resolvedImmutableProperty(elements[index])
            : IMMUTABLE_PROPERTY_MISSING
        }

        function staticImmutablePath(
          node,
          path,
          resolvingVariables,
          localValues,
        ) {
          let current = resolvedImmutableProperty(node)
          for (const segment of path) {
            if (current.status !== 'resolved') return current
            const property = typeof segment === 'object' ? segment.property : segment
            current = staticImmutableProperty(
              current.value,
              property,
              resolvingVariables,
              localValues,
            )
            if (
              segment?.fallback &&
              (current.status === 'missing' ||
                (current.status === 'resolved' && isStaticallyUndefined(current.value)))
            ) {
              current = resolvedImmutableProperty(segment.fallback)
            }
          }
          return current
        }

        // Resolve one property from a statically immutable container. The
        // tagged result keeps a proven absence distinct from an access whose
        // value could change at runtime; private-token callers fail closed only
        // for the latter. One recursive path handles direct member chains,
        // destructured aliases, arrays, and object/array spreads.
        function staticImmutableProperty(
          node,
          propertyName,
          resolvingVariables = new Set(),
          localValues = new Map(),
        ) {
          node = unwrapStaticExpression(node)
          if (node.type === 'ObjectExpression') {
            let result = IMMUTABLE_PROPERTY_MISSING
            for (const property of node.properties) {
              if (property.type === 'SpreadElement') {
                const spread = staticImmutableProperty(
                  property.argument,
                  propertyName,
                  resolvingVariables,
                  localValues,
                )
                if (spread.status !== 'missing') result = spread
                continue
              }

              const key = property.computed
                ? stringConstructionValue(property.key, resolvingVariables, localValues)
                : memberPropertyName({ computed: false, property: property.key })
              if (key === DYNAMIC_VALUE_MARKER || key === null) {
                result = IMMUTABLE_PROPERTY_AMBIGUOUS
                continue
              }
              if (key !== propertyName) continue
              result = property.kind === 'init' && !property.method
                ? resolvedImmutableProperty(property.value)
                : IMMUTABLE_PROPERTY_AMBIGUOUS
            }
            return result
          }

          if (node.type === 'ArrayExpression') {
            const arrayIndex = canonicalArrayIndex(propertyName)
            if (arrayIndex === null) return IMMUTABLE_PROPERTY_MISSING
            const elements = constArrayElements(
              node,
              resolvingVariables,
              localValues,
            )
            if (!elements) return IMMUTABLE_PROPERTY_AMBIGUOUS
            return elements[arrayIndex]
              ? resolvedImmutableProperty(elements[arrayIndex])
              : IMMUTABLE_PROPERTY_MISSING
          }

          if (node.type === 'MemberExpression') {
            const memberName = memberPropertyName(node, resolvingVariables, localValues)
            if (memberName === null) return IMMUTABLE_PROPERTY_AMBIGUOUS
            const member = staticImmutableProperty(
              node.object,
              memberName,
              resolvingVariables,
              localValues,
            )
            return member.status === 'resolved'
              ? staticImmutableProperty(
                  member.value,
                  propertyName,
                  resolvingVariables,
                  localValues,
                )
              : member
          }

          if (node.type === 'Identifier') {
            const binding = constInitializer(node)
            const destructured = binding ? null : constDestructuredBinding(node)
            const variable = binding?.variable ?? destructured?.variable
            if (!variable || resolvingVariables.has(variable)) {
              return IMMUTABLE_PROPERTY_AMBIGUOUS
            }
            if (!bindingReferencesAreReadOnly(variable)) {
              return IMMUTABLE_PROPERTY_AMBIGUOUS
            }

            resolvingVariables.add(variable)
            const value = binding
              ? resolvedImmutableProperty(binding.initializer)
              : staticImmutablePath(
                  destructured.initializer,
                  destructured.path,
                  resolvingVariables,
                  localValues,
                )
            const property = value.status === 'resolved'
              ? staticImmutableProperty(
                  value.value,
                  propertyName,
                  resolvingVariables,
                  localValues,
                )
              : value
            resolvingVariables.delete(variable)
            return property
          }

          return node.type === 'Literal'
            ? IMMUTABLE_PROPERTY_MISSING
            : IMMUTABLE_PROPERTY_AMBIGUOUS
        }

        function staticCallback(node) {
          node = unwrapStaticExpression(node)
          if (node.type !== 'ArrowFunctionExpression' && node.type !== 'FunctionExpression') {
            return null
          }
          if (node.params.length > 1 || node.params.some((parameter) => parameter.type !== 'Identifier')) {
            return null
          }
          if (node.body.type !== 'BlockStatement') {
            return { body: node.body, parameter: node.params[0]?.name }
          }
          if (
            node.body.body.length !== 1 ||
            node.body.body[0].type !== 'ReturnStatement' ||
            !node.body.body[0].argument
          ) {
            return null
          }
          return { body: node.body.body[0].argument, parameter: node.params[0]?.name }
        }

        function staticNumberValue(node, resolvingVariables, localValues) {
          node = unwrapStaticExpression(node)
          if (node.type === 'Literal' && typeof node.value === 'number' && Number.isInteger(node.value)) {
            return node.value
          }
          if (node.type === 'UnaryExpression' && (node.operator === '+' || node.operator === '-')) {
            const operand = staticNumberValue(node.argument, resolvingVariables, localValues)
            return operand === null ? null : node.operator === '-' ? -operand : operand
          }
          if (node.type !== 'Identifier' || localValues.has(node.name)) return null

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return null

          resolvingVariables.add(binding.variable)
          const value = staticNumberValue(binding.initializer, resolvingVariables, localValues)
          resolvingVariables.delete(binding.variable)
          return value
        }

        function staticBooleanValue(node, resolvingVariables, localValues) {
          node = unwrapStaticExpression(node)
          if (node.type === 'Literal' && typeof node.value === 'boolean') return node.value
          if (node.type === 'UnaryExpression' && node.operator === '!') {
            const value = staticBooleanValue(node.argument, resolvingVariables, localValues)
            return value === null ? null : !value
          }
          if (node.type === 'LogicalExpression') {
            const left = staticBooleanValue(node.left, resolvingVariables, localValues)
            const right = staticBooleanValue(node.right, resolvingVariables, localValues)
            if (left === null || right === null) return null
            return node.operator === '&&' ? left && right : left || right
          }
          if (
            node.type === 'BinaryExpression' &&
            ['===', '!==', '==', '!='].includes(node.operator)
          ) {
            const left = stringConstructionValue(node.left, resolvingVariables, localValues)
            const right = stringConstructionValue(node.right, resolvingVariables, localValues)
            if (left === DYNAMIC_VALUE_MARKER || right === DYNAMIC_VALUE_MARKER) return null
            const equal = left === right
            return node.operator === '===' || node.operator === '==' ? equal : !equal
          }
          return null
        }

        function staticMappedElements(elements, callbackNode, resolvingVariables, localValues) {
          const callback = staticCallback(callbackNode)
          if (!callback?.parameter) return null

          const mapped = []
          for (const element of elements) {
            const elementValue = stringConstructionValue(element, resolvingVariables, localValues)
            if (elementValue === DYNAMIC_VALUE_MARKER) return null
            const callbackValues = new Map(localValues)
            callbackValues.set(callback.parameter, elementValue)
            const value = stringConstructionValue(callback.body, resolvingVariables, callbackValues)
            if (value === DYNAMIC_VALUE_MARKER) return null
            mapped.push(syntheticStringLiteral(value))
          }
          return mapped
        }

        function staticFilteredElements(elements, callbackNode, resolvingVariables, localValues) {
          const callback = staticCallback(callbackNode)
          if (!callback) return null

          const filtered = []
          for (const element of elements) {
            const elementValue = stringConstructionValue(element, resolvingVariables, localValues)
            if (elementValue === DYNAMIC_VALUE_MARKER) return null
            const callbackValues = new Map(localValues)
            if (callback.parameter) callbackValues.set(callback.parameter, elementValue)
            const keep = staticBooleanValue(callback.body, resolvingVariables, callbackValues)
            if (keep === null) return null
            if (keep) filtered.push(element)
          }
          return filtered
        }

        function staticReducer(node) {
          node = unwrapStaticExpression(node)
          if (node.type !== 'ArrowFunctionExpression' && node.type !== 'FunctionExpression') {
            return null
          }
          if (
            node.params.length !== 2 ||
            node.params.some((parameter) => parameter.type !== 'Identifier')
          ) {
            return null
          }
          if (node.body.type !== 'BlockStatement') {
            return {
              accumulator: node.params[0].name,
              body: node.body,
              element: node.params[1].name,
            }
          }
          if (
            node.body.body.length !== 1 ||
            node.body.body[0].type !== 'ReturnStatement' ||
            !node.body.body[0].argument
          ) {
            return null
          }
          return {
            accumulator: node.params[0].name,
            body: node.body.body[0].argument,
            element: node.params[1].name,
          }
        }

        function staticReducedString(
          elements,
          callbackNode,
          initialValueNode,
          resolvingVariables,
          localValues,
        ) {
          const reducer = staticReducer(callbackNode)
          if (!reducer) return null

          let index = 0
          let accumulator
          if (initialValueNode) {
            accumulator = stringConstructionValue(
              initialValueNode,
              resolvingVariables,
              localValues,
            )
            if (accumulator === DYNAMIC_VALUE_MARKER) return null
          } else {
            const first = elements[0]
            if (!first) return null
            accumulator = stringConstructionValue(first, resolvingVariables, localValues)
            if (accumulator === DYNAMIC_VALUE_MARKER) return null
            index = 1
          }

          for (; index < elements.length; index += 1) {
            const element = stringConstructionValue(elements[index], resolvingVariables, localValues)
            if (element === DYNAMIC_VALUE_MARKER) return null
            const callbackValues = new Map(localValues)
            callbackValues.set(reducer.accumulator, accumulator)
            callbackValues.set(reducer.element, element)
            accumulator = stringConstructionValue(reducer.body, resolvingVariables, callbackValues)
            if (accumulator === DYNAMIC_VALUE_MARKER) return null
          }

          return accumulator
        }

        function constArrayElements(node, resolvingVariables, localValues = new Map()) {
          node = unwrapStaticExpression(node)
          if (node.type === 'ArrayExpression') {
            const elements = []
            for (const element of node.elements) {
              if (!element) {
                elements.push(syntheticStringLiteral(''))
                continue
              }
              if (element.type !== 'SpreadElement') {
                elements.push(element)
                continue
              }
              const spreadElements = constArrayElements(
                element.argument,
                resolvingVariables,
                localValues,
              )
              if (!spreadElements) return null
              elements.push(...spreadElements)
            }
            return elements
          }
          if (node.type === 'MemberExpression') {
            const property = memberPropertyName(node)
            if (property === null) return null
            const propertyValue = staticImmutableProperty(
              node.object,
              property,
              resolvingVariables,
              localValues,
            )
            return propertyValue.status === 'resolved'
              ? constArrayElements(propertyValue.value, resolvingVariables, localValues)
              : null
          }
          if (
            node.type === 'CallExpression' &&
            node.callee.type === 'MemberExpression' &&
            memberPropertyName(node.callee) === 'concat'
          ) {
            const receiverElements = constArrayElements(
              node.callee.object,
              resolvingVariables,
              localValues,
            )
            if (!receiverElements) return null

            const elements = [...receiverElements]
            for (const argument of node.arguments) {
              if (argument.type === 'SpreadElement') {
                const spreadElements = constArrayElements(
                  argument.argument,
                  resolvingVariables,
                  localValues,
                )
                if (!spreadElements) return null
                elements.push(...spreadElements)
                continue
              }
              const argumentElements = constArrayElements(argument, resolvingVariables, localValues)
              if (argumentElements) {
                elements.push(...argumentElements)
              } else {
                elements.push(argument)
              }
            }
            return elements
          }
          if (
            node.type === 'CallExpression' &&
            node.callee.type === 'MemberExpression' &&
            node.callee.object.type === 'Identifier' &&
            node.callee.object.name === 'Array' &&
            memberPropertyName(node.callee) === 'from' &&
            node.arguments.length >= 1 &&
            node.arguments.length <= 2
          ) {
            const sourceElements = constArrayElements(node.arguments[0], resolvingVariables, localValues)
            if (!sourceElements) return null
            if (node.arguments.length === 1) return sourceElements
            return staticMappedElements(
              sourceElements,
              node.arguments[1],
              resolvingVariables,
              localValues,
            )
          }
          if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression') {
            const property = memberPropertyName(node.callee)
            const receiverElements = constArrayElements(
              node.callee.object,
              resolvingVariables,
              localValues,
            )
            if (!receiverElements) return null

            if (property === 'slice' && node.arguments.length === 0) return receiverElements
            if (property === 'reverse' && node.arguments.length === 0) {
              return [...receiverElements].reverse()
            }
            if (property === 'map' && node.arguments.length === 1) {
              return staticMappedElements(
                receiverElements,
                node.arguments[0],
                resolvingVariables,
                localValues,
              )
            }
            if (property === 'filter' && node.arguments.length === 1) {
              return staticFilteredElements(
                receiverElements,
                node.arguments[0],
                resolvingVariables,
                localValues,
              )
            }
            return null
          }
          if (node.type !== 'Identifier' || localValues.has(node.name)) return null

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return null

          resolvingVariables.add(binding.variable)
          const elements = constArrayElements(binding.initializer, resolvingVariables, localValues)
          resolvingVariables.delete(binding.variable)
          return elements
        }

        function constRegExpValue(node, resolvingVariables, localValues = new Map()) {
          node = unwrapStaticExpression(node)
          if (
            node.type === 'Literal' &&
            node.regex
          ) {
            try {
              return new RegExp(node.regex.pattern, node.regex.flags)
            } catch {
              return null
            }
          }
          if (
            node.type === 'NewExpression' &&
            node.callee.type === 'Identifier' &&
            node.callee.name === 'RegExp' &&
            node.arguments.length >= 1 &&
            node.arguments.length <= 2 &&
            node.arguments.every((argument) => argument.type !== 'SpreadElement')
          ) {
            const source = stringConstructionValue(node.arguments[0], resolvingVariables, localValues)
            const flags = node.arguments[1]
              ? stringConstructionValue(node.arguments[1], resolvingVariables, localValues)
              : ''
            if (source === DYNAMIC_VALUE_MARKER || flags === DYNAMIC_VALUE_MARKER) return null
            try {
              return new RegExp(source, flags)
            } catch {
              return null
            }
          }
          if (node.type !== 'Identifier') return null

          const binding = constInitializer(node)
          if (!binding || resolvingVariables.has(binding.variable)) return null

          resolvingVariables.add(binding.variable)
          const value = constRegExpValue(binding.initializer, resolvingVariables, localValues)
          resolvingVariables.delete(binding.variable)
          return value
        }

        function staticStringFactoryValue(
          node,
          resolvingVariables = new Set(),
          localValues = new Map(),
        ) {
          node = unwrapStaticExpression(node)
          if (node.type !== 'CallExpression') return null

          const factory = isGlobalMemberReference(node.callee, 'String', 'fromCharCode')
            ? String.fromCharCode
            : isGlobalMemberReference(node.callee, 'String', 'fromCodePoint')
              ? String.fromCodePoint
              : null
          if (!factory) return null
          if (node.arguments.some((argument) => argument.type === 'SpreadElement')) {
            return DYNAMIC_VALUE_MARKER
          }

          const codeUnits = node.arguments.map((argument) =>
            staticNumberValue(argument, resolvingVariables, localValues),
          )
          if (codeUnits.some((codeUnit) => codeUnit === null)) return DYNAMIC_VALUE_MARKER
          try {
            return factory(...codeUnits)
          } catch {
            return DYNAMIC_VALUE_MARKER
          }
        }

        function staticConstructionFragments(node, resolvingVariables = new Set()) {
          node = unwrapStaticExpression(node)
          if (node.type === 'Literal') return typeof node.value === 'string' ? [node.value] : []
          if (node.type === 'Identifier') {
            const binding = constInitializer(node)
            if (!binding || resolvingVariables.has(binding.variable)) return []
            resolvingVariables.add(binding.variable)
            const fragments = staticConstructionFragments(binding.initializer, resolvingVariables)
            resolvingVariables.delete(binding.variable)
            return fragments
          }
          if (node.type === 'ArrayExpression') {
            return node.elements.flatMap((element) =>
              element
                ? staticConstructionFragments(
                    element.type === 'SpreadElement' ? element.argument : element,
                    resolvingVariables,
                  )
                : [],
            )
          }
          if (node.type === 'TemplateLiteral') {
            return node.quasis.flatMap((quasi, index) => [
              quasi.value.cooked ?? quasi.value.raw,
              ...(index < node.expressions.length
                ? staticConstructionFragments(node.expressions[index], resolvingVariables)
                : []),
            ])
          }
          if (node.type === 'BinaryExpression' && node.operator === '+') {
            return [
              ...staticConstructionFragments(node.left, resolvingVariables),
              ...staticConstructionFragments(node.right, resolvingVariables),
            ]
          }
          const factoryValue = staticStringFactoryValue(node, resolvingVariables)
          if (factoryValue !== null && factoryValue !== DYNAMIC_VALUE_MARKER) {
            return [factoryValue]
          }
          if (node.type === 'CallExpression' || node.type === 'NewExpression') {
            const receiver =
              node.type === 'CallExpression' && node.callee.type === 'MemberExpression'
                ? staticConstructionFragments(node.callee.object, resolvingVariables)
                : []
            return [
              ...receiver,
              ...node.arguments.flatMap((argument) =>
                staticConstructionFragments(
                  argument.type === 'SpreadElement' ? argument.argument : argument,
                  resolvingVariables,
                ),
              ),
            ]
          }
          return []
        }

        function containsPrivateIdentityFragment(value) {
          return /--(?:color-)?category-(?:[1-9]|1[0-2])(?!\d)|--(?:color-)?category-(?=$|[^0-9])/.test(
            value,
          )
        }

        function failClosedStructuralValue(node, resolvingVariables) {
          const staticText = staticConstructionFragments(node, resolvingVariables).join('')
          const containsCssVariableConstruction = /\bvar\s*\(/i.test(staticText)
          const containsTailwindConstruction = TAILWIND_COLOR_UTILITY_SPELLINGS.some((utility) =>
            staticText.includes(`${utility}-(`),
          )
          const containsPrivateFragment = containsPrivateIdentityFragment(staticText)
          return containsCssVariableConstruction ||
            containsTailwindConstruction ||
            containsPrivateFragment
            ? `var(${DYNAMIC_VALUE_MARKER})`
            : DYNAMIC_VALUE_MARKER
        }

        function failClosedAmbiguousResolverValue(node, resolvingVariables) {
          const staticText = staticConstructionFragments(node, resolvingVariables).join('')
          return containsPrivateIdentityFragment(staticText)
            ? `var(${DYNAMIC_VALUE_MARKER})`
            : DYNAMIC_VALUE_MARKER
        }

        function staticResolverStringValue(
          node,
          resolvingVariables = new Set(),
          localValues = new Map(),
        ) {
          const invocation = staticResolverInvocation(node, resolvingVariables)
          if (!invocation || !invocation.receiver) return null

          if (invocation.kind === AMBIGUOUS_RESOLVER_TARGET) {
            return failClosedAmbiguousResolverValue(node, resolvingVariables)
          }

          if (invocation.kind === 'array-join') {
            if (invocation.arguments.length > 1) return DYNAMIC_VALUE_MARKER
            const array = constArrayElements(invocation.receiver, resolvingVariables, localValues)
            if (!array) return failClosedStructuralValue(invocation.receiver, resolvingVariables)
            const separator = invocation.arguments[0]
              ? stringConstructionValue(invocation.arguments[0], resolvingVariables, localValues)
              : ','
            if (separator === DYNAMIC_VALUE_MARKER) return failClosedStructuralValue(node, resolvingVariables)
            const values = array.map((element) =>
              stringConstructionValue(element, resolvingVariables, localValues),
            )
            return values.includes(DYNAMIC_VALUE_MARKER)
              ? failClosedStructuralValue(node, resolvingVariables)
              : values.join(separator)
          }

          if (invocation.kind === 'string-concat') {
            const values = [invocation.receiver, ...invocation.arguments].map((part) =>
              stringConstructionValue(part, resolvingVariables, localValues),
            )
            return values.includes(DYNAMIC_VALUE_MARKER)
              ? failClosedStructuralValue(node, resolvingVariables)
              : values.join('')
          }

          if (invocation.kind === 'array-reduce') {
            if (invocation.arguments.length < 1 || invocation.arguments.length > 2) {
              return DYNAMIC_VALUE_MARKER
            }
            const array = constArrayElements(invocation.receiver, resolvingVariables, localValues)
            if (!array) return failClosedStructuralValue(invocation.receiver, resolvingVariables)
            return (
              staticReducedString(
                array,
                invocation.arguments[0],
                invocation.arguments[1],
                resolvingVariables,
                localValues,
              ) ?? failClosedStructuralValue(node, resolvingVariables)
            )
          }

          if (invocation.kind === 'string-replace' || invocation.kind === 'string-replace-all') {
            if (invocation.arguments.length !== 2) return DYNAMIC_VALUE_MARKER
            const [search, replacement] = invocation.arguments
            const regExpSearch = constRegExpValue(search, resolvingVariables, localValues)
            if (invocation.kind === 'string-replace-all' && regExpSearch && !regExpSearch.global) {
              return DYNAMIC_VALUE_MARKER
            }

            try {
              const subject = stringConstructionValue(
                invocation.receiver,
                resolvingVariables,
                localValues,
              )
              const replacementValue = stringConstructionValue(
                replacement,
                resolvingVariables,
                localValues,
              )
              const searchValue = regExpSearch ?? stringConstructionValue(
                search,
                resolvingVariables,
                localValues,
              )
              if (
                subject === DYNAMIC_VALUE_MARKER ||
                replacementValue === DYNAMIC_VALUE_MARKER ||
                searchValue === DYNAMIC_VALUE_MARKER
              ) {
                return failClosedStructuralValue(node, resolvingVariables)
              }
              return invocation.kind === 'string-replace'
                ? subject.replace(searchValue, replacementValue)
                : subject.replaceAll(searchValue, replacementValue)
            } catch {
              return DYNAMIC_VALUE_MARKER
            }
          }

          return null
        }

        function stringConstructionValue(
          node,
          resolvingVariables = new Set(),
          localValues = new Map(),
        ) {
          node = unwrapStaticExpression(node)
          // These wrappers are erased before runtime string construction, so they
          // must not hide a statically proven alias from the structural grammar
          // detector. Every other expression remains ambiguous and fail-closed.
          if (node.type === 'Literal') {
            return typeof node.value === 'string' ? node.value : DYNAMIC_VALUE_MARKER
          }
          if (node.type === 'Identifier') {
            if (localValues.has(node.name)) return localValues.get(node.name)
            const binding = constInitializer(node)
            if (!binding || resolvingVariables.has(binding.variable)) {
              return DYNAMIC_VALUE_MARKER
            }

            resolvingVariables.add(binding.variable)
            const value = stringConstructionValue(binding.initializer, resolvingVariables, localValues)
            resolvingVariables.delete(binding.variable)
            return value
          }
          if (node.type === 'MemberExpression') {
            const property = memberPropertyName(node)
            if (property === null) return DYNAMIC_VALUE_MARKER
            const propertyValue = staticImmutableProperty(
              node.object,
              property,
              resolvingVariables,
              localValues,
            )
            return propertyValue.status === 'resolved'
              ? stringConstructionValue(propertyValue.value, resolvingVariables, localValues)
              : DYNAMIC_VALUE_MARKER
          }
          if (node.type === 'TemplateLiteral') {
            return node.quasis.reduce(
              (value, quasi, index) =>
                value +
                (quasi.value.cooked ?? quasi.value.raw) +
                (index < node.expressions.length
                  ? stringConstructionValue(node.expressions[index], resolvingVariables, localValues)
                  : ''),
              '',
            )
          }
          if (node.type === 'BinaryExpression' && node.operator === '+') {
            return (
              stringConstructionValue(node.left, resolvingVariables, localValues) +
              stringConstructionValue(node.right, resolvingVariables, localValues)
            )
          }
          const staticStringFactory = staticStringFactoryValue(
            node,
            resolvingVariables,
            localValues,
          )
          if (staticStringFactory !== null) return staticStringFactory
          const indirectResolverValue = staticResolverStringValue(
            node,
            resolvingVariables,
            localValues,
          )
          if (indirectResolverValue !== null) return indirectResolverValue
          if (isPrototypeMethodCall(node, 'Array', 'join')) {
            if (
              node.arguments.length < 1 ||
              node.arguments.length > 2 ||
              node.arguments.some((argument) => argument.type === 'SpreadElement')
            ) {
              return DYNAMIC_VALUE_MARKER
            }
            const array = constArrayElements(node.arguments[0], resolvingVariables, localValues)
            if (!array) return failClosedStructuralValue(node.arguments[0], resolvingVariables)
            const separator = node.arguments[1]
              ? stringConstructionValue(node.arguments[1], resolvingVariables, localValues)
              : ','
            if (separator === DYNAMIC_VALUE_MARKER) return failClosedStructuralValue(node, resolvingVariables)
            const values = array.map((element) =>
              stringConstructionValue(element, resolvingVariables, localValues),
            )
            return values.includes(DYNAMIC_VALUE_MARKER)
              ? failClosedStructuralValue(node, resolvingVariables)
              : values.join(separator)
          }
          if (isPrototypeMethodCall(node, 'String', 'concat')) {
            if (
              node.arguments.length < 1 ||
              node.arguments.some((argument) => argument.type === 'SpreadElement')
            ) {
              return DYNAMIC_VALUE_MARKER
            }
            const values = node.arguments.map((argument) =>
              stringConstructionValue(argument, resolvingVariables, localValues),
            )
            return values.includes(DYNAMIC_VALUE_MARKER)
              ? failClosedStructuralValue(node, resolvingVariables)
              : values.join('')
          }
          if (isPrototypeMethodCall(node, 'Array', 'reduce')) {
            if (
              node.arguments.length < 2 ||
              node.arguments.length > 3 ||
              node.arguments.some((argument) => argument.type === 'SpreadElement')
            ) {
              return DYNAMIC_VALUE_MARKER
            }
            const array = constArrayElements(node.arguments[0], resolvingVariables, localValues)
            if (!array) return failClosedStructuralValue(node.arguments[0], resolvingVariables)
            return (
              staticReducedString(
                array,
                node.arguments[1],
                node.arguments[2],
                resolvingVariables,
                localValues,
              ) ?? failClosedStructuralValue(node, resolvingVariables)
            )
          }
          if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression') {
            const property = memberPropertyName(node.callee)

            if (property === 'join') {
              if (node.arguments.length > 1) return DYNAMIC_VALUE_MARKER

              const array = constArrayElements(node.callee.object, resolvingVariables, localValues)
              if (!array) return failClosedStructuralValue(node.callee.object, resolvingVariables)

              const separator = node.arguments[0]
                ? stringConstructionValue(node.arguments[0], resolvingVariables, localValues)
                : ','
              if (separator === DYNAMIC_VALUE_MARKER) return failClosedStructuralValue(node, resolvingVariables)
              const values = array.map((element) =>
                stringConstructionValue(element, resolvingVariables, localValues),
              )
              return values.includes(DYNAMIC_VALUE_MARKER)
                ? failClosedStructuralValue(node, resolvingVariables)
                : values.join(separator)
            }

            if (property === 'concat') {
              const values = [node.callee.object, ...node.arguments]
                .map((part) =>
                  part.type === 'SpreadElement'
                    ? DYNAMIC_VALUE_MARKER
                    : stringConstructionValue(part, resolvingVariables, localValues),
                )
              return values.includes(DYNAMIC_VALUE_MARKER)
                ? failClosedStructuralValue(node, resolvingVariables)
                : values.join('')
            }

            if (property === 'reduce') {
              if (
                node.arguments.length < 1 ||
                node.arguments.length > 2 ||
                node.arguments.some((argument) => argument.type === 'SpreadElement')
              ) {
                return DYNAMIC_VALUE_MARKER
              }
              const array = constArrayElements(node.callee.object, resolvingVariables, localValues)
              if (!array) return failClosedStructuralValue(node.callee.object, resolvingVariables)
              return (
                staticReducedString(
                  array,
                  node.arguments[0],
                  node.arguments[1],
                  resolvingVariables,
                  localValues,
                ) ?? failClosedStructuralValue(node, resolvingVariables)
              )
            }

            if (property === 'at' && node.arguments.length === 1) {
              const array = constArrayElements(node.callee.object, resolvingVariables, localValues)
              const index = staticNumberValue(node.arguments[0], resolvingVariables, localValues)
              if (!array || index === null) return DYNAMIC_VALUE_MARKER
              const normalizedIndex = index < 0 ? array.length + index : index
              const element = array[normalizedIndex]
              return element
                ? stringConstructionValue(element, resolvingVariables, localValues)
                : DYNAMIC_VALUE_MARKER
            }

            if ((property === 'replace' || property === 'replaceAll') && node.arguments.length === 2) {
              const [search, replacement] = node.arguments
              if (search.type === 'SpreadElement' || replacement.type === 'SpreadElement') {
                return DYNAMIC_VALUE_MARKER
              }

              const regExpSearch = constRegExpValue(search, resolvingVariables, localValues)
              if (property === 'replaceAll' && regExpSearch && !regExpSearch.global) {
                return DYNAMIC_VALUE_MARKER
              }

              try {
                const subject = stringConstructionValue(
                  node.callee.object,
                  resolvingVariables,
                  localValues,
                )
                const replacementValue = stringConstructionValue(
                  replacement,
                  resolvingVariables,
                  localValues,
                )
                const searchValue = regExpSearch ?? stringConstructionValue(
                  search,
                  resolvingVariables,
                  localValues,
                )
                if (
                  subject === DYNAMIC_VALUE_MARKER ||
                  replacementValue === DYNAMIC_VALUE_MARKER ||
                  searchValue === DYNAMIC_VALUE_MARKER
                ) {
                  return DYNAMIC_VALUE_MARKER
                }
                return subject[property](
                  searchValue,
                  replacementValue,
                )
              } catch {
                return DYNAMIC_VALUE_MARKER
              }
            }
          }
          return DYNAMIC_VALUE_MARKER
        }

        function isNestedStringConstruction(node) {
          const parent = node.parent
          const enclosingCall =
            parent?.type === 'CallExpression'
              ? parent
              : parent?.type === 'MemberExpression' &&
                  parent.object === node &&
                  parent.parent?.type === 'CallExpression' &&
                  parent.parent.callee === parent
                ? parent.parent
                : null
          const isResolverReceiver =
            enclosingCall?.callee.type === 'MemberExpression' && enclosingCall.callee.object === node
          return (
            (parent?.type === 'BinaryExpression' && parent.operator === '+') ||
            parent?.type === 'TemplateLiteral' ||
            (isResolverReceiver &&
              ['at', 'concat', 'join', 'reduce', 'replace', 'replaceAll'].includes(
                memberPropertyName(enclosingCall?.callee),
              ))
          )
        }

        function isCssomStyleReceiver(node) {
          return resolvesImmutableAlias(node, (candidate) => {
            if (
              candidate.type === 'CallExpression' &&
              isGetComputedStyleReference(candidate.callee)
            ) {
              return true
            }
            return candidate.type === 'MemberExpression' && memberPropertyName(candidate) === 'style'
          })
        }

        function isTypedOmReceiver(node) {
          return resolvesImmutableAlias(
            node,
            (candidate) =>
              candidate.type === 'CallExpression' &&
              resolvesImmutableAlias(
                candidate.callee,
                (callee) =>
                  callee.type === 'MemberExpression' &&
                  memberPropertyName(callee) === 'computedStyleMap',
              ),
          )
        }

        function prototypePropertyReadKind(node) {
          const isPrototypeMethod = (constructorName, methodName) =>
            resolvesImmutableAlias(
              node,
              (candidate) =>
                candidate.type === 'MemberExpression' &&
                memberPropertyName(candidate) === methodName &&
                candidate.object.type === 'MemberExpression' &&
                memberPropertyName(candidate.object) === 'prototype' &&
                resolvesImmutableAlias(
                  candidate.object.object,
                  (constructor) => isUnshadowedGlobalIdentifier(constructor, constructorName),
                ),
            )

          if (isPrototypeMethod('CSSStyleDeclaration', 'getPropertyValue')) return 'cssom'
          if (isPrototypeMethod('StylePropertyMapReadOnly', 'get')) return 'typed-om'
          return null
        }

        function prototypePropertyReadArgumentValue(kind, receiver, propertyNode) {
          const receiverIsKnown =
            kind === 'cssom' ? isCssomStyleReceiver(receiver) : isTypedOmReceiver(receiver)
          if (!receiverIsKnown) return null
          if (!propertyNode || propertyNode.type === 'SpreadElement') {
            return DYNAMIC_VALUE_MARKER
          }
          return stringConstructionValue(propertyNode)
        }

        function prototypePropertyReadValue(node) {
          const staticInvocation = staticResolverInvocation(node)
          if (
            staticInvocation &&
            staticInvocation.receiver &&
            (staticInvocation.kind === 'cssom' || staticInvocation.kind === 'typed-om')
          ) {
            return prototypePropertyReadArgumentValue(
              staticInvocation.kind,
              staticInvocation.receiver,
              staticInvocation.arguments[0],
            )
          }

          if (
            node.callee.type === 'MemberExpression' &&
            memberPropertyName(node.callee) === 'call'
          ) {
            const kind = prototypePropertyReadKind(node.callee.object)
            if (!kind || node.arguments.length < 2) return null
            return prototypePropertyReadArgumentValue(kind, node.arguments[0], node.arguments[1])
          }

          if (!isGlobalMemberReference(node.callee, 'Reflect', 'apply') || node.arguments.length !== 3) {
            return null
          }

          const kind = prototypePropertyReadKind(node.arguments[0])
          if (!kind || node.arguments[1].type === 'SpreadElement' || node.arguments[2].type === 'SpreadElement') {
            return null
          }

          const argumentsList = constArrayElements(node.arguments[2], new Set())
          if (!argumentsList) {
            return prototypePropertyReadArgumentValue(kind, node.arguments[1], null)
          }
          return prototypePropertyReadArgumentValue(kind, node.arguments[1], argumentsList[0])
        }

        function cssomPropertyReadValue(node) {
          const prototypeProperty = prototypePropertyReadValue(node)
          if (prototypeProperty !== null) return prototypeProperty

          if (
            node.callee.type !== 'MemberExpression' ||
            node.arguments.length !== 1 ||
            node.arguments[0].type === 'SpreadElement'
          ) {
            return null
          }

          const property = memberPropertyName(node.callee)
          const argumentValue = stringConstructionValue(node.arguments[0])
          const cssomReceiver = isCssomStyleReceiver(node.callee.object)
          const typedOmReceiver = isTypedOmReceiver(node.callee.object)
          if (
            (property === 'getPropertyValue' && cssomReceiver) ||
            (property === 'get' && typedOmReceiver)
          ) {
            return argumentValue
          }

          // A dynamic method on a known CSSOM receiver can still be a private
          // token read. Reject only a statically proven private argument at
          // that boundary; dynamic arguments remain unreported so unrelated
          // runtime dispatch does not turn into a broad false positive.
          if (
            property === null &&
            (cssomReceiver || typedOmReceiver) &&
            argumentValue !== DYNAMIC_VALUE_MARKER
          ) {
            return argumentValue
          }

          return null
        }

        function reportPrivateIdentityReferences(value, node) {
          for (const reference of findPrivateIdentityReferences(value)) {
            context.report({
              data: { property: reference.property },
              messageId: 'privateIdentity',
              node,
            })
          }
        }

        return {
          Literal(node) {
            if (typeof node.value === 'string' && !isNestedStringConstruction(node)) {
              reportPrivateIdentityReferences(stringConstructionValue(node), node)
            }
          },
          TemplateLiteral(node) {
            if (!isNestedStringConstruction(node)) {
              reportPrivateIdentityReferences(stringConstructionValue(node), node)
            }
          },
          BinaryExpression(node) {
            if (node.operator === '+' && !isNestedStringConstruction(node)) {
              reportPrivateIdentityReferences(stringConstructionValue(node), node)
            }
          },
          CallExpression(node) {
            const cssomProperty = cssomPropertyReadValue(node)
            if (cssomProperty !== null) {
              // A CSSOM property lookup is semantically equivalent to reading
              // var(--token): the token name must observe the same private
              // ButlerMark boundary even though it is not written in CSS.
              reportPrivateIdentityReferences(`var(${cssomProperty})`, node)
              return
            }
            if (!isNestedStringConstruction(node)) {
              reportPrivateIdentityReferences(stringConstructionValue(node), node)
            }
          },
        }
      },
    },
  },
}

const VISUAL_ROLE_SELECTORS = [
  {
    selector:
      'ImportDeclaration[source.value="@/components/ui/ButlerMark"] ImportSpecifier[imported.name=/^(?:butlerHueVar|categoryHueVar)$/]',
    message:
      'Butler identity resolution is private to ButlerMark. Import a typed helper from ' +
      '@/lib/visual-token-roles instead.',
  },
  {
    selector: 'CallExpression[callee.name=/^(?:butlerHueVar|categoryHueVar)$/]',
    message:
      'Butler identity resolution is private to ButlerMark. Use a typed semantic role helper.',
  },
]

// bu-ep4ks.15: every raw <th> must declare a `scope` attribute (jsx-a11y has
// no built-in rule for this -- it only validates `scope` when present, not
// its absence). A screen reader announcing a data table with unscoped
// headers cannot associate a cell with its column/row header at all. Applied
// repo-wide via the base '**/*.tsx' block below rather than a file
// allowlist: unlike POLL_POLICY_FILES (and the now-retired
// NO_WINDOW_CONFIRM_FILES), EVERY existing
// <th> in this codebase was migrated onto `scope` in the same change that
// added this rule (5 hand-rolled data tables -- ButlerRelationshipContactsTab,
// ButlerFinanceFinancesTab, ButlerHomeDevicesTab, ButlerGeneralCollectionsTab,
// ButlerQaInvestigationsTab -- plus the two pre-existing compliant sites,
// components/ui/table.tsx's TableHead primitive and
// approvals/attention-ledger-panel.tsx's scope="row"), so there is no
// narrower starting scope to pick.
const TH_SCOPE_SELECTORS = [
  {
    selector: 'JSXOpeningElement[name.name="th"]:not(:has(JSXAttribute[name.name="scope"]))',
    message:
      'A raw <th> must declare scope="col" (or scope="row" for a row header) -- without it, ' +
      'a screen reader cannot associate the header with its column/row (bu-ep4ks.15). Prefer ' +
      'TableHead from components/ui/table.tsx (defaults to scope="col") where the shadcn ' +
      'Table primitives already fit; otherwise add scope directly.',
  },
]

// bu-ep4ks.15: the secrets passport's hand-styled form fields strip the
// native focus outline (`outline-none`) without adding any replacement focus
// indicator -- a keyboard user tabbing through the Add/Edit Secret forms
// gets zero visual feedback on which field is focused. Fixed at the four
// cited sites (GoogleAppCredentials.tsx, Spine.tsx, ProviderConfigDrawer.tsx,
// pages.tsx) onto the same `focus-visible:ring-[3px] focus-visible:ring-ring/50`
// pattern components/ui/input.tsx and textarea.tsx already establish.
//
// Scoped to NO_UNGUARDED_OUTLINE_NONE_FILES (the passport family this bead
// touches) rather than repo-wide: several other outline-none sites elsewhere
// in the app already have a DIFFERENT replacement indicator (e.g.
// ApprovalsPage.tsx's `focus:border-destructive/50`), and several are
// deliberately-unfocusable sr-only headings (tabIndex={-1} programmatic
// focus targets for a11y announcements, not real Tab-stops needing a visible
// ring) -- both are legitimate, and a blanket repo-wide rule would false-
// positive on them. Broadening this to a full repo audit is a follow-up, not
// silently expanded here (mirrors the POLL_POLICY_FILES scoping precedent).
const NO_UNGUARDED_OUTLINE_NONE_FILES = [
  'src/components/secrets/passport/pages.tsx',
  'src/components/secrets/passport/ProviderConfigDrawer.tsx',
  'src/components/secrets/passport/GoogleAppCredentials.tsx',
  'src/components/secrets/passport/Spine.tsx',
]

const NO_UNGUARDED_OUTLINE_NONE_SELECTORS = [
  {
    selector:
      'Literal[value=/^(?!.*focus-visible:ring)(?!.*focus:ring)(?=.*\\boutline-none\\b).*$/s]',
    message:
      'outline-none strips the native focus indicator with no replacement (bu-ep4ks.15) -- a ' +
      'keyboard user gets no visual feedback that this field is focused. Add ' +
      'focus-visible:ring-[3px] focus-visible:ring-ring/50 (the pattern components/ui/input.tsx ' +
      'and textarea.tsx already use), or another visible focus-visible indicator.',
  },
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    // The identity component is the one canonical owner of this private
    // surface. The lint test itself is a deliberately malicious source
    // fixture; its virtual files remain checked by this rule through ESLint's
    // lintText API, while this on-disk harness stays readable.
    files: ['**/*.{ts,tsx}'],
    ignores: ['src/components/ui/ButlerMark.tsx', 'src/lib/visual-role-eslint.test.ts'],
    plugins: {
      'visual-role': VISUAL_ROLE_GUARD_PLUGIN,
    },
    rules: {
      'visual-role/no-private-identity-token': 'error',
    },
  },
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
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
        ...KEYDOWN_LISTENER_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
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
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
        ...KEYDOWN_LISTENER_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
      ],
    },
  },
  {
    // components/ui/ and the passport composition wrapper: still theme-token
    // and hex clean, but exempt from the redeclaration ban (they ARE the
    // canonical declaration / the accepted wrapper pattern). The semantic
    // identity guard remains active here; only ButlerMark and its identity-
    // mapping fixture are exempt because they are the canonical identity home.
    files: ['src/components/ui/**/*.tsx', 'src/components/secrets/passport/atoms.tsx'],
    ignores: ['src/components/ui/ButlerMark.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...HEX_COLOR_SELECTORS,
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
        ...KEYDOWN_LISTENER_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
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
        ...KEYDOWN_LISTENER_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
      ],
    },
  },
  {
    // These are the only two homes allowed to own DOM key listeners. Their
    // full selector lists deliberately omit KEYDOWN_LISTENER_SELECTORS while
    // retaining every other matching invariant from the generic file blocks.
    files: ['src/hooks/use-keyboard-shortcuts.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...HSL_VAR_SELECTORS,
        ...STATUS_COLOR_SELECTORS,
        ...POLL_POLICY_SELECTORS,
        ...ANIMATE_PULSE_SELECTORS,
        ...FORMAT_CLONE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
      ],
    },
  },
  {
    files: ['src/hooks/use-register-shortcut.tsx'],
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
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
      ],
    },
  },
  {
    // bu-ep4ks.15: no-categorical-status-color, scoped -- see
    // NO_CATEGORICAL_STATUS_FILES comment above for why this isn't repo-wide.
    // Must repeat the general '**/*.tsx' block's full selector set (flat
    // config does not merge no-restricted-syntax across matching blocks for
    // the same file). Harmless to apply the non-ui selector set (including
    // PRIMITIVE_REDECLARATION/HANDROLLED_OVERLAY) to StateDot.tsx too -- it
    // declares neither, so those simply never match there.
    files: NO_CATEGORICAL_STATUS_FILES,
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
        ...KEYDOWN_LISTENER_SELECTORS,
        ...NO_CATEGORICAL_STATUS_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
      ],
    },
  },
  {
    // bu-ep4ks.15: no-unguarded-outline-none, scoped -- see
    // NO_UNGUARDED_OUTLINE_NONE_FILES comment above for why this isn't
    // repo-wide. Must repeat the general '**/*.tsx' block's full selector set
    // (flat config does not merge no-restricted-syntax across matching
    // blocks for the same file).
    files: NO_UNGUARDED_OUTLINE_NONE_FILES,
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
        ...KEYDOWN_LISTENER_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_UNGUARDED_OUTLINE_NONE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
      ],
    },
  },
  {
    // bu-d3z0t: status-color audit guard. It comes after every generic and
    // per-file no-restricted-syntax block because flat-config arrays replace,
    // rather than merge.
    files: BLUE_PURPLE_STATUS_AUDIT_FILES,
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
        ...KEYDOWN_LISTENER_SELECTORS,
        ...BLUE_PURPLE_STATUS_SELECTORS,
        ...TH_SCOPE_SELECTORS,
        ...NO_WINDOW_CONFIRM_SELECTORS,
        ...VISUAL_ROLE_SELECTORS,
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
