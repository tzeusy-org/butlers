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
- An observation older than 900 seconds (`MAX_SOURCE_AGE`, the profile's freshness maximum) is treated as
  unmeasured whatever it claimed, so a stale `available` can never publish as fresh `available`: alone it
  reduces to unmeasurable, beside measured inputs to `blind`. `read_health` applies the same bound.
- `observed_at` is the winning observation's time, or the oldest input's for `available`; times after
  `now` are clamped. Source age is `now - observed_at`.

## Publishing

The `health.dependencies` profile is *owned* by the installer when its evidence source is exactly
`capability_health`; only then does re-running the installer register a newly declared dependency (the
subjects are the code-declared set). A profile an operator configured with any other evidence source is
never touched.

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

## Collection

The dashboard-api runs one zero-LLM job (`butlers.jobs.capability_health`, every 60s). Each tick gathers
observations, reduces one verdict per declared dependency, and publishes those that changed or are due
for the 300s heartbeat.

- Credential states from the secrets inventory feed `google`, `steam`, `home_assistant`, `owntracks`,
  `spotify` and `telegram`: `ok`/`expiring` is available, `never_set` is unconfigured
  (`credential_missing`), `expired` is down (`oauth_revoked`), `failing` is down (`unknown`). `warn` and
  anything unrecognised is unmeasured. A dependency no credential feeds stays unmeasured, which readers
  see as `unknown`.
- A probe-backed claim (`ok`, `expiring`, `failing`) is observed at the provider's last probe time
  (`last_verified`), not when the inventory was read. A month-old success therefore ages past the
  900s bound and reads as unmeasured, and a claim with no (or a timezone-naive) probe time is
  unmeasured. `never_set` and `expired` are facts about the stored row and are observed at read time.
  Health does not rely on the lifecycle job's stale-`ok` reclassification.
- `conditions` is probed directly (acquire bounded separately, then a one-row read, 2s deadline), stamped at
  probe time. A timeout or connection failure is down (`domain_unreachable`); any other failure is
  unmeasured, never available. The service's own `DependencyReporter` seam has no in-process consumer.

### Writer login and rollout

The job publishes as `perception_health_writer` over its own small pool (at most two connections) from
`BUTLERS_HEALTH_WRITER_DSN` (optional `secretKeyRef` in the chart, from the local env Secret). Service
roles stay `NOINHERIT` with no memberships (perception.md), so the dashboard login cannot act as the
writer and the job never falls back to it. Without the variable the job does not start, logs once, and
readers see `unknown`.

Bootstrap and `finalize_interface()` refuse `LOGIN` only on `perception_owner`; the writer role is
created `NOLOGIN` once and a rerun does not reset it. Enabling it is a host-provisioning step, dev
only and content-blind: the password is generated by the provisioning process and is never typed,
echoed, put in a repo, a transcript, shell history or a psql command line.

DSN shape (placeholders only): `postgresql://perception_health_writer:<password>@<postgres-host>:5432/<database>`.

Provision (owner or lead), as one script so the value flows between processes:

1. Generate the password in the script (for example `openssl rand -base64 32 | tr -d '\n'`) into a
   variable, never to the terminal.
2. Set it by feeding `\password perception_health_writer` and the value to psql on stdin, so it is in
   no argv, history or server log (`\password` hashes client-side). Do not use
   `ALTER ROLE ... PASSWORD '<literal>'`: the literal lands in server logs. Then
   `ALTER ROLE perception_health_writer LOGIN`.
3. From the same process build the DSN and write the `BUTLERS_HEALTH_WRITER_DSN` key of the
   `butlers-local-env` Secret with `kubectl` (value from stdin or a mode 0600 file removed after), never
   echoing it.
4. Restart dashboard-api; check `/api/system/background-jobs` for `capability_health` (it reads
   `disabled` when the key is absent).

Disable or rotate: `ALTER ROLE perception_health_writer NOLOGIN`, remove the key from the Secret, restart
dashboard-api (the job then reports `disabled` and readers see `unknown`). To rotate, repeat steps 1 to 4
with the role still `LOGIN`; the old password stops working as soon as step 2 completes.

Known gaps:

- The bws-mode ExternalSecret mapping for `BUTLERS_HEALTH_WRITER_DSN` is not added; the key is
  local-env only until a BWS secret exists.
- Several credential rows (for example one per butler schema) can feed one dependency; the reducer takes
  the worst fresh state. A probe-less row is unmeasured, so most of these do not hold a dependency down,
  but a recently probed `failing` row still can.
- `conditions` reads `public.condition_instances` through the dashboard's `switchboard` pool. core_323
  grants SELECT to `butler_switchboard_rw` only, with RLS enabled but not forced. Migration-created
  public tables are owned by the migration role `butlers` (dev metadata), the same login dashboard-api
  uses, and an owner is not subject to unforced RLS, so the probe can read it. This is inferred from
  source and dev metadata, not proven against a live table. Until the environment has run core_323 the
  table does not exist, the probe fails with a missing-relation error, and `conditions` reads
  unmeasured (unknown), never available.
- Contention on the shared pool is not the store being down: waiting for a connection is bounded
  separately and reads unmeasured; only the query itself is timed against the store.
