import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

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
  // ---------------------------------------------------------------------------
  // Chart color plumbing guard (bu-86c4c.5)
  //
  // Every theme color token (--primary, --chart-1..5, etc.) is a full
  // oklch(...) color literal, not a raw HSL component tuple, so wrapping one
  // in hsl(var(--x)) is invalid CSS — browsers drop the declaration and the
  // series/element silently renders black/invisible in the dark theme. This
  // exact bug hit 9+ recharts components before this bead fixed it. Reference
  // tokens directly with var(--x), or use the chartColor()/chartColorAlpha()
  // helpers in src/lib/chart-colors.ts for chart series.
  // ---------------------------------------------------------------------------
  {
    files: ['**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
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
