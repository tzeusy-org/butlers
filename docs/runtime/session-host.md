# Dark session-host foundation

`butlers.session_host` is a private library for RFC0038 D3 sandboxing and
authenticated local dispatch. Its process entrypoint (`butlers session-host`) and
off-by-default Compose/Helm manifests are described under
[The host process and its manifests](#the-host-process-and-its-manifests). It has no
provider profile and no imports from the existing daemon, runner or adapters.
Importing it starts no listener or process. It does not make any runtime/model tuple eligible.
This foundation implements part of `bu-kiuh8v`; that parent remains incomplete.

## Trust and launch contract

A trusted host owner selects the inert domain factory, runtime image hashes,
broker origin, staging root and TLS material. Requests cannot supply executable
paths, mounts, environment variables, profile proofs, trust roots or caller pins.
The factory receives validated runtime/model labels, but remains responsible for
refusing unsupported profiles; labels alone confer no eligibility.

`LocalControl` constructs its own TLS1.3 server context from bounded immutable PEM
bytes for one certificate leaf, its private key and the client CA. Each input is
at most 64 KiB. It parses and normalizes the X.509 leaf, hashes the actual DER, and
loads the normalized certificate and key through kernel-sealed memfds. The declared
server SHA256 pin must equal that leaf; the caller pin must differ. No externally
constructed SSLContext is accepted. TLS verifies the client chain and the host
checks the exact caller leaf pin before reading a command. The local client checks
the server chain, hostname and exact leaf pin before sending credential bytes.
Only a trusted constructor supplies these identities; no deployment PKI is inferred.

`LocalControl.start()` binds `127.0.0.1` on an ephemeral port unless its trusted
constructor passes `bind_host` (an IP literal) and `bind_port`. A non-loopback bind
is allowed only behind the same mTLS with the pinned caller and server leaves, plus a
NetworkPolicy that admits butlers-control alone; there is no sidecar. It is a dark
control channel, never a dashboard endpoint.
One connection carries one JSON object, prefixed by its four-byte big-endian
length. The aggregate limit is 280 KiB and the read/write deadlines are 3 seconds, except that
a `launch` reply is awaited until the invocation settles (see below).
Duplicate keys and unknown fields refuse. Escaped JSON can exceed the aggregate
limit even if individual decoded fields meet their limits.

The exact launch fields are `version`, `operation`, `invocation_id`, `goal_id`,
`endpoint`, `token`, `runtime`, `model`, `credential`, `prompt`, `timeout_s`.
Version is integer 1, operation is `launch`, and IDs are canonical UUID strings.
The credential is base64-encoded and decodes to 1–65536 bytes; the prompt is bounded
by 128 KiB of UTF-8. The finite invocation timeout is at most 300 seconds.
The endpoint must be the trusted origin plus `/goals/{canonical goal UUID}/mcp`,
with no query, fragment or alternative spelling. The bearer is an opaque canonical
43-character base64url token for 32 bytes. Shape checks do not authenticate broker
authority: the broker owns active-token/goal/invocation/epoch/grant checks.

**Launch reply.** The reply to `launch` arrives when the invocation settles, so the runner-side
client waits for the launch's own `timeout_s` plus 35 seconds (the owner's cleanup cap and
slack), not 3 seconds; a `timeout_s` that is not a plausible launch timeout gets the largest
bound (335s), never an unbounded wait. The reply is one of:

| Reply | Meaning |
| --- | --- |
| `{"status":"completed"}` | The runtime exited cleanly and cleanup was proven |
| `{"status":"failed","failure_class":C}` | It ran and did not finish well; `C` is `timeout`, `runtime_failed`, `containment_failed` (cleanup not proven; the owner is quarantined) or `cancelled` (revoke or host shutdown) |
| `{"status":"refused"}` | Nothing started: bad frame, replay, unknown profile, staging refused, owner unhealthy |
| `{"status":"refused","reason":"host_busy"}` | The concurrency bound is full; nothing started and the id is not remembered, so it may be offered again |

Either `completed` or `failed` may carry `"usage"`: counts only (`input_tokens`, `output_tokens`,
`cached_input_tokens`, each an integer from 0 to 2^53) that the domain reports through an
optional `usage()` after settlement. Anything else the domain returns there is dropped. The host
never inspects runtime output, so provider classes (auth, quota, rate limit) are not reported;
the runner treats an unknown class as non-retryable (default-closed).

**Concurrency.** `LocalControl(max_concurrent=N)` (1 to 64, default 4) runs that many
invocations at once. A launch beyond the bound is refused `host_busy`.

**Early revoke.** A `revoke` for an id that has not launched answers `revoked` and tombstones
the id, so a later launch of it is refused (a Stop that lands before its launch). Repeating it is
idempotent. A revoke for an id that already ran still refuses. The host remembers at most 65536
ids; with none left it refuses new work rather than forget one, because forgetting would permit
a replay.

`revoke` accepts only `version`, `operation`, `invocation_id` and addresses the
current owned invocation. Responses contain fixed status strings only; prompt,
credential, bearer, runtime output and exception content are never returned or
logged by this channel. `revoked` means local invocation settlement, not broker
token revocation. The runner remains responsible for broker authority.

## Exact profile admission (dark)

`ProfileFactory()` has no installed tuples and refuses every runtime/model pair.
A trusted host constructor may supply a `ReviewedProfile` only after independent
review of actual runtime conformance evidence. A control request cannot supply a
profile, proof, approval flag, expected hash, executable path or adapter setting.
There is no fallback, runtime alias, model alias or duplicate-tuple resolution.

`Profile` binds the exact runtime/model, sorted image destination/SHA256 inventory,
and all command arguments plus the existing broker-only material contract. The
backend still reopens, verifies and seals actual image bytes at use. The profile
hash does not replace those consumption checks. Model/config/image changes require
a matching new conformance subject; there are no generic permission booleans.
Adapter denial settings must be present in the actual immutable image/command and
proved by execution, rather than inferred from labels. The existing staging owner
builds the sole-broker MCP configuration from the authenticated launch binding.
The initial admission step did not implement runtime-specific config/pointer
consumption. The closed OpenCode transport described below now implements that
source path; actual CLI/parser and catalog/provider conformance remain unproved.

The private conformance report is a bounded (16 KiB) closed JSON object with integer
`version=1`, exact `subject`, exact `checks`, `execution_sha256` and `artifact_sha256`.
Its subject contains `runtime`, `model`, `image_sha256`, `settings_sha256`. Every
required check has the literal string outcome `pass`: filesystem/process isolation,
restricted egress, built-in shell/filesystem/web denial, local MCP denial,
provider-hosted search/fetch/remote-MCP/code denial, broker-only MCP and catalog
conformance. Missing, unknown, duplicate, malformed or nonfinite fields refuse.
The expected report SHA256 comes separately from trusted review/deployment
configuration, never the report or request. Matching it establishes only the
identity of those reviewed bytes; this parser does not run conformance or attest
that a claimed observation happened. Review must reconcile the referenced actual
execution/artifacts and their complete runtime/image/settings subject before
configuring that pin. No real runtime/model report or eligible tuple is installed.

`InvocationOwner` keeps its healthy/replay checks and consumes the invocation ID
before resolving the inert host factory. An unsupported or unproven profile now
refuses before credential staging. The factory must allocate no process, FD or
other owned resource; only its returned domain's `execute` may start execution.
Accepted profiles still use the existing staging, failure quarantine, cancellation
and whole-domain settlement path. A failed admission does not release its consumed
invocation ID for replay. Synthetic pinned reports and synthetic domain execution
in admission tests earn only source-boundary evidence, never actual runtime,
provider, networking or native-conformance credit.

## Isolation, settlement and restart

`InvocationOwner` admits one invocation identity once per owner lifetime. The
local channel permits up to `max_concurrent` active launches and also keeps an in-memory
replay fence.
Neither fence is durable restart authority. An invocation receives only its staged
model credential, prompt and sole-broker MCP configuration. Staging uses a private
owned root; partial staging or uncertain cleanup poisons the owner and quarantines
material. No environment-credential fallback exists.

`BubblewrapDomain` starts from an empty root with separate user, filesystem, PID,
IPC, UTS and network namespaces, no inherited environment, dropped capabilities,
and disabled nested user namespaces. Its individually pinned image files and the
three staged inputs are copied into kernel-sealed memfds, then hashed and passed
by FD. Image files must be regular, single-link, non-writable and free of unsafe
mode bits; symlink components and changed input metadata refuse. A mutable source
pathname is never handed to Bubblewrap after verification. Image inventory and
snapshot memory sizing still require review for a production runtime profile.

The existing `scripts/runtime_cli_sandbox_init.c` gate shim runs as namespace PID1.
The owner acquires a pidfd before releasing the payload. Results are accepted only
after observing namespace-init death and reaping the direct child, not merely
leader exit. Output is bounded; timeout and repeated cancellation retain a finite
cleanup deadline. Missing observation, unresolved spawn or failed material deletion
quarantines the owner instead of claiming settlement. Disconnecting the control
client does not abandon the invocation; handler cancellation still observes cleanup.

Startup refuses a nonempty/unknown staging root. It does not replay, adopt, delete
or age-reap stale material. Independent containment evidence is needed before an
operator/runner can recover that root. This policy does not establish how a deployed
host restart marks durable runner attempts failed.

## Proof and remaining gates

The tests use synthetic static C payloads in real Bubblewrap namespaces and real
local TLS handshakes with synthetic certificates. They prove cross-invocation read
denial with an own-read companion, resistant descendant cleanup, output bounds,
immutable image consumption, actual certificate identity separation and local
credential staging/refusal. Synthetic domain doubles prove local state-machine
failures; they do not establish native or provider outcomes.
Hosted unit groups require the native setup: missing Bubblewrap or a refused user
namespace is failure, not skip. A local non-strict skip is not evidence. See
[CI native policy](../testing/ci.md#session-host-native-proof).

The network namespace currently denies all connectivity. Allowed broker/provider
reachability is unimplemented. No real model was called. Only the Codex tuple has an
adversarial tool-inventory proof (see [below](#codex-adapter-proof-dark-tuple-not-eligible));
other tuples have none. Provider-hosted tools cannot be disabled by host network
isolation alone.

The scoped CI-host profile experiment is specific to Noble ABI4 and does not
establish production deployment policy. It uses Canonical's purpose-built
[Bubblewrap guidance](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007)
and the [Noble package](https://packages.ubuntu.com/noble-updates/apparmor-profiles),
version `4.0.1really4.0.1-0ubuntu0.24.04.9`. The helper pins the published package
SHA256 and exact `bwrap-userns-restrict` member SHA256; the latter also defines the
necessary `unpriv_bwrap` child restriction. A separately pinned runner variant adds
only `mediate_deleted` to both named profile headers, following
[AppArmor MR1272](https://gitlab.com/apparmor/apparmor/-/merge_requests/1272).
Bubblewrap0.9 binds then unlinks temporary files for `--ro-bind-data`; the plain-path
prerequisite does not exercise that behavior. The observed native shim-exec ENOENT
is consistent with deleted-entry lookup failure, but the flag change needs a same-run
original/derived/original control using the unchanged native FD-backed node before
it establishes causality. No arbitrary stderr is emitted by that node's observer.
The original member and derived policy retain separate hashes in load receipts;
cleanup refuses a different variant or uncertain receipt. It leaves the global
restriction enabled,
refuses uncertain policy ownership, and removes observed owned policy in the job's
always cleanup path. No live policy changes run on developer hosts through tests.
Local helper controls use fake host operations and earn no hosted/AppArmor credit;
only the exact hosted negative/positive sequence and zero-skip native cases do.

The initial source CI also hit a distinct integration-group Postgres `DiskFull`
fixture failure. A userns profile cannot repair database disk exhaustion; that
infra family needs separate bounded handling and the full required checks must
still succeed before merge.

Production CA/workload identity, key lifecycle/rotation and pin distribution,
durable replay and restart ledger, fleet admission, authenticated production service,
runner/broker integration, restricted gateway and deployment, switching OAuth/Codex
and dashboard login to the [credential volume](#codex-auth-credential-volume-dark)
and tool-less model functions remain separate gates.
Existing Engineering/QA adapter behavior is unchanged. This child cannot complete
parent AC1/3/4/5/6/7 or authorize activation; native evidence can support only the
bounded dark-library portion of AC2 after hosted verification.

## Codex auth credential volume (dark)

bu-kiuh8v.3 builds the session-host target for Codex device auth. Nothing live uses
it yet. The system-global `cli-auth/codex` row stays the authority. The session
host's `CredentialVolume` (`session_host/credential_volume.py`) would replace
butlers-up's `~/.codex/auth.json` as the runtime copy.

- The volume holds one document, `codex-auth.json`. It must be a single non-empty
  JSON object, UTF-8, at most 64 KiB, with no duplicate keys.
- It is replaced only by compare-and-set on its SHA-256. Writes are atomic,
  fsynced, mode `0600` and `O_NOFOLLOW`.
- The root must be a real `0700` directory owned by the host user. Errors are fixed
  codes; no path or document byte appears in them.
- `LocalControl` takes the volume as an optional `credentials` argument and serves
  three closed operations over the pinned mTLS channel: `codex_auth_status`
  (digest only), `codex_auth_install` (document plus expected digest) and
  `codex_auth_read`.
- `codex_auth_read` is the one reply that carries a credential. That is how a
  rotation the runtime made on the host gets back to the authority. Only the
  pinned control-plane caller can reach it.
- A host built without a volume refuses all three operations.

The control plane's side is `session_host/codex_auth.py`:

- `SessionHostCodexAuth` checks every reply against an exact key set. It refuses a
  read whose digest does not match the decoded document.
- `push_codex_authority` installs the authority's exact document. It fails closed,
  leaving the volume unchanged, when the authority is missing, invalid or
  unreachable, or when the volume changes during the install.
- Push never rolls back a rotation. Codex refresh tokens are single-use. The caller
  passes the previous push's fence (`PushedAuthority`).
  - When the host holds a document other than the one last pushed, push first
    stores that document into the authority. This is conditional on the authority
    being unchanged, as the local reconcile flushes a rotated `auth.json`.
  - Only if that write loses, because a newer login exists, does the authority
    replace the host's document.
  - Without a fence, an unknown host document cannot be judged, so push fails
    closed. Persisting the fence across control-plane restarts is switch-time
    wiring. Until then, a dashboard login recovers it.
- `collect_codex_rotation` stores a changed document back to the authority. It does
  this only when the authority still holds the value that was pushed, so a newer
  dashboard login always wins. This matches today's `_codex_auth_sync` fencing.
- `persist_validated_staged_device_auth_bytes(..., session_host=)` and the dashboard
  router's `_DeviceAuthAuthorityPersistence` / `_build_on_success` take a
  keyword-only `session_host` target that defaults to `None`.
  - With `None`, a login writes the local file exactly as today.
  - With a target, a Codex login commits the authority, then installs on the volume
    and does not write the local file. An unreachable or conflicting host reports
    login failure.
  - Other providers refuse a target.

What pins the off path (`tests/session_host/test_codex_auth.py`):

- The `session_host` parameters are keyword-only and default to `None`.
- An AST scan fails if any live module outside `session_host/` constructs
  `SessionHostCodexAuth` or `CredentialVolume`.

The control plane's client side is `goal_runner/session_host_client.py` (bu-kiuh8v.11),
also dark:

- `session_host_clients(config, credentials, broker_origin=)` binds `local_command` to one
  host. `SessionHostClientConfig` names the dialed address and port, the TLS server name,
  the host's exact leaf pin, and three mounted files: CA, client leaf and client key.
  - Files are opened once, must be regular and at most 64 KiB, and the TLS library loads
    the descriptors that were checked. A secret mount's links are followed.
  - The key must not be readable by group or others, and the host's pin must not be the
    client's own leaf.
  - Every refusal is `session_host_client_unavailable`; no path or byte appears in it.
  - It returns the runner's `ControlSessionHost` and the volume's `SessionHostCodexAuth`
    over that one channel. Production CA, workload identity and rotation stay their own
    gate: this takes whatever a trusted deployment mounts.
- `CodexAuthFencedHost(host, authority, target, runtimes=)` wraps the runner's host for
  the runtimes named, which must be passed explicitly.
  - Before each such launch it pushes the authority (fenced by the last push). A push
    that fails closed refuses the launch as `NotInvoked` (`codex_auth_unavailable`), so
    no model runs.
  - After the launch, whether it succeeded or failed, it collects a rotation. A
    rotation a collect missed is flushed by the next push, under the same fence.
  - Those launches run one at a time: two runs on one document would spend a single-use
    refresh token twice. Other runtimes pass straight through and are never held.
- `local_command` takes the dialed `host` by trusted construction (default `127.0.0.1`).
  TLS still checks the server name and the pin. The TCP connect and the handshake are
  bounded together by the 3-second I/O deadline (`TimeoutError`, an `OSError`). A
  black-holed non-loopback host therefore fails fast instead of waiting out the
  kernel's SYN retries while `CodexAuthFencedHost` holds its lock.
- The AST scan also fails on any live construction of `ControlSessionHost`,
  `CodexAuthFencedHost` or `session_host_clients` outside the factory module.

Startup restore and the dashboard health probe take the same keyword-only
`session_host` target, default `None` (bu-kiuh8v.14). Every live caller passes none.

- `restore_tokens(..., session_host=)` pushes the Codex authority to the volume instead
  of writing `~/.codex/auth.json`. Other providers restore as today. The push fails
  closed, leaving the volume as it was. After a control-plane restart there is no fence,
  so a host document that differs from the authority is not judged and restore reports
  Codex `False` until a dashboard login.
- `probe_provider` and `probe_all(..., session_host=)` check the volume's document
  against the authority, for Codex only; the local file is neither read nor reconciled.
  - A volume that is absent, differs or cannot be read is `probe_failed` without
    contacting the backend.
  - The probe never installs, rotates or collects. Push and collect around launches
    reconcile the volume.
  - The prepared path, used after a login test, re-reads the volume against the
    prepared authority.

### The host process and its manifests

`butlers session-host` (`session_host/entrypoint.py`, bu-kiuh8v.15) runs one host
until SIGTERM or SIGINT. It imports neither the daemon nor the goal runner.

- `--pki-dir` holds `tls.crt`, `tls.key`, `client-ca.crt`, `server.sha256` and
  `caller.sha256`. Each file is at most 64 KiB; the key must not be readable by group
  or others; the pins are 64 hex characters. Any defect refuses as
  `session_host_pki_unavailable` before anything listens, with no path in the refusal.
- `--staging-root` must be empty (a fresh tmpfs or emptyDir), and `--credentials-root`
  is the Codex volume. Each is created `0700`, or must already be a directory owned by
  the host's uid; otherwise `session_host_storage_unavailable`. Without
  `--credentials-root`, every Codex auth operation refuses.
- `--bind-host` defaults to `127.0.0.1` and `--port` to an ephemeral port.
- It installs no runtime profile, so every launch refuses. `--broker-origin` is
  required, but no runtime reaches it yet.

The manifests are off by default and pinned by
`tests/scripts/test_session_host_env_guard.py`, which compares storage and exposure
exactly:

- Compose: the `session-host` profile on the `control-session` network, port 41198,
  nothing published. The PKI directory is `SESSION_HOST_PKI_DIR`, mounted read-only.
  It defaults to the keyless `deploy/session-host/pki-unprovisioned`, so an
  unprovisioned host refuses to start. The `session_host_credentials` volume holds the
  Codex copy and staging is a `0700` tmpfs.
- Helm (`sessionHost.enabled`, which requires `butlersControl.enabled`): one Deployment
  that mounts the `sessionHost.pkiSecret` Secret read-only (mode `0400`), a Memory
  emptyDir for staging, and the `<release>-session-host-credentials` PVC (kept on
  uninstall). There is a ClusterIP Service on `sessionHost.port`, and an ingress
  NetworkPolicy admitting only the release's butlers-control pods on that port. The
  PKI Secret is the only secret reference; the environment is still `HOME` alone.
  Rendering fails if `sessionHost.pkiSecret` names the release's `<release>-bws`
  Secret, so database credentials can never be mounted into the host.
- Key mounts, on both sides. Any Secret volume carrying a TLS key (the host's
  `tls.key` here, and the control plane's client key when it is wired) uses
  `defaultMode` `0400` or `0600`, and its pod sets no `fsGroup`. An `fsGroup` makes
  the projected key group-readable (`0440`), and both the host and
  `session_host_clients` refuse a key with any `0o077` bit, so the pod would fail
  closed. The env guard pins the host's `0400` and fails on an `fsGroup`.

Provisioning the PKI (CA, workload identity, rotation, pin distribution) is a separate
gate. Nothing in the control plane dials the host yet.

Remaining before the switch, which is a live change:

- Provisioning the session-host PKI and the control plane's client identity.
- Calling `session_host_clients` and `CodexAuthFencedHost` from the control plane.
- Moving `runtime_codex` out of butlers-up, which is the switch itself while butlers-up
  runs Codex.
- Passing the session-host target to the startup restore and the dashboard health probe.
- Wiring the OAuth mode (below). The mode itself exists (bu-kiuh8v.16).
  - In `entrypoint.build`, `ProfileFactory(codex_oauth=CodexOAuthVolume(volume))`, where
    `volume` is the same `CredentialVolume` instance passed as
    `LocalControl(credentials=)`. One instance keeps the lease and the control
    channel's installs on one volume object.
  - `CodexOAuthCredentials` around the control plane's `ModelCredentials`.
  - A reviewed OAuth profile.

### Codex OAuth mode (dark)

`CodexSettings(model, auth="oauth")` is the second closed Codex preset. The runtime
authenticates from the volume's device-auth document instead of an API key (bu-kiuh8v.16).

- The launch credential is not a secret. It is the SHA-256 of the document
  `CodexAuthFencedHost` just pushed, supplied by `CodexOAuthCredentials` only inside
  that fenced launch. Outside it, a Codex launch refuses `codex_auth_unavailable`
  (`NotInvoked`), and no API key is sent for an OAuth runtime.
- `CodexOAuthVolume.stage` leases the volume's document only if the volume still holds
  that digest (else `codex_auth_stale`). The document must be a valid device-auth
  document (`oauth_account`):
  - id, access and refresh tokens are present and `account_id` is set;
  - the id token's account claim repeats that `account_id`;
  - there is no API key.
- The document is staged byte for byte as the runtime's `auth.json`. This is the one
  writable native file, bound with `--bind-fd` from the staged file that was checked
  (regular, owned, `0600`, one link, staged hash). The rest stays sealed read-only:
  config, catalog, prompt and the API-key auth file of the other mode.
  - A bind mount of a single file cannot be renamed over, unlinked or replaced with a
    link from inside the sandbox. The pinned CLI rewrites it in place, so the runtime
    can change that file's bytes and nothing else on the host.
- Only after `settle()` proves the domain gone does the host read the file back
  (bounded at 64 KiB). It installs the file into the volume by compare-and-set, with
  the staged digest as the expected value:
  - Garbage, an API-key document or a document for another account is `rejected`, and
    the volume is left untouched.
  - A volume that moved on (a dashboard login) wins, as `conflict`.
  - The control plane's existing collect then stores the refresh in the authority.
- One lease per volume. A second OAuth launch refuses `codex_auth_busy` until the
  first is collected. The control plane's fence lock already serializes these launches;
  this is the host's own guard. An unproven settlement keeps the lease, so OAuth
  launches stay refused rather than racing a runtime that may still write.
- A stale or busy lease is refused before spawn (`CredentialNotStaged`). The owner
  still settles and discards the invocation, and the host replies
  `{"status": "refused", "reason": "credential_unavailable"}`. The runner raises
  `CredentialUnavailable("session_host_credential_unavailable")`, a `NotInvoked`
  refusal, so no model ran and nothing counts as a runtime failure. The same refusal
  after a spawn is still `sandbox_execution_failed`.

## Closed OpenCode settings transport (dark)

The trusted `opencode` preset targets the image-pinned OpenCode1.17.7 and an
explicit OpenAI API-key credential mode. It generates one fresh native
`opencode.jsonc` from the exact model and invocation BrokerBinding, with wildcard
built-in denial, only `broker_*` allowed, one remote broker and OAuth discovery
disabled. It imports no caller option map, plugin, local MCP or butler guidance.
Credential text-format checks establish no auth provenance or capability. Other
provider/auth modes are unsupported. The invocation text remains runner supplied;
this transport does not prove lead/child governing-policy assembly.

The native file is owned by the existing staging directory, hashed and copied to
a sealed memfd, then mounted read-only at `/invocation/opencode.jsonc`. The fixed
`OPENCODE_CONFIG` points there. Stdin uses an independently opened readonly
file description of the same sealed prompt, positioned at byte zero. Bubblewrap's
`--ro-bind-data` reads its bind descriptor to EOF; a plain `dup` would share that
offset and leave stdin empty. Both descriptions remain owner-tracked and closed.
Prompt and secret values never become argv or environment values. `HOME=/tmp/home`
names only fresh private tmpfs scratch, with no inherited HOME or contents. The
closed environment otherwise contains PATH and the credential-file pointer.
Native-file preparation failure precedes any spawn; partial files remain under
the existing settlement/discard/quarantine owner. Non-adapter domains retain
their original plan and DEVNULL stdin.

The exact proof subject includes the native template bytes, two broker binding
slots, CLI version, model/provider/auth mode, command, paths and stdin contract.
Changing those invalidates an old report. No real eligible tuple is installed.
Permission refusal is not by itself proof that a tool is absent from the actual
model-visible catalog, and no parser/provider-wire conformance is claimed. The
synthetic native transport consumer proves neither OpenCode nor model behavior.
The landed Dashboard CLI closure generator is not a goal image per-file proof.
The full image, restricted-network, runtime conformance, credential-retargeting
and tool-less-function acceptance criteria remain unfinished.

## Codex adapter proof (dark, tuple not eligible)

`CodexSettings` is the closed preset for `codex` on `gpt-6-sol`: Codex 0.159.2, API
key auth, no bypass flag, a generated config, and a private one-model catalog.
`Profile` refuses known bypass forms in any runtime's command, ignoring case and
reading `--flag=value` and `--flag value` alike: any argument containing
`dangerously`, `bypassPermissions` or `danger-full-access`, `--yolo`, and
`--sandbox`, `--approval-mode` or `--permission-mode` with an unrestricted value.
Restricting values such as `--sandbox read-only` are admitted. A runtime label must
be lowercase, so `Codex` cannot skip the closed `codex` preset. This is a
deny-list, not a parser of each CLI's grammar. It misses forms it cannot recognise
without that grammar:

- short switches whose meaning differs by runtime, such as Gemini's `-y`;
- long negations, such as Gemini's `--no-sandbox`;
- escapes inside config values, such as a TOML escape that hides
  `danger-full-access` in a Codex `-c` value.

Any of these would pass under a non-`codex` label that runs the same binary. Only
a closed preset like `CodexSettings` pins the exact argv, so a new runtime needs one
before it can be admitted. The live adapters
are unchanged and keep their flags until the switch release.

The proof runs the real binary through the production `BubblewrapDomain`, with
`--unshare-net`, against a fake provider and a fake broker. A helper joins the
sandbox's network namespace from outside and hands back a loopback listener, so
nothing else is reachable. The only proof-only delta is the `openai_base_url` line
and the broker origin. The fake provider sends hostile tool calls, and the checker
reads the tools the runtime offered and how it answered, over both WebSocket and
HTTP. Software tests run in CI. The native ones need the pinned binary:

```bash
npm install --ignore-scripts --prefix "$DIR" @openai/codex@0.159.2
export BUTLERS_CODEX_PROOF_BINARY="$DIR/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
uv run pytest tests/session_host/test_codex_adapter_proof.py -n 0
```

Without that variable they skip, and a skip is no evidence. Hosted CI does not set
it yet. A hash test pins the settings, so changing one needs a fresh native run.

What the pinned CLI taught us, each now a test:

- `tools: []` is not proof. On `gpt-6-sol`'s bundled catalog the real list, shell
  included, travels inside `input` as `additional_tools`. The checker walks the
  whole body.
- Feature flags cannot remove `apply_patch`. Model metadata registers it, which is
  why the private catalog exists. `read-only` refuses a patch that the bypass flag
  lets through.
- Without a bypass flag, `approval_policy=never` also refuses every MCP call, so a
  denial-only proof would pass a runtime that cannot reach the broker. The broker
  server is explicitly approved, and a broker call must complete for the proof to
  pass.
- The binary is 274 MiB, over the old 256 MiB image limit. The limit is now 384 MiB,
  and each launch holds a sealed copy, which is a sizing input for the host.

Each enforcing setting has a native row that removes it and requires failure.
Settings in `codex.py` marked unmeasured changed nothing this proof could see, so
they claim nothing.

### Native discovery (`tool_search`) is the broker's corpus, not provider search

The private catalog keeps `supports_search_tool`. It enables Codex's own
`tool_search`, which RFC 0027 native deferred discovery needs (`bu-r980fk`). On the
wire it is a distinct tool entry, `{"type": "tool_search", "execution": "client"}`,
whose description lists the MCP sources it searches. Its answers are
`tool_search_output` items holding the matching tools. With it on, the broker's
tools are deferred: they are no longer offered up front, and reach the model only
through search. The checker therefore classifies:

- a client-executed `tool_search` as its own surface entry, `client:tool_search`;
- a `tool_search` (or any search) the provider executes as `hosted:*`, which fails
  `provider_search_disabled` like web search.

The proof fails unless all of these hold:

- the offered `tool_search` lists `broker` and nothing else as its source;
- a hostile `tool_search` call is answered on the client and completes;
- every tool in the answer is in the `mcp__broker` namespace;
- the broker's `goal_ping` is reachable through it.

The sources check matters. A built-in family the config fails to disable, such as
multi-agent tools, becomes deferred too and shows up only in that list. Native rows
pin both directions: dropping `supports_search_tool` fails `broker_only_mcp`, and so
does a second MCP server, whose tools the same search returns. Three MCP resource
tools (`list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource`)
stay offered because the pinned CLI has no switch for them. They can only name
configured servers, so a broker test pins that the goal endpoint serves no
resource, template or prompt.

### Isolation measured on the live runtime

`filesystem_isolation` and `process_isolation` are now measured for this tuple. They
are no longer covered only by synthetic payloads. While the runtime waits on its
first provider answer, the proof reads the domain from outside:

- its mount table through `/proc/<pid>/mountinfo`;
- its files through `/proc/<pid>/root`;
- its pid namespace through that root's `/proc`.

The mount table is the primary filesystem check. A path walk cannot judge `/tmp`,
the runtime's own scratch, where a host directory bound underneath looks like
scratch. Every mount must be exactly one the production plan asks for, read from
the unmutated bwrap argv:

- `/` and `/tmp` must be fresh tmpfs;
- `/proc` must be proc;
- `/dev` must be bwrap's tmpfs and device nodes;
- each staged file must be bwrap's deleted data file.

A bind from the host anywhere, a planned mount point with a foreign source, a
second mount over a planned point, or a missing planned mount fails the check. As
a positive anchor, the walk must also find the staged runtime, invocation and
Codex config paths, so an empty or wrong view cannot pass.

Next to the launch, the proof stages a second invocation's real material under the
same stage root, with a canary credential, and starts a host process. The checks
also fail if the domain sees any of these:

- a path outside its plan;
- the canary in any readable file;
- the peer's stage directory;
- any process other than the shim and `/runtime/codex`;
- the host peer.

Native rows loosen the bwrap plan, and each fails on a completed launch with a
measured view, not because the launch broke:

- mount the peer's material;
- mount a host directory, at its own path or under `/tmp` (`/etc` at `/tmp/etc`);
- mount a host credential store under `/tmp`;
- mount a host directory over `/invocation`;
- drop `--unshare-pid`.

That is this host's bwrap and kernel. `tests/session_host/exact_image_codex_proof.py`
runs the same clean launches and every mutation row inside the rebuilt production
image, with the image's own bwrap and Codex binary. It needs Docker, so the owner
runs it. The shim there is compiled statically from the same source, because the
image ships a dynamically linked one, which cannot run inside the empty domain root.

### Measured checks

Five of the eleven measured checks show absence, not enforcement:

- `provider_search_disabled`, `provider_fetch_disabled`, `provider_remote_mcp_disabled`
  and `provider_code_disabled` pass when no hosted tool appears anywhere in the client
  request. Hosted tools run at the provider, so no hostile call can test them here,
  and a provider that ran one anyway would not show up.
- `builtin_web_disabled` refuses a hostile `web_search` call. The pinned CLI has no
  local web function, so it refuses that call whatever the config says, and no
  native mutation can make this check fail. Hosted search is covered only by
  `provider_search_disabled`.

The other six enforce. Each has a native mutation that makes it fail:

- `builtin_shell_disabled`, `builtin_filesystem_disabled`, `local_mcp_disabled`,
  `broker_only_mcp`. These send hostile calls and require each one to be refused,
  the broker call to complete, and discovery to stay on the broker.
- `filesystem_isolation`, `process_isolation`. These read the live domain, as
  described above.

The tuple stays ineligible. `ReviewedProfile` requires 13 checks. The proof measures
eleven and reports the other two as `unproven`, which admission refuses:

| Check | What remains | Who can run it |
| --- | --- | --- |
| `restricted_egress` | The domain has no route out, so a real launch cannot reach a provider or broker yet. Needs the gateway and the dev network proof (`bu-kiuh8v.4`). | Dev deployment |
| `catalog_conformance` | RFC 0027 gate at the real catalog size, through the native discovery measured above (`bu-r980fk`). | Broker work |

The exact-image run confirms the two isolation checks in the installed image. It
does not by itself make the tuple eligible. Also unproven:

- a real provider honoring the request (no real calls were made, so server-side
  behavior is outside this proof);
- the bundled TLS trust path.

### Control-channel authentication

D3 requires an authenticated control channel. This is an engineering choice, not an
owner gate. The choice is mutual TLS 1.3 with constructor-held leaf pins
(`LocalControl`, above), not a shared bearer or a Unix-socket peer credential:

- The runner and the session host are separate processes and may become separate
  pods. A peer-credential check only works on one host, and would have to be
  replaced at that split.
- A bearer is replayable by anyone who reads it once, and the channel carries model
  credentials. A pinned client leaf is useless without its key, and the key never
  crosses the channel.
- Pinning the exact leaf, on top of chain verification, means a second certificate
  from the same CA cannot impersonate either side. That is also why the server and
  caller pins must differ.
- The trusted constructor supplies every identity, and no deployment PKI is
  inferred. Issuing, rotating and distributing production certificates is a
  switch-time gate, together with provider activation. Until then, tests use
  synthetic certificates only.

### OAuth mode native proof

The same harness runs OAuth mode (`{"oauth": True}`), with its own pinned settings hash.
The staged document's access token has expired, so the runtime must refresh. Two more
proof-only deltas apply, besides `openai_base_url`:

- `chatgpt_base_url` points at the fakes;
- `CODEX_REFRESH_TOKEN_URL_OVERRIDE` points the refresh at a fake token endpoint.

Production sets neither.

- The runtime refreshed once, with the staged refresh token, over WebSocket and over
  HTTP. The provider saw only the refreshed access token and the staged account. All
  nine measured checks pass as in API-key mode, and the broker call completes.
- After settlement the volume holds the refreshed document for the same account, and
  the lease is free.
- If the token endpoint returns an id token for another account, the pinned CLI still
  writes it under the old `account_id`. The claim check rejects that, and the volume
  keeps the staged document.
- Control: the same staged file mounted read-only (`--ro-bind-fd`) loses the refresh,
  and the volume keeps the staged document. The writable file is what carries a
  refresh back.

What OAuth mode adds, and does not prove:

- The pinned CLI also reads `GET /backend-api/wham/accounts/check` and
  `GET /backend-api/wham/settings/user` from the ChatGPT backend.
  - They offer no tool, and the checker allows exactly these two
    (`OAUTH_PROVIDER_PATHS`).
  - The fakes answer 404, so what a real backend returns, and whether that changes
    runtime behavior, is unmeasured. That is part of the restricted-egress gate.
- Over HTTP the CLI zstd-compresses request bodies to the ChatGPT backend. The fake
  decompresses with the `zstd` CLI, so the native OAuth tests need it on PATH.
- Real ChatGPT tokens, a real refresh grant and the real token endpoint were not used.
- The runtime holds a live, single-use refresh token. Model-driven reads of
  `$CODEX_HOME/auth.json`, and exfiltration through tool arguments or output, fall under
  the `filesystem_isolation` and `restricted_egress` gates, as the API key does.
- The account and claim check binds fields the document asserts about itself. It
  guards against confusion (a refresh for the wrong account), not malice. A hostile
  runtime can spend the refresh token and write back garbage, which is rejected but
  costs a re-login.


## Installed-image evidence export (build only)

The existing nightly `exact-image-sandbox` builder exports installed-image
assets before its separate Dashboard user-namespace policy/proof steps. The
exporter never starts OpenCode, Codex or the app entrypoint. A read-only,
network-none, capability-free container runs only the fixed Python helper as
an unprivileged UID, with one empty output mount, finite process/file/memory
limits and a 120-second outer watchdog. Its exact owned container ID is removed
before host verification or upload; failure leaves no success artifact upload.
Existing v3 generation, base fingerprints and Dashboard proof steps are unchanged.

The two-path artifact allowlist is `manifest.json` plus numbered `files/*.bin`.
The host first reconciles its complete population, sizes and hashes. It contains
only the v3 manifest, CLI build-version inventory, installed OpenCode package
metadata and selected native payload, fixed init shim, individually listed
regular loader/libraries, and fixed CA bundle. It omits resolver contents and
package directories. Original inode/link/ownership/mode and logical symlink
metadata remain in the report even though exported copies are regular files.
Metadata findings do not waive session-host ImageFile restrictions.

The report binds exact checkout/tree, workflow SHA, run/attempt/numeric job ID,
actual built image ID, unchanged base-input fingerprint, exporter/generator
hashes and pinned version inventory. ELF class, byte order, machine, interpreter
and DT_NEEDED are parsed from exported bytes. These facts establish neither
loader compatibility nor complete embedded/dlopen dependencies, CA consumption,
parser acceptance, model-visible tool removal or provider-wire behavior. Actual
artifacts require a successful future builder run. Goal-image assembly, native
execution and runtime conformance remain unfinished; the eligible tuple set
remains empty.
