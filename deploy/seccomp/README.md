# Dashboard runtime CLI sandbox seccomp policy

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
