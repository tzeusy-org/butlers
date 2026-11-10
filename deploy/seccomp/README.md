# Runtime sandbox seccomp policies

## Dashboard runtime CLI sandbox

`dashboard-runtime-cli-sandbox.json` is a pinned copy of Moby v25.0.0's
default seccomp profile, whose upstream SHA-256 is
`6f3b980368d6256756adaf30611640bd68dce843101eefa65dd76b8e44cf3790`.
It retains Moby's default-deny baseline for the Dashboard process and adds
only the exact Bubblewrap 0.11 bootstrap syscalls this image needs before the
new user namespace gains its own capabilities:

- `clone(CLONE_NEWNS|CLONE_NEWUTS|CLONE_NEWIPC|CLONE_NEWUSER|CLONE_NEWPID|SIGCHLD)`
- `unshare(CLONE_NEWUSER)` for the v0.11 second-level user namespace reached
  by the current `--block-fd` and leased-UID/GID launch flow
- `mount`, `pivot_root`, and `umount2`

It does not add a broad `unshare`, `setns`, `clone3`, or `chroot` allowance,
and it does not add `CAP_SYS_ADMIN` to either Dashboard service.  The
`unshare` rule is argument-filtered to `CLONE_NEWUSER`; other calls remain
under Moby's existing capability-gated rules when they are otherwise needed
inside the Bubblewrap child namespace.

The policy is applied only to `dashboard-api` and
`dashboard-api-hotreload`.  It is deliberately not applied to Butler daemon
or restore-drill services.

Upstream source: <https://raw.githubusercontent.com/moby/moby/v25.0.0/profiles/seccomp/default.json>.

## Session host sandbox

`session-host-sandbox.json` (the pod profile) is the dashboard profile above
with exactly one delta (bu-kiuh8v.9), pinned by
`tests/config/test_session_host_seccomp_profile.py`: the namespace `clone` rule
adds `CLONE_NEWNET`:
`clone(CLONE_NEWNS|CLONE_NEWUTS|CLONE_NEWIPC|CLONE_NEWUSER|CLONE_NEWPID|CLONE_NEWNET|SIGCHLD)`,
compared for equality. The session host's Bubblewrap plan passes
`--unshare-net`, and the test derives the mask from that plan's `--unshare-*`
flags, so a new namespace flag fails it. The pod profile allows no `setns`:
the launch never calls it.

Everything else is unchanged:

- `unshare` is limited to `CLONE_NEWUSER`, for the nested namespace behind
  `--disable-userns`.
- `mount`, `pivot_root` and `umount2` are allowed, for `--tmpfs`, `--proc`,
  `--dev` and the binds inside the new user namespace.
- There is still no broad `unshare` or `setns`, no `clone3` (it answers
  `ENOSYS`, so libc falls back to the filtered `clone`), no `chroot`, and no
  `CAP_SYS_ADMIN`.
- The static init shim needs only syscalls the Moby baseline already allows
  (`close_range`, `openat`, `getdents64`, `read`, `write`, `execve`).

Seccomp is one of three container settings Bubblewrap needs. The other two are
an unmasked `/proc`, because `--proc` cannot mount over masked paths
(`--security-opt systempaths=unconfined` in Docker), and AppArmor that allows
the user namespace (`apparmor=unconfined` in the proof). This profile replaces
only `seccomp=unconfined`.

Compose applies it to `session-host`. The Helm chart applies it as a
`Localhost` profile when `sessionHost.seccompLocalhostProfile` names its
installed path under the kubelet seccomp root; the default leaves the pod
unchanged.

### Exact-image proof profile

`session-host-proof.json` is the pod profile plus exactly two rules, and is
used only by the owner's exact-image proof
(`tests/session_host/exact_image_codex_proof.py`); no deployment names it.
The rules allow `setns` when its `nstype` argument is exactly `CLONE_NEWUSER`
or exactly `CLONE_NEWNET`. Joining a user namespace still needs
`CAP_SYS_ADMIN` in it, so a process can enter only namespaces its own uid
created. The proof uses this to put its fake listeners inside the domain's
network namespace. The same test file pins it to the pod profile plus those
two rules and nothing else.

## Dashboard on Kubernetes

The Helm chart does not use the dashboard profile: it runs `dashboard-api` `privileged` because the
user-namespace alternative (`hostUsers: false`, `procMount: Unmasked`, seccomp `Localhost` pointing
at this file installed under the kubelet seccomp root) failed on the current k3s node, where the
overlayfs snapshotter lacks idmapped-mount support (kernel 5.15). See "Security posture
differences from compose" in `docs/operations/kubernetes-deployment.md` for the probe matrix.
