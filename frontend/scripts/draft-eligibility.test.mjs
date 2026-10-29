import { expect, it } from 'vitest'
import { checkDraftModule } from './check-draft-eligibility.mjs'

// REQ-dashboard-shell-005: reject registration before any record or presence write.
it('refuses forbidden paths, credential controls, aliases, unknown identities and extra fields', () => {
  const registration = 'import { useDraft } from "@/hooks/use-draft"; useDraft("health.condition", ["create"], { name, status, diagnosedAt, notes }, restore);'
  expect(checkDraftModule('src/components/health/ConditionForm.tsx', registration)).toEqual([])
  for (const filename of ['src/pages/SecretsPage.tsx', 'src/components/providers/ProviderConfig.tsx', 'src/components/ui/input.tsx']) expect(checkDraftModule(filename, registration).length).toBeGreaterThan(0)
  for (const source of [
    registration.replace('useDraft }', 'useDraft as persist }'),
    registration.replace('@/hooks/use-draft', '@/hooks/use-draft.ts').replace('useDraft }', 'useDraft as persist }'),
    registration + 'export * from "@/hooks/use-draft";',
    registration + 'import("@/hooks/use-draft");',
    registration.replace('"health.condition"', '"unknown.surface"'),
    registration.replace('notes }', 'notes, password }'),
    registration + '<input type="password" />',
    registration + '<input type={purpose} />',
    ...["credential", "private-key", "recovery-code", "auth-session", "copy-once"].map((purpose) => registration + `<input data-purpose="${purpose}" />`),
  ]) expect(checkDraftModule('src/components/health/ConditionForm.tsx', source).length).toBeGreaterThan(0)
})
