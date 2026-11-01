# Vitest dependency import cost

Each CI Vitest child still collects its own fresh, complete unsharded reference,
then executes its actual half with the default isolated forks. Dependency
prebundling in `frontend/vite.config.ts` reduces repeated library import work;
it does not cache test declarations, membership, starts or outcomes.

Vitest 3's supported [dependency optimizer](https://v3.vitest.dev/config/#deps-optimizer)
bundles a library's internal imports for reuse across isolated workers. Both
`ssr` (node) and `web` (jsdom) include `date-fns` and `date-fns-tz`, alongside the
existing `@dagrejs/dagre` dependency. In the installed `date-fns-tz` version,
timezone-name formatting imports the `date-fns` barrel internally, so changing
application imports alone would leave that large graph on the collection path.
Production build chunking and the development dependency list are unchanged.

## Measurement protocol

The paired local experiment used the complete source at
`e6436fdbc3fad17788df0913d89d373b0d2fb5bf`, the locked dependencies (Vitest 3.2.4,
Vite 7.3.1), Node 24.6.0 and Linux CPU affinity `0-3`. Each run had a separate,
initially absent Vite cache directory and kept the default isolated forks,
parallel file execution and three workers. The second run changed only the
dependency optimizer configuration. No test bodies ran in this collection
experiment.

A scratch copy of the installed reference reporter recorded elapsed
`ctx.collect([])` time and public `TestModule.diagnostic()` phases. The actual
`ci_vitest.validate_reference` admitted both outputs. Complete JSON reference
equality, including every opaque item identity and declared mode, was required;
matching aggregate counts alone was insufficient. The summary artifact records
the configuration and canonical reference digest without raw names, parameters,
logs or exception text.

The first exploratory frontend-only source copy lacked documents imported by
four tests and failed collection; it is excluded from the paired results.
The five-file exploratory gain is also excluded from full-population evidence.

## Paired result

| Complete cold collection | Before | Prebundled |
| --- | ---: | ---: |
| Elapsed seconds | 285.457 | 228.145 |
| Files / items | 527 / 8,298 | 527 / 8,298 |
| Sum of module collection seconds | 422.447 | 212.451 |
| Sum of environment setup seconds | 252.472 | 280.133 |

Elapsed collection fell by 57.312 seconds (20.1%). The module phase totals sum
parallel work and must not be read as elapsed time. The reduction in module
collection, despite higher environment setup time, is consistent with avoiding
repeated library graph imports. Both complete references have canonical SHA256
`feb8e3707181c74cfb2f094d8f2ead29df5ce4b6ff7e42b3f23fcd1ffbcff21b`.
The [machine-readable summary](ci-vitest-import-cost.json) retains exact values.

## Limits and acceptance

This is one local paired observation, not a p95 or a hosted service-target
claim. The original hosted Node 24.21.0/four-CPU Vitest2 receipt spent 289.913s
in reference collection and 194.081s in execution (484.112s total), already above
the seven-minute target before other job overhead. The local Node minor version
differs; protected hosted CI and subsequent latency observations remain the
authority for that target. No selector, timeout, required gate or test assertion
is relaxed by this change.
