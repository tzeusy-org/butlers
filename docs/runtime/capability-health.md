# Capability health

RFC 0038 S6 defines one health value per declared dependency (`butlers.core.capability_dependencies`,
the same IDs a tool descriptor names in `depends_on`). This page is the contract of
`butlers.capability_health`. The reducer, publisher and reader exist; nothing runs them yet (the
collectors and periodic job, then the broker/runner/admission consumers, follow).

## One value, one writer

Health is published as the `system.dep` perception kind, value `{state, cause, observed_at}`
([perception](../data_and_storage/perception.md)). The only writer is `HealthPublisher`, through
`perception_write`'s generation compare-and-set under the `perception_health_writer` role.

| State | Causes it may name |
| --- | --- |
| `available` | none |
| `degraded` | `provider_unavailable`, `domain_unreachable`, `signal_unmeasurable`, `unknown` |
| `blind` | `signal_unmeasurable`, `unknown` |
| `down` | `oauth_revoked`, `provider_unavailable`, `domain_unreachable`, `unknown` |
| `held` | `policy_held` |
| `unconfigured` | `not_configured`, `credential_missing` |

## Reducer

`reduce(dependency, observations, now)` is pure and order independent. An `Observation` is one
collector's `(state, cause, observed_at)` claim, or `state=None` for "could not measure".

- Worst state wins: `held`, `unconfigured`, `down`, `blind`, `degraded`, `available`. Inside a state the
  earlier cause in a fixed order wins (`oauth_revoked` before `provider_unavailable` before
  `domain_unreachable` before `unknown`).
- An input that could not be measured is never read as healthy: beside measured inputs the dependency is
  at least `blind`/`signal_unmeasurable`; with nothing measured the result is *unmeasurable* and the slot
  is published `unmeasurable` with no value.
- `observed_at` is the winning observation's time, or the oldest input's for `available`; times after
  `now` are clamped. Source age is `now - observed_at`.

## Publishing

`should_publish` writes on first contact, on a change of state or cause, and every 300 seconds. The
profile's freshness maximum is 900 seconds, so two missed ticks still read fresh and a third reads
`unknown`. A conflict returns the current generation and the publisher retries once with it; each
attempt has its own event key so a replay of one attempt is idempotent. A storage fault is reported as
`unavailable`, never as exception text.

## Reading and the typed result

`read_health` reads `system.dep.<dependency>` and returns `Health(state, cause, observed_at, generation,
source_age_seconds, availability)`. Anything but a fresh, valid value (a storage fault, an unauthorized
reader, a missing, stale, unmeasurable or malformed slot) is `state=None, availability=unknown`. That is
not a healthy state and not an error string.

`first_unavailable` returns the `CapabilityUnavailable` result for the first dependency that is
*known* down, unconfigured or held, immediately and without waiting on a timeout. Its wire form is
closed words and counts only: `code`, `dependency`, `state`, `cause`, `since`, `source_age_seconds`,
`retryable` (true for `provider_unavailable`, `domain_unreachable`, `unknown`), `generation`.

Health can only refuse or annotate; it never authorizes. Unknown never relaxes a check: normal
authorization and the underlying call checks stay in force, and advisory surfaces say `unknown`.

## Conditions

The conditions service reports its own store as `system.dep.conditions` with a free-text reason.
`ReportCollector` keeps the latest report per dependency for five minutes, maps the reason to a closed
cause (network exception classes to `domain_unreachable`, anything else to `unknown`) and drops the
text. Silence after expiry is no claim, never "healthy".
