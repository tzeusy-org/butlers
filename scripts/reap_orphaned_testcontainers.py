#!/usr/bin/env python3
"""
reap_orphaned_testcontainers.py

Identify (and optionally remove) Docker containers left behind by pytest runs
that died before their testcontainers teardown could execute.

Why this exists (bu-3zu5l)
--------------------------
Integration tests under ``tests/api/*_db.py``, ``tests/migrations/`` and
``tests/config/`` start an ephemeral ``pgvector/pgvector:pg17`` container per
pytest *process* via testcontainers. With ``-n 3 --dist loadfile`` in
``pyproject.toml`` addopts, one ``make test`` is three worker processes, each
with its own container. A run that is SIGKILLed (agent timeout, ctrl-c, OOM)
never reaches the fixture finalizer, so the container stays up forever, holding
RAM and a published port.

testcontainers already ships the correct machinery for this: the Ryuk sidecar.
Ryuk is NOT disabled in this repo (there is no ``TESTCONTAINERS_RYUK_DISABLED``
anywhere, and no ``~/.testcontainers.properties``), and it does run: every
pytest process that starts a container first starts
``testcontainers-ryuk-<SESSION_ID>``, holds a TCP socket open to it, and Ryuk
removes everything labelled with that session id shortly after the socket
drops. **This script is not a replacement for Ryuk.** It only handles the
residue Ryuk cannot: containers whose Ryuk itself died without reaping (Ryuk
runs with ``auto_remove=True`` and nothing ever calls
``Reaper.delete_instance()``, so a Ryuk that exits early leaves no trace and no
second chance).

Identification rule
-------------------
Age is deliberately NOT the signal. A three-week-old container can be a live
investigation, and a ten-minute-old one can already be garbage. A container is
reapable only when EVERY one of these holds:

1. ``org.testcontainers=true`` and a non-empty ``org.testcontainers.session-id``
   label. Only the testcontainers library stamps these, so a container someone
   started by hand can never qualify. (This alone spares
   ``codex-pr3708-acl-repro-11668``, which carries no labels at all.)
2. No ``com.docker.compose.*`` label: never touch a compose stack such as the
   ``butlers-dev-*`` dev stack.
3. No ``dev.butlers.keep`` label: an explicit human opt-out, honoured for any
   value.
4. The name has the shape Docker's own name generator produces
   (``lowercase_words``, no digits, no hyphens). Anything else means a human
   passed ``--name``, which is provenance, not noise.
5. **No running ``testcontainers-ryuk-<session-id>`` container for that session
   id.** This is the load-bearing predicate: a live pytest session always has a
   live Ryuk, so a missing Ryuk is positive evidence that the owning session is
   gone, not an inference from the clock.
6. Older than ``--min-age-hours`` (default 4, comfortably past the ~40 minute
   full backend gate). This is a backstop, not the signal: it covers the one
   case where predicate 5 lies, namely a live run launched with
   ``TESTCONTAINERS_RYUK_DISABLED=true``.

Safety argument
---------------
The predicates are conjunctive, and each one on its own is sufficient to spare
a container someone wants. Anything a human named, anything in a compose
project, anything explicitly pinned, and anything whose owning test session is
still alive is excluded before age is even consulted. Every failure mode of
every predicate (missing label, unparsed inspect output, a ``docker`` call that
errors) resolves to "not reapable", so the script's way of being wrong is to
leave an orphan running, never to kill something live. Removal is opt-in
(``--reap``); the default run only reports.

Usage:
  python3 scripts/reap_orphaned_testcontainers.py            # report only
  python3 scripts/reap_orphaned_testcontainers.py --json     # machine readable
  python3 scripts/reap_orphaned_testcontainers.py --reap     # remove candidates
  python3 scripts/reap_orphaned_testcontainers.py --min-age-hours 24

Exit codes:
  0  No orphan candidates (or ``--reap`` removed them all).
  1  Orphan candidates exist and were only reported (no ``--reap``).
  2  Docker is unavailable or its output could not be parsed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

LABEL_TESTCONTAINERS = "org.testcontainers"
LABEL_SESSION_ID = "org.testcontainers.session-id"
LABEL_KEEP = "dev.butlers.keep"
COMPOSE_LABEL_PREFIX = "com.docker.compose."
RYUK_NAME_PREFIX = "testcontainers-ryuk-"

# Docker's namesgenerator emits "adjective_surname"; a few surnames are
# themselves underscore-joined (e.g. "van_kleeck"), so allow up to three
# lowercase alphabetic tokens. Digits and hyphens never appear, which is what
# makes a name like "codex-pr3708-acl-repro-11668" self-evidently human.
GENERATED_NAME_RE = re.compile(r"^[a-z]+(?:_[a-z]+){1,2}$")

DEFAULT_MIN_AGE_HOURS = 4.0


@dataclass(frozen=True)
class Container:
    """The subset of ``docker inspect`` this script reasons about."""

    id: str
    name: str
    image: str
    created: datetime | None
    labels: dict[str, str]


@dataclass(frozen=True)
class Verdict:
    """Why a container is or is not safe to remove."""

    reapable: bool
    reasons: tuple[str, ...] = ()


def parse_created(raw: str) -> datetime | None:
    """Parse a Docker ``.Created`` RFC3339 timestamp, tolerating nanoseconds."""
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python's parser accepts at most 6 fractional digits; Docker emits 9.
    match = re.match(r"^(.*\.\d{6})\d*(\+\d{2}:\d{2})$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def classify(
    container: Container,
    *,
    live_ryuk_session_ids: set[str],
    now: datetime,
    min_age_hours: float,
) -> Verdict:
    """Decide whether ``container`` is a provably unowned test container.

    Reasons are accumulated rather than short-circuited so a report can explain
    every protection that applied, which is what makes the output auditable.
    """
    reasons: list[str] = []

    labels = container.labels
    session_id = labels.get(LABEL_SESSION_ID, "")

    if labels.get(LABEL_TESTCONTAINERS) != "true" or not session_id:
        reasons.append("not created by testcontainers (no session-id label)")
    if any(key.startswith(COMPOSE_LABEL_PREFIX) for key in labels):
        reasons.append("belongs to a docker compose project")
    if LABEL_KEEP in labels:
        reasons.append(f"pinned by {LABEL_KEEP} label")
    if not GENERATED_NAME_RE.match(container.name):
        reasons.append("name was chosen by a human, not Docker's name generator")
    if session_id and session_id in live_ryuk_session_ids:
        reasons.append("owning pytest session is alive (its Ryuk sidecar is running)")

    if container.created is None:
        reasons.append("creation time could not be parsed")
    else:
        age_hours = (now - container.created).total_seconds() / 3600.0
        if age_hours < min_age_hours:
            reasons.append(f"younger than the {min_age_hours:g}h backstop ({age_hours:.1f}h)")

    if reasons:
        return Verdict(reapable=False, reasons=tuple(reasons))
    return Verdict(
        reapable=True,
        reasons=(
            f"testcontainers session {session_id} has no running Ryuk sidecar, "
            "Docker-generated name, no compose project, past the age backstop",
        ),
    )


def live_ryuk_session_ids(names: list[str]) -> set[str]:
    """Extract session ids from running ``testcontainers-ryuk-<id>`` names."""
    return {
        name[len(RYUK_NAME_PREFIX) :]
        for name in names
        if name.startswith(RYUK_NAME_PREFIX) and len(name) > len(RYUK_NAME_PREFIX)
    }


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


_INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"created":{{json .Created}},'
    '"image":{{json .Config.Image}},"labels":{{json .Config.Labels}}}'
)


def running_container_names() -> list[str]:
    return [line for line in _run(["docker", "ps", "--format", "{{.Names}}"]).splitlines() if line]


def inspect_containers(names: list[str]) -> list[Container]:
    """Inspect ``names`` and return the fields this script reasons about."""
    if not names:
        return []
    out = _run(["docker", "inspect", "--format", _INSPECT_FORMAT, *names])
    containers: list[Container] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        containers.append(
            Container(
                id=raw.get("id") or "",
                # docker inspect reports names with a leading slash.
                name=(raw.get("name") or "").lstrip("/"),
                image=raw.get("image") or "",
                created=parse_created(raw.get("created") or ""),
                labels=raw.get("labels") or {},
            )
        )
    return containers


def build_report(
    containers: list[Container],
    *,
    ryuk_session_ids: set[str],
    now: datetime,
    min_age_hours: float,
) -> list[tuple[Container, Verdict]]:
    return [
        (
            container,
            classify(
                container,
                live_ryuk_session_ids=ryuk_session_ids,
                now=now,
                min_age_hours=min_age_hours,
            ),
        )
        for container in containers
    ]


def remove_container(container_id: str) -> None:
    _run(["docker", "rm", "--force", "--volumes", container_id])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report or remove Docker containers left behind by dead pytest runs."
    )
    parser.add_argument(
        "--reap",
        action="store_true",
        help="remove the candidates instead of only reporting them",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=f"age backstop in hours (default {DEFAULT_MIN_AGE_HOURS:g})",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of prose")
    args = parser.parse_args(argv)

    try:
        names = running_container_names()
        containers = inspect_containers(names)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"docker inspection failed: {exc}", file=sys.stderr)
        return 2

    report = build_report(
        containers,
        ryuk_session_ids=live_ryuk_session_ids(names),
        now=datetime.now(UTC),
        min_age_hours=args.min_age_hours,
    )
    candidates = [(container, verdict) for container, verdict in report if verdict.reapable]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": container.id,
                        "name": container.name,
                        "image": container.image,
                        "session_id": container.labels.get(LABEL_SESSION_ID),
                        "reapable": verdict.reapable,
                        "reasons": list(verdict.reasons),
                    }
                    for container, verdict in report
                ],
                indent=2,
            )
        )
    else:
        if not candidates:
            print(f"No orphaned test containers among {len(containers)} running containers.")
        for container, verdict in candidates:
            print(f"{container.name} ({container.id[:12]}, {container.image})")
            for reason in verdict.reasons:
                print(f"    {reason}")

    if not candidates:
        return 0
    if not args.reap:
        print(
            f"\n{len(candidates)} orphan candidate(s). Re-run with --reap to remove them.",
            file=sys.stderr,
        )
        return 1

    for container, _ in candidates:
        remove_container(container.id)
        print(f"removed {container.name} ({container.id[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
