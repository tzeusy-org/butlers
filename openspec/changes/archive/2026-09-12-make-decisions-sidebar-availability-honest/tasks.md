## 1. Decisions badge state

- [x] 1.1 Add RED hook regressions for available empty, positive count,
  `decisions_available: false`, and direct query-error states.
- [x] 1.2 Implement the narrow discriminated Decisions badge state while
  keeping QA and approval badge values numeric.

## 2. Sidebar rendering

- [x] 2.1 Add RED sidebar regressions for no marker on available zero, numeric
  positive count, and the labelled unavailable marker in rail, expanded, and
  mobile variants.
- [x] 2.2 Render the existing accessible degraded status marker for the
  Decisions unavailable state without changing navigation authority.

## 3. Verification and contract coherence

- [x] 3.1 Run focused hook and Sidebar tests, frontend lint, typecheck/build,
  and the required final quality gates.
- [x] 3.2 Revalidate the successor OpenSpec change strictly and review the
  final diff against the read-only Decisions boundary.
