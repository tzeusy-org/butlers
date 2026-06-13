## 1. Spec Delta

- [x] 1.1 Write `specs/dashboard-domain-pages/spec.md` replacing all three
      hex-literal occurrences with named CSS token references:
      - Measurements blood-pressure chart: `#3b82f6` → `var(--category-1)`,
        `#f43f5e` → `var(--category-5)`
      - Symptoms severity bar: `#22c55e` → `var(--severity-low)`,
        `#ef4444` → `var(--severity-high)`
      - Contacts label palette: eight hex literals → `var(--category-1)` through
        `var(--category-8)`
- [x] 1.2 Confirm no `#RRGGBB` strings remain in the delta file.
- [x] 1.3 Confirm `--chart-*` tokens are NOT included (out of scope per
      design-language.md exemption).

## 2. Validation

- [x] 2.1 Run `openspec validate token-system-spec-sync` and confirm pass.

## 3. Sync (owner step — do NOT run)

- [ ] 3.1 Owner runs `openspec archive token-system-spec-sync` after PR merges
      to promote the delta into `openspec/specs/dashboard-domain-pages/spec.md`.
