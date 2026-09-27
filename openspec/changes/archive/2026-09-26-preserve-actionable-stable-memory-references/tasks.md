## 1. Contract and reference codec

- [x] 1.1 Add strict module-memory scenarios for typed references, bounded
  rendering, and content-free refusal.
- [x] 1.2 Define one canonical fact/rule reference formatter and parser.

## 2. Context and feedback behavior

- [x] 2.1 Render references on local facts and rules inside existing budgets.
- [x] 2.2 Resolve references in confirm/helpful/harmful without exposing a
  caller-controlled authority input.
- [x] 2.3 Apply sensitivity and live-row predicates atomically to feedback
  mutations.

## 3. Verification

- [x] 3.1 Extend nearest context/action tests, including real-Postgres privacy
  and stale-reference cases.
- [x] 3.2 Run targeted tests, test-plan, lint/format, strict OpenSpec,
  overwrite guard, and repository guards.
