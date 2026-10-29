## Why

The runtime-authority change was archived on 2026-09-10 before PR #4046's
awaiting-first-heartbeat presentation amendment landed. Recompose that bounded
amendment as an active change against the current persisted-role baseline.

## What Changes

- Preserve all three adopted requirement prose and all twelve scenarios byte
  for byte; only their location, source-reference wrapper and required trace metadata change.
- Present content-blind setup evidence as an awaiting-first-heartbeat subset of
  unknown rows while a real heartbeat remains the only runtime promotion.
- Preserve additive API fields/counts, exact operator copy, optional catalog
  navigation, degraded-source behavior and the named verification seams.

## Capabilities

### Modified Capabilities

- `connector-base-spec`: additive presentation evidence, without a new role.
- `dashboard-ingestion-dispatch-console`: additive API and roster presentation.

## Impact

No source code, schema, registry, connector, provider, credential or deployment
changes. Existing runtime-authority requirements are already canonical and are
not repeated or reintroduced. Implementation remains separate work (bu-d7ifs7).

## Adoption and provenance

The owner adopted PR #4046 at `1a14d530abad009ad5ea08c5d71be3887c5a094a`
on 2026-09-15 (bu-poven, connector-preheartbeat-adoption, choice A). The
pre-recomposition head `f1c736db634829222f4b5ad9d31f40f258990e2b`
carries the same specification bytes. This change preserves each adopted
normative clause and scenario exactly; it does not seek or assert a new adoption.
The original archived change stays untouched. Fresh independent integration
review remains required. Source landing, implementation, runtime/provider use,
credentials, deployment, archive and merge are not authorized by recomposition.

## Source References

- `openspec/specs/connector-base-spec/spec.md`
- `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`
- `about/heart-and-soul/vision.md`, connector responsibility and honest authority.
- bu-poven and bu-xmk58, original adoption and current recomposition boundary.
