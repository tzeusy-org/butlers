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
reachability is unimplemented. No real runtime CLI or model was launched and no
built-in or provider-hosted tool denial proof is claimed. Exact runtime/model/image/
settings tuples still need adversarial tool inventory and provider-wire proofs;
provider-hosted tools cannot be disabled by host network isolation alone.

The scoped CI-host profile experiment is specific to Noble ABI4 and does not
establish production deployment policy. It uses Canonical's purpose-built
[Bubblewrap guidance](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007)
and the [Noble package](https://packages.ubuntu.com/noble-updates/apparmor-profiles),
version `4.0.1really4.0.1-0ubuntu0.24.04.9`. The helper pins the published package
SHA256 and exact `bwrap-userns-restrict` member SHA256; the latter also defines the
necessary `unpriv_bwrap` child restriction. It leaves the global restriction enabled,
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
