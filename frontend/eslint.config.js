import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// ---------------------------------------------------------------------------
// Dispatch one-visual-language guards, as `no-restricted-syntax` selector lists.
//
// Flat config does NOT merge `rules.no-restricted-syntax` across config objects
// that match the same file: the LAST matching object wins outright. So each
// file family below gets ONE complete list, built from the named parts here.
// Escape a single line with `eslint-disable-next-line no-restricted-syntax -- reason`.
// ---------------------------------------------------------------------------

// A string literal and a template chunk are checked with the same pattern.
const both = (pattern, message, flags = '') => [
  { selector: `Literal[value=/${pattern}/${flags}]`, message },
  { selector: `TemplateElement[value.raw=/${pattern}/${flags}]`, message },
]
const declared = (names, message) =>
  ['FunctionDeclaration', 'VariableDeclarator'].map((node) => ({
    selector: `${node}[id.name=/^(?:${names})$/]`,
    message,
  }))
const tailwind = (colors) =>
  String.raw`\b(?:bg|text|border|ring|decoration|from|via|to|fill|stroke|outline|divide|caret|accent|shadow)-(?:${colors})-(?:50|100|150|200|300|400|500|600|700|800|900|950)\b`

// Tokens are full oklch(...) literals, so hsl(var(--x)) is invalid CSS.
const HSL_VAR_SELECTORS = both(
  String.raw`hsla?\(\s*var\(`,
  'hsl(var(--x)) is invalid CSS here (tokens are oklch literals). Use var(--x), or ' +
    'chartColor()/chartColorAlpha() from src/lib/chart-colors.ts for chart series.',
  'i',
)

// The shadcn Card is retired (bu-27dxl.12): Section, or Tile for dense grids.
const RETIRED_CARD_SELECTORS = [{
  selector: 'ImportDeclaration[source.value=/ui\\/card$/i]',
  message: 'The Card primitive is retired (bu-27dxl.12). Use Section, or Tile only for an ' +
    'independently loading/degrading dense-grid module.',
}]

// A fallback literal in var(--token, ...) hides a missing token.
const TOKEN_FALLBACK_SELECTORS = both(
  String.raw`var\(\s*--[A-Za-z0-9_-]+\s*,`,
  'Literal CSS-token fallbacks are banned (bu-27dxl.12). Use the token directly, or add it ' +
    'to frontend/src/index.css.',
  'i',
)

// bu-86c4c.6: three state colors only (--red, --amber, --green). A line-level
// disable is the escape for a fixed categorical palette that lands on a shade.
const STATUS_COLOR_SELECTORS = both(
  tailwind('red|green|emerald|amber|yellow|orange'),
  'Raw Tailwind status-palette classes are banned (bu-86c4c.6): use var(--red), var(--amber) ' +
    '(var(--amber-text) for text) or var(--green). For a non-status palette that happens to ' +
    'land on this shade, add a line-level eslint-disable-next-line with a reason.',
)

// bu-d3z0t: blue/purple are not a status dialect. Applied to audited files only.
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
const BLUE_PURPLE_STATUS_SELECTORS = both(
  tailwind('sky|cyan|blue|indigo|violet|purple|fuchsia'),
  'Raw blue/purple Tailwind status classes are banned in audited files (bu-d3z0t). Use ' +
    "StateDot's state/tone registry for live state; a fixed category may keep its shade " +
    'with a line-level eslint-disable-next-line and a one-line reason.',
)

// .tsx only: a few .ts data modules hold small fixed hex palettes. Needs a letter
// so purely decimal ids like "#401" do not match.
const HEX_COLOR_SELECTORS = both(
  '#(?=[0-9a-fA-F]*[a-fA-F])(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\\b',
  'Raw hex color literals are banned in JSX files (bu-86c4c.6). Use var(--x) (see ' +
    'frontend/src/index.css). For a user-chosen color, add a line-level eslint-disable-next-line.',
)

// bu-86c4c.6: Eyebrow and Voice live in components/ui/; wrap them, never redeclare.
const PRIMITIVE_REDECLARATION_SELECTORS = declared(
  'Eyebrow|Voice',
  'Do not redeclare Eyebrow/Voice (bu-86c4c.6). Import them from "@/components/ui/Eyebrow" / ' +
    '"@/components/ui/Voice", or wrap the canonical component (see secrets/passport/atoms.tsx).',
)

// bu-qvnce.10: a full-viewport `fixed ... inset-0` scrim is a hand-rolled dialog.
const HANDROLLED_OVERLAY_MESSAGE =
  'Hand-rolled fixed-inset overlay (bu-qvnce.10). Use useModalChoreography ' +
  '(src/hooks/use-modal-choreography.tsx) or the Dialog/Sheet primitives. If it is not a ' +
  'dialog, add a line-level eslint-disable-next-line with a reason.'
const HANDROLLED_OVERLAY_SELECTORS = [
  { selector: 'Literal[value=/\\bfixed\\b[^"]*\\binset-0\\b/]', message: HANDROLLED_OVERLAY_MESSAGE },
  { selector: 'TemplateElement[value.raw=/\\bfixed\\b[^`]*\\binset-0\\b/]', message: HANDROLLED_OVERLAY_MESSAGE },
]

// Motion Vocabulary (dashboard-design-language spec): whole class tokens only.
// Template chunks are checked at known token boundaries, raw and cooked text.
const MOTION_TOKEN = String.raw`(?:transition(?:-all)?|animate-bounce|transition-(?:fast|base|slow)|transition-\[(?:[^\]\s]*[, _])?(?:width|height|max-height|margin|padding|top|left|right|bottom|inset)(?:[, _][^\]\s]*)?\]|duration-\[(?!(?:150|200|250)ms\])(?:[+-]?(?:\d+(?:\.\d+)?|\.\d+))ms\])`
const MOTION_MESSAGE =
  'Motion Vocabulary: use allowed state-change properties and duration tiers; keep named exceptions scoped to their line.'
const motionPattern = (start, end) => `${start}${MOTION_TOKEN}${end}`
const MOTION_VOCABULARY_SELECTORS = [
  {
    selector: `Literal[value=/${motionPattern(String.raw`(?:^|[\s:!])`, String.raw`(?=$|[\s!])`)}/]`,
    message: MOTION_MESSAGE,
  },
  ...[
    [':first-child[tail=true]', String.raw`(?:^|[\s:!])`, String.raw`(?=$|[\s!])`],
    [':first-child[tail=false]', String.raw`(?:^|[\s:!])`, String.raw`(?=[\s!])`],
    [':not(:first-child)[tail=true]', String.raw`[\s:!]`, String.raw`(?=$|[\s!])`],
    [':not(:first-child)[tail=false]', String.raw`[\s:!]`, String.raw`(?=[\s!])`],
  ].map(([boundary, start, end]) => ({
    selector: `TemplateElement${boundary}:matches([value.raw=/${motionPattern(start, end)}/], [value.cooked=/${motionPattern(start, end)}/])`,
    message: MOTION_MESSAGE,
  })),
]

// bu-yykif: the Motion Vocabulary bans skeleton-pulse; use a static placeholder or FetchingDim.
const ANIMATE_PULSE_SELECTORS = both(
  String.raw`\banimate-pulse\b`,
  'animate-pulse is forbidden (bu-yykif). Use a static placeholder block for a skeleton, or ' +
    'FetchingDim (components/ui/fetching-dim.tsx) for a background-refetch dim.',
)

// bu-qvnce.14 / bu-ep4ks.15: a bare numeric refetchInterval hides whether it is the
// primary update path or a bus-covered reconcile sweep (src/lib/poll-policy.ts).
const POLL_POLICY_SELECTORS = [{
  selector: 'Property[key.name="refetchInterval"] Literal[value=type(number)][value>=0]',
  message: 'refetchInterval must use a named poll-policy token (POLL_BUS_RECONCILE_MS from ' +
    'src/lib/poll-policy.ts, or an equally-named local constant), not a raw number ' +
    '(bu-qvnce.14). A test asserting the resolved cadence may use a line-level ' +
    'eslint-disable-next-line instead of importing the constant.',
}]

// bu-sd0l7.3: formatDuration/formatCost have canonical homes in src/lib/.
const FORMAT_CLONE_SELECTORS = declared(
  'formatDuration|formatCost',
  'Do not redeclare formatDuration/formatCost (bu-sd0l7.3). Import from "@/lib/format-duration" ' +
    'or "@/lib/format-cost"; a genuinely different contract needs a line-level ' +
    'eslint-disable-next-line with a reason (see lib/format-duration.ts).',
)

// bu-q7vx1q.6: trend charts draw linear or step joins only (components/ui/TimeSeriesChart).
const TREND_CHART_GRAMMAR_SELECTORS = [{
  selector:
    'JSXAttribute[name.name="type"][value.value=/^(?:monotone|monotoneX|monotoneY|natural|basis|cardinal|bump|bumpX|bumpY)$/]',
  message: 'Smoothed curve types invent values between readings (bu-q7vx1q.6). Use ' +
    'components/ui/TimeSeriesChart, which draws linear or step joins only.',
}]

// A distinct rule survives later no-restricted-syntax/imports overrides. It reads
// original names and static module sources; it never evaluates computed ones.
const isRecharts = (source) => source?.type === 'Literal'
  ? source.value === 'recharts'
  : source?.type === 'TemplateLiteral' && source.expressions.length === 0 &&
    source.quasis[0].value.cooked === 'recharts'
const isRawChart = (name) => ['LineChart', 'AreaChart'].includes(name?.name ?? name?.value)
const TREND_CHART_GUARD_PLUGIN = {
  rules: {
    'no-raw-import': {
      meta: {
        type: 'problem',
        schema: [],
        messages: {
          rawImport: 'Trend charts use components/ui/TimeSeriesChart (time-proportional axis, ' +
            'visible observations, gap breaks, labelled stale tail), not a raw recharts ' +
            'LineChart/AreaChart (bu-q7vx1q.6).',
        },
      },
      create(context) {
        const reject = (node) => context.report({ node, messageId: 'rawImport' })
        const flagged = (node, side) => isRecharts(node.source) && node.specifiers.some((s) =>
          s.type === `${side}NamespaceSpecifier` ||
          (s.type === `${side}Specifier` && isRawChart(side === 'Import' ? s.imported : s.local)))
        return {
          ImportDeclaration: (node) => flagged(node, 'Import') && reject(node),
          ExportNamedDeclaration: (node) => flagged(node, 'Export') && reject(node),
          ExportAllDeclaration: (node) => isRecharts(node.source) && reject(node),
          ImportExpression: (node) => isRecharts(node.source) && reject(node),
        }
      },
    },
  },
}

// Keyboard handling belongs to the app-wide shell map and the page-scoped shortcut
// registry; a third listener would drift from their editable-field/dialog protections.
const KEYDOWN_LISTENER_SELECTORS = [{
  selector: 'CallExpression[callee.property.name="addEventListener"][arguments.0.value=/^key(?:down|up)$/]',
  message: 'Raw keydown listeners are forbidden outside use-keyboard-shortcuts.ts and ' +
    'use-register-shortcut.tsx. Register the binding through the keyboard registry so ' +
    'editable-field and dialog suspension stay consistent.',
}]

// bu-ep4ks.11: window.confirm cannot show pending state or carry evidence.
const NO_WINDOW_CONFIRM_MESSAGE =
  'window.confirm is banned in this file (bu-ep4ks.11). Use ConfirmDialog ' +
  '(components/ui/confirm-dialog.tsx): it shows a pending state and can carry evidence.'
const NO_WINDOW_CONFIRM_SELECTORS = [
  'CallExpression[callee.object.name="window"][callee.property.name="confirm"]',
  'CallExpression[callee.name="confirm"]',
].map((selector) => ({ selector, message: NO_WINDOW_CONFIRM_MESSAGE }))

// bu-ep4ks.15: a raw <th> needs `scope`; jsx-a11y only validates it when present.
const TH_SCOPE_SELECTORS = [{
  selector: 'JSXOpeningElement[name.name="th"]:not(:has(JSXAttribute[name.name="scope"]))',
  message: 'A raw <th> must declare scope="col" (or "row") so a screen reader can associate ' +
    'it with its cells (bu-ep4ks.15). Prefer TableHead from components/ui/table.tsx.',
}]

// bu-7exe4.11: outline-none needs a local focus replacement; the global --focus
// outline is the floor beneath it. ring-ring is below the WCAG 1.4.11 floor.
const NO_UNGUARDED_OUTLINE_NONE_SELECTORS = both(
  String.raw`^(?!.*(?:^|\s)(?:focus-visible|focus):ring-focus(?:\s|$))(?=.*\boutline-none\b).*$`,
  'outline-none strips the native focus indicator with no local replacement (bu-7exe4.11). ' +
    'Add a focus-visible:ring-focus affordance; the global --focus outline remains the floor.',
  's',
)
const RING_RING_SELECTORS = both(
  String.raw`\bring-ring(?:\/\d+)?\b`,
  'ring-ring is below the WCAG 1.4.11 non-text contrast floor (bu-7exe4.11). ' +
    'Use the measured ring-focus token.',
)

// Owner time: display timestamps go through components/ui/time.tsx (<Time> or
// formatOwnerDateTime). These files format backend date keys or derive calendar and
// query boundaries, not event timestamps shown to the owner.
const OWNER_TIME_EXEMPT = [
  'src/components/chronicles/RecentDaysIndex.tsx',
  'src/components/chronicles/WorkScheduleSettings.tsx',
  'src/components/costs/CostStripeChart.tsx',
  'src/components/dashboard/session-stripe-utils.ts',
  'src/components/health/MealTracker.tsx',
  'src/lib/day-window.ts',
  'src/lib/hourly-buckets.ts',
  'src/lib/medication-schedule.ts',
  'src/lib/memory-derived.ts',
  'src/pages/ApprovalsPage.tsx',
  'src/pages/CalendarWorkspacePage.tsx',
  'src/pages/calendar/ButlerEventDialog.tsx',
]
const OWNER_TIME_MESSAGE = 'Use <Time> or formatOwnerDateTime (components/ui/time.tsx) for owner-visible ' +
  'timestamps, not locale or Intl date formatting (bu-6jv4m.14).'

// Keep this separate from no-restricted-syntax: later file-family lists replace
// that rule. Resolve Date bindings so numeric toLocaleString keeps working.
const OWNER_TIME_PLUGIN = {
  rules: {
    'no-local-date-format': {
      meta: { type: 'problem', schema: [], messages: { ownerTime: OWNER_TIME_MESSAGE } },
      create(context) {
        const source = context.sourceCode
        const isDate = (node, seen = new Set()) => {
          if (!node) return false
          if (node.type === 'NewExpression') return node.callee.name === 'Date'
          if (node.type !== 'Identifier') return false
          let scope = source.getScope(node)
          while (scope && !scope.set.has(node.name)) scope = scope.upper
          const variable = scope?.set.get(node.name)
          if (!variable || seen.has(variable)) return false
          seen.add(variable)
          return variable.defs.some((definition) =>
            definition.name.typeAnnotation?.typeAnnotation?.typeName?.name === 'Date' ||
            (definition.type === 'Variable' && isDate(definition.node.init, seen)))
        }
        return {
          CallExpression(node) {
            const callee = node.callee
            if (callee.type === 'MemberExpression' &&
                (callee.computed ? callee.property.value : callee.property.name) === 'toLocaleString' &&
                isDate(callee.object)) context.report({ node, messageId: 'ownerTime' })
          },
          Property(node) {
            if ((!node.computed && node.key.name === 'timeZone' || node.key.value === 'timeZone') &&
                node.value.value === 'UTC') {
              context.report({ node, messageId: 'ownerTime' })
            }
          },
        }
      },
    },
  },
}

// Selector parts shared by every file family (see the note above).
const SHARED = [
  ...HSL_VAR_SELECTORS, ...RETIRED_CARD_SELECTORS, ...TOKEN_FALLBACK_SELECTORS,
  ...STATUS_COLOR_SELECTORS, ...POLL_POLICY_SELECTORS, ...ANIMATE_PULSE_SELECTORS,
  ...MOTION_VOCABULARY_SELECTORS, ...FORMAT_CLONE_SELECTORS, ...NO_WINDOW_CONFIRM_SELECTORS,
  ...NO_UNGUARDED_OUTLINE_NONE_SELECTORS, ...RING_RING_SELECTORS,
]
const TS_SET = [...SHARED, ...KEYDOWN_LISTENER_SELECTORS]
const TSX_SET = [
  ...TS_SET, ...HEX_COLOR_SELECTORS, ...PRIMITIVE_REDECLARATION_SELECTORS,
  ...HANDROLLED_OVERLAY_SELECTORS, ...TREND_CHART_GRAMMAR_SELECTORS, ...TH_SCOPE_SELECTORS,
]
// components/ui/ and the passport wrapper are the canonical declarations: no
// redeclaration, overlay or trend-grammar bans.
const UI_SET = [...TS_SET, ...HEX_COLOR_SELECTORS, ...TH_SCOPE_SELECTORS]
const without = (set, removed) => set.filter((entry) => !removed.includes(entry))
const restricted = (selectors) => ({ 'no-restricted-syntax': ['error', ...selectors] })

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    ignores: ['src/components/ui/**'],
    plugins: { 'trend-chart': TREND_CHART_GUARD_PLUGIN },
    rules: { 'trend-chart/no-raw-import': 'error' },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      // Static a11y gate: missing alt text, invalid ARIA, clickable non-interactives.
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
    rules: {
      // `role` on custom components (IdentityChip) is a domain prop, not ARIA.
      'jsx-a11y/aria-role': ['error', { ignoreNonDOM: true }],
      // Every autoFocus (audited bu-86c4c.16) answers an explicit user action
      // inside a Dialog/Sheet/inline editor, which is APG-recommended behavior.
      'jsx-a11y/no-autofocus': 'off',
    },
  },
  {
    // Each provider ships with its hooks as one tightly-coupled unit.
    files: [
      'src/components/ui/**/*.{ts,tsx}',
      'src/lib/command-registry.tsx',
      'src/lib/page-context.tsx',
      'src/hooks/use-register-shortcut.tsx',
      'src/lib/event-bus.tsx',
    ],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['**/*.test.{ts,tsx}', ...OWNER_TIME_EXEMPT],
    plugins: { 'owner-time': OWNER_TIME_PLUGIN },
    rules: {
      'owner-time/no-local-date-format': 'error',
      'no-restricted-properties': ['error',
        { property: 'toLocaleDateString', message: OWNER_TIME_MESSAGE },
        { property: 'toLocaleTimeString', message: OWNER_TIME_MESSAGE },
        { object: 'Intl', property: 'DateTimeFormat', message: OWNER_TIME_MESSAGE },
      ],
    },
  },
  // Plain .ts can never hold JSX, so the JSX-only bans cannot fire there.
  { files: ['**/*.ts'], rules: restricted(TS_SET) },
  {
    files: ['**/*.tsx'],
    ignores: ['src/components/ui/**', 'src/components/secrets/passport/atoms.tsx'],
    rules: restricted(TSX_SET),
  },
  {
    files: ['src/components/ui/**/*.tsx', 'src/components/secrets/passport/atoms.tsx'],
    rules: restricted(UI_SET),
  },
  {
    // The only two homes allowed to own DOM key listeners.
    files: ['src/hooks/use-keyboard-shortcuts.ts'],
    rules: restricted(without(TS_SET, KEYDOWN_LISTENER_SELECTORS)),
  },
  {
    files: ['src/hooks/use-register-shortcut.tsx'],
    rules: restricted(without(TSX_SET, KEYDOWN_LISTENER_SELECTORS)),
  },
  {
    // Last, so it wins over the generic .tsx list for the audited files.
    files: BLUE_PURPLE_STATUS_AUDIT_FILES,
    rules: restricted([...TSX_SET, ...BLUE_PURPLE_STATUS_SELECTORS]),
  },
  {
    // butler-secrets §No-LLM-Narration Invariant: /secrets surfaces never call an LLM.
    files: [
      'src/pages/Secrets/**/*.{ts,tsx}',
      'src/pages/SecretsPage.{ts,tsx}',
      'src/components/secrets/**/*.{ts,tsx}',
    ],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['@anthropic-ai/sdk', '@anthropic-ai/sdk/*'],
          message: 'LLM SDK imports are forbidden in /secrets surfaces. ' +
            'See butler-secrets §No-LLM-Narration Invariant.',
        }],
      }],
    },
  },
])
