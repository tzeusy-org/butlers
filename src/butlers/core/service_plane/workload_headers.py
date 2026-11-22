"""Shared headers and projection path for workload-authenticated requests."""

from pathlib import Path

#: Projected token, separate from the broker's X-Butlers-Assertion.
TOKEN_HEADER = "x-butlers-workload-token"
#: Claimed daemon narrows the verified workload's permissions.
DAEMON_HEADER = "x-butlers-workload-daemon"
#: Each projected audience has its own file in this directory.
TOKEN_DIR = Path("/var/run/secrets/butlers/workload")
