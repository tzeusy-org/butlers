## Context

The dashboard persists `cli-auth/codex` in the shared/public Tier 1
credential store.  A schema-isolated daemon constructs a `CredentialStore`
with a butler-local pool and public as its fallback, and ordinary `load()`
prefers the local row.  A runtime-originated Codex rotation could therefore
write a local row which permanently shadows a later dashboard refresh.

The canonical `~/.codex/auth.json` is also mutable: a Codex subprocess can
rotate it, while post-subprocess persistence is intentionally fire-and-forget.
A naïve pre-launch `DB → file` copy would overwrite a just-finished local
rotation; an older in-flight session could later overwrite a newer dashboard
credential in the DB.

## Goals / Non-Goals

**Goals:**

- Make a dashboard refresh effective for the next Codex spawn without a
  daemon restart whenever the adapter has an explicit shared/public
  credential authority.
- Treat shared/public `cli-auth/codex` as the explicit authority whenever the
  store has a shared fallback; retain local authority only in flat topology.
- Preserve a known local CLI rotation or let a newer dashboard refresh win,
  deterministically and without last-writer-wins credential corruption.
- Keep file updates atomic and `0600`, validate DB auth documents before a
  replacement, and never log raw credential values.
- Reconcile before both speculative and on-path Codex prewarm.
- Do not infer shared authority from a schema-local direct-dispatcher pool.

**Non-Goals:**

- Restarting services, changing an already-running process, replaying a failed
  session, or changing model catalog/failover/ingress behavior.
- Adding a migration, polling loop, secret API output, or dependency.
- Creating and lifecycle-managing a shared credential pool for split-topology
  direct dispatchers (including scheduled jobs and standalone connectors);
  that broader seam is tracked in `bu-ih90b`.

## Decisions

### 1. Codex has an explicit shared credential authority

`persist_token()` and restoration use `load_shared()` / `store_shared()` for
Codex when a fallback exists.  This bypasses a stale schema-local row.  Other
CLI providers retain their established resolution behavior, and a flat store
falls back to its local pool.

### 2. Use a captured optimistic-concurrency snapshot

Preflight returns the exact authority snapshot it reconciled to the canonical
file.  The adapter revalidates and captures that snapshot again immediately
before each subprocess spawn.  The final completed operation then uses its
own captured snapshot for a conditional shared-store update:

```
shared value at launch == expected snapshot ? write local rotation : skip
```

The credential-store primitive can insert only when the expected snapshot is
absent. Runtime finalization is more conservative: it writes only when
preflight captured a concrete, valid authority value. An unavailable,
malformed, or absent row is not implicit permission to bootstrap from a local
auth file. New Codex device auth is persisted explicitly by the dashboard flow,
and a dashboard refresh or another winning runtime rotation makes an old
session's update affect zero rows.

### 3. Flush a detected local rotation before DB replacement

The per-path in-process lock serializes local reconciliation/finalization. A
coherent stat/read/stat snapshot carries the private authority value and full
file fingerprint needed for CAS. If the local file changed since that
baseline, preflight attempts its CAS before reading or replacing from DB. A
success promotes exactly the read rotation; a CAS conflict then loads the
current authority and replaces the local file. Store failures leave the
working local file intact. A disappearing file clears cache state so a later
explicit dashboard login can establish a new credential normally.

### 4. Centralize atomic auth-file writing

`cli_auth.persistence` owns a same-directory temporary writer: restrictive
temporary mode, complete write, flush/fsync, atomic replace, and cleanup. It
is used by startup restore and live reconciliation. Codex DB values must parse
as non-empty JSON objects before replacing a local file; a matching file is
also repaired to `0600`.

### 5. Cover both prewarm paths and dashboard post-prewarm rotation

`CodexAdapter.speculative_prewarm()` reconciles before `login status` and
finalizes with that operation's captured snapshot afterward. `invoke()` does
the same before token freshness/isolated HOME and after on-path prewarm, then
revalidates immediately before every subprocess attempt and finalizes once
after all internal retries. The dashboard successful-login callback follows
the same captured-snapshot finalization path. Codex health updates are also
conditional on the credential bytes actually used, so an old refresh failure
cannot mark a dashboard replacement failing. The dashboard test endpoint
reconciles and verifies the canonical auth file around its status command
before it records a Codex result. Its fenced health update, probe-log insert,
and audit row share one credential-row transaction, so a Passport replacement
cannot land in between them. A Passport value write and every runtime value
write atomically clear previous health fields; Passport reads treat that reset
state as the credential-version fence and do not surface an older retained
probe-log row for the replacement. If `codex login status` itself rotates the
canonical file, the endpoint finalizes that successor against its pre-probe
snapshot but withholds the probe result; a concurrent dashboard successor wins
the same CAS.

### 6. Bound the best-effort authority path

Each credential-store load or conditional rotation write has a short timeout.
Each Codex invocation shares one finite synchronization allowance across its
initial preflight, on-path `login status` prewarm, refresh-lock acquisition,
immediate pre-spawn revalidation, and finalization rather than giving every
phase a full independent wait. `RuntimeAdapter` declares that allowance to
both the Spawner and direct `DiscretionDispatcher`, whose outer guards add it
outside the catalog provider timeout; the unchanged catalog timeout remains
the Codex subprocess limit. A blocked authority path therefore preserves
local auth and cannot consume the session runtime budget.

### 7. Require an explicit authority for direct dispatcher callers

`DiscretionDispatcher` forwards a credential store only when its caller
provides one. Calendar quick-add receives the API's known public credential
pool and supplies it explicitly. A generic schema-local dispatcher — including
a standalone connector with a potentially split cursor/control pool — must not
construct a store from its model pool: doing so can create the exact local
shadow this change eliminates. Creating an owned shared-pool lifecycle for
those callers is tracked in `bu-ih90b`.

## Risks / Trade-offs

- **A preflight store read or write can fail or time out.** It is best-effort,
  preserves the old runtime behavior and local file, and has a bounded wait so
  it cannot consume the session runtime budget.
- **Raw snapshot content exists briefly in process memory.** CAS requires an
  exact DB value. The private cache has no repr/logging path and is bounded by
  canonical auth paths; it avoids a race-prone read-before-write.
- **An existing session cannot be re-authenticated in place.** Its durable
  update is fenced by CAS; the next invocation receives the dashboard state.
- **Some direct dispatchers lack a shared-store injection seam.** They remain
  deliberately unwired instead of treating a private or split-topology cursor
  pool as authority; `bu-ih90b` owns the required scheduler/connector-pool
  extension.
- **An orphaned cross-process rotation has no launch provenance after a crash.**
  A fresh process without a known baseline conservatively applies shared DB
  authority instead of guessing that a local file is a valid successor. This
  preserves dashboard-refresh safety; durable cross-process provenance is
  tracked in `bu-gg4fo`.

## Migration Plan

1. Deploy through the normal image/restart workflow; no migration or manual
   secret repair is required.
2. The next Codex prewarm/invocation reconciles the shared credential before
   using its isolated HOME.
3. Roll back by reverting the code; existing credential and auth-file formats
   remain valid.
