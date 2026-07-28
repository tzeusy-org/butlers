## 1. Verdict source settlement

- [x] 1.1 Add focused System page render regressions for healthy database and
  egress state, database loading/error, egress loading/non-403 error, and
  egress `isForbidden` limited visibility.
- [x] 1.2 Add the existing database and egress hooks to
  `SystemVerdictBanner`'s `DispatchVerdict` source list, using their real query
  flags and treating only non-forbidden egress errors as unavailable.

## 2. Contract and verification

- [x] 2.1 Sync the narrow `system-overview-page` and `deployment-and-drift`
  delta requirements into their canonical specifications.
- [x] 2.2 Run the focused frontend test, frontend lint and build, strict
  OpenSpec validation, and a whitespace/diff review for the completed change.
