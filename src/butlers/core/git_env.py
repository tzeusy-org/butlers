"""Environments for dispatcher git subprocesses that run in an agent-writable repository.

QA and healing worktrees are linked worktrees of the daemon's clone, so an investigation agent
can write the shared ``.git/hooks`` and ``.git/config``.  Every dispatcher git subprocess in that
clone therefore runs with a minimal allowlisted environment (no daemon secrets) and with
command-line config (``GIT_CONFIG_COUNT``), which beats every file scope, switching off the
config keys that execute commands or consume credentials.

``remote.<name>.uploadpack``/``receivepack`` cannot be overridden by config (git keeps the first
value it sees), so dispatcher fetches and pushes to a *named* remote must pass
:data:`FETCH_COMMAND_ARGS` / :data:`PUSH_COMMAND_ARGS` explicitly.
"""

from __future__ import annotations

import os

#: Variables a dispatcher git subprocess may inherit.  Anything else (``GH_TOKEN``,
#: ``DATABASE_URL``, API keys, ``BUTLERS_*``) never reaches a git child.
_GIT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)

#: Pin the remote helper of a named-remote fetch; a planted ``remote.<name>.uploadpack`` is ignored.
FETCH_COMMAND_ARGS: tuple[str, ...] = ("--upload-pack=git-upload-pack",)

#: Pin the remote helper of a named-remote push; a planted ``remote.<name>.receivepack`` is ignored.
PUSH_COMMAND_ARGS: tuple[str, ...] = ("--receive-pack=git-receive-pack",)


def allowlisted_git_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return the allowlisted subset of *source* (default ``os.environ``)."""
    env = os.environ if source is None else source
    return {key: value for key, value in env.items() if key in _GIT_ENV_ALLOWLIST}


def harden_git_env(env: dict[str, str]) -> dict[str, str]:
    """Return *env* with command-line git config that agent-writable config cannot override.

    Neutralised: hooks, fsmonitor, credential helpers, ``core.sshCommand``/``core.askPass`` and
    the ``ext::`` and ``git://`` transports (``protocol.ext.allow``/``protocol.git.allow``).
    System and global config are disabled.  Remaining file-scoped config (for example
    ``url.*.insteadOf`` or ``filter.*``) is why callers must keep the GitHub token out of git
    subprocesses.
    """
    hardened = dict(env)
    settings: list[tuple[str, str]] = [
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("credential.helper", ""),
        ("core.sshCommand", "ssh"),
        ("core.askPass", "/bin/false"),
        # Origin is https; ext:: and git:// (core.gitProxy) transports run commands or dial out.
        ("protocol.ext.allow", "never"),
        ("protocol.git.allow", "never"),
    ]
    hardened["GIT_CONFIG_NOSYSTEM"] = "1"
    hardened["GIT_CONFIG_GLOBAL"] = "/dev/null"
    hardened["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        hardened[f"GIT_CONFIG_KEY_{index}"] = key
        hardened[f"GIT_CONFIG_VALUE_{index}"] = value
    # The environment also beats config for these, so a planted value is moot twice over.
    hardened["GIT_SSH_COMMAND"] = "ssh"
    hardened.setdefault("GIT_ASKPASS", "/bin/false")
    hardened["GIT_TERMINAL_PROMPT"] = "0"
    return hardened


def isolated_git_env() -> dict[str, str]:
    """Allowlisted, hardened environment for a tokenless dispatcher git subprocess."""
    return harden_git_env(allowlisted_git_env())
