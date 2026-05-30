import js from '@eslint/js'
import globals from 'globals'
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
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
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
