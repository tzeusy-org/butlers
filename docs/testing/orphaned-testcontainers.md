# Orphaned Test Containers

> **Scope:** How pytest runs leak Docker containers, how to tell a leak from a container someone
> wants, and how to sweep the leaks safely.
> **Belongs here:** The identification rule, the Ryuk contract, the reaper script.
> **Does NOT belong here:** Fixture design (see [Markers and Fixtures](markers-and-fixtures.md)).

## The leak

DB-backed tests (`tests/api/*_db.py`, `tests/migrations/`, `tests/config/`) start an ephemeral
`pgvector/pgvector:pg17` container per pytest **process** via testcontainers. `pyproject.toml`
addopts carry `-n 3 --dist loadfile`, so one `make test` is three worker processes, each with its
own session-scoped container, and a few test modules open a second container of their own.

A run that reaches teardown removes its containers. This is not an assumption: a full DB-backed
suite run to completion during the bu-3zu5l investigation (`tests/api/`, 12 tests, exit 0) left the
host container count unchanged at 12 before and after. **Teardown works.** A run that is SIGKILLed
(agent timeout, ctrl-c, OOM) never gets there, and the container stays up indefinitely, holding RAM
and a published port. That is where the 11 leaked containers found in the wild came from, the
oldest three weeks old.

So the problem is confined to exactly the case a process cannot handle on its own: a killed process
cannot run its own teardown. That is precisely why testcontainers ships a sidecar, and it is why
the fix below is not a teardown change.

## Ryuk is enabled, and is the primary defence

Ryuk is **not** disabled for local runs. There is no `~/.testcontainers.properties`, no
`ryuk.disabled` property, and no `TESTCONTAINERS_RYUK_DISABLED` in the environment, so
`config.ryuk_disabled` falls through to its `False` default. It runs: every pytest process that
starts a container first starts `testcontainers-ryuk-<SESSION_ID>`, opens a TCP socket to it, and
registers the filter `label=org.testcontainers.session-id=<SESSION_ID>`. When the socket drops
(including via SIGKILL, because the kernel closes it), Ryuk removes everything carrying that label
and then exits.

There is one place the variable *is* set, and it is worth knowing before you grep and conclude this
page is wrong: CI sets `TESTCONTAINERS_RYUK_DISABLED: "true"` in `.github/workflows/ci.yml` (the
`check-preflight` job's smoke step and the `check-integration-1..5` shard jobs) and in
`.github/workflows/nightly.yml`. That is harmless there and cannot leak onto a developer machine,
because those jobs are `runs-on: ubuntu-latest`
throwaway VMs that are destroyed wholesale. (The `gha-runner-*` containers on this host belong to a
different project, not to butlers CI.) It matters here only because copying the CI environment into
a local run turns Ryuk off and defeats the live-Ryuk predicate below.

**Do not write an age-based reaper to replace this, and do not "re-enable" Ryuk: it is already on.**

Ryuk's one gap is that it cannot cover its own death. It runs with `auto_remove=True`, and nothing
in testcontainers-python 4.14.2 ever calls `Reaper.delete_instance()`, so a Ryuk container that
exits before its containers do vanishes without a trace and without a second chance. The containers
it was guarding (`AutoRemove=false`, `RestartPolicy=no`) then survive forever. That residue, not the
common case, is what the sweep below is for.

## Identifying an orphan

Age is not the signal. A three-week-old container can be a live investigation, and a ten-minute-old
one can already be garbage. Use provenance and ownership:

| Signal | Meaning |
| --- | --- |
| `org.testcontainers=true` + `org.testcontainers.session-id` | Machine-created by testcontainers. A container started by hand carries neither. |
| A running `testcontainers-ryuk-<session-id>` | The owning pytest session is **alive**. Leave it alone. |
| No such Ryuk | Positive evidence the owning session is gone. This is the load-bearing signal. |
| A Docker-generated name (`angry_dubinsky`) | Nobody chose it. |
| A hand-written name (`codex-pr3708-acl-repro-11668`) | Somebody chose it, which is provenance. Leave it alone. |
| A `com.docker.compose.*` label | Part of a compose stack such as `butlers-dev-*`. Never touch. |
| A `dev.butlers.keep` label | Explicit human pin. Never touch. |

Inspect one directly:

```bash
docker inspect <name> --format '{{json .Config.Labels}}'
```

## Sweeping

```bash
python3 scripts/reap_orphaned_testcontainers.py            # report only (exit 1 if any)
python3 scripts/reap_orphaned_testcontainers.py --json     # machine readable, with reasons
python3 scripts/reap_orphaned_testcontainers.py --reap     # remove the candidates
```

The rule never fires during a healthy run. A container created by a live session is spared twice
over: its Ryuk sidecar is running for the container's whole life, and it is nowhere near the age
backstop. Both protections are pinned by tests
(`test_live_session_is_spared_because_its_ryuk_is_running`,
`test_recent_container_is_spared_by_the_age_backstop`).

An agent may run this **without owner sign-off**, including `--reap`. The safety argument: the
predicates above are conjunctive, each one alone is enough to spare a container someone wants, and
every failure mode (a missing label, an unparseable timestamp, a `docker` call that errors) resolves
to "not reapable". The script's way of being wrong is to leave an orphan running, never to kill
something live. Age enters only as a backstop (`--min-age-hours`, default 4, comfortably past the
~40 minute full backend gate) covering the one case where a missing Ryuk does not imply a dead
session: a run launched with `TESTCONTAINERS_RYUK_DISABLED=true`. Treat that case as real rather
than theoretical, since it is exactly what CI does, and copying a CI command line into a local shell
is the obvious way to reach it.

To pin a container the sweep would otherwise take, give it a name of your own or label it:

```bash
docker run --name my-repro --label dev.butlers.keep=bu-xxxxx ...
```

## Noticing a leak as it happens

The root `conftest.py` retries Docker teardown for the known transient Docker API races and, after
the retries, gives up so the run can finish. Giving up leaks a live container. That path now names
the container in its `RuntimeWarning`, so a leak is traceable to the run that caused it instead of
being rediscovered weeks later by `docker ps`.
