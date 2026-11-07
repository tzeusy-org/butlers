# Dark session-host foundation

`butlers.session_host` is a private, unmounted library for RFC0038 D3 sandboxing
and authenticated local dispatch. It has no production entrypoint, deployment,
provider profile or imports from the existing daemon, runner or adapters. Importing
it starts no listener or process. It does not make any runtime/model tuple eligible.
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

`LocalControl.start()` binds only `127.0.0.1` on an ephemeral port. This is a dark
prototype control channel, not a production listener or a dashboard endpoint.
One connection carries one JSON object, prefixed by its four-byte big-endian
length. The aggregate limit is 280 KiB and the read/write deadlines are 3 seconds.
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
local channel permits one active launch and also keeps an in-memory replay fence.
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
runner/broker integration, restricted gateway and deployment, OAuth/Codex/dashboard
credential-volume retargeting and tool-less model functions remain separate gates.
Existing Engineering/QA adapter behavior is unchanged. This child cannot complete
parent AC1/3/4/5/6/7 or authorize activation; native evidence can support only the
bounded dark-library portion of AC2 after hosted verification.

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

Five of the nine measured checks show absence, not enforcement:

- `provider_search_disabled`, `provider_fetch_disabled`, `provider_remote_mcp_disabled`
  and `provider_code_disabled` pass when no hosted tool appears anywhere in the client
  request. Hosted tools run at the provider, so no hostile call can test them here,
  and a provider that ran one anyway would not show up.
- `builtin_web_disabled` refuses a hostile `web_search` call. The pinned CLI has no
  local web function, so it refuses that call whatever the config says, and no
  native mutation can make this check fail. Hosted search is covered only by
  `provider_search_disabled`.

The other four (`builtin_shell_disabled`, `builtin_filesystem_disabled`,
`local_mcp_disabled`, `broker_only_mcp`) enforce. Each has a native mutation that
makes it fail. They send hostile calls and require each one to be refused, or the
broker call to complete.

The tuple stays ineligible. `ReviewedProfile` requires 13 checks. The proof measures
nine and reports the other four as `unproven`, which admission refuses:

| Check | What remains | Who can run it |
| --- | --- | --- |
| `restricted_egress` | The domain has no route out, so a real launch cannot reach a provider or broker yet. Needs the gateway and the dev network proof (`bu-kiuh8v.4`). | Dev deployment |
| `filesystem_isolation`, `process_isolation` | Native tests cover synthetic payloads, not this runtime in the installed image. | Exact-image run (Docker denied on agent hosts) |
| `catalog_conformance` | RFC 0027 gate at the real catalog size, with native discovery (`bu-r980fk`). | Broker work |

Also unproven: a real provider honoring the request (no real calls were made, so
server-side behavior is outside this proof), and the bundled TLS trust path. Three
MCP resource tools remain offered because the pinned CLI has no switch for them.
They can only reach configured MCP servers, so the broker must answer them safely.


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
