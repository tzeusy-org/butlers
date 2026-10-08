#!/usr/bin/env python3
"""Finite host coordinator step; default is read-only, never a runtime bd bridge.

Apply requires the existing trusted coordinator's inherited lifecycle lock.
This flag/FD check serializes writes; it is not an authentication boundary.
Host credentials and permissions remain the existing coordinator premise.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from butlers.nightly_assurance import (
    EXPORT_PATH,
    REPOSITORY,
    VERSION,
    WORKFLOW,
    Assessment,
    EvidenceUnavailable,
    atomic_json,
    digest,
    latest_nights,
    second_red,
    validate_export,
)
from butlers.nightly_github import GitHubEvidence, parse_marker


class BeadsCoordinator:
    def __init__(self, *, apply: bool, owner: str | None) -> None:
        self.apply = apply
        self.owner = owner
        self.cluster_states: dict[str, dict] = {}

    def command(self, arguments: list[str], *, input_text: str | None = None) -> Any:
        mutation = arguments[0] in {"create", "update", "close", "dep"}
        if mutation and not self.apply:
            raise EvidenceUnavailable("readonly-coordinator")
        completed = subprocess.run(
            ["bd", *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode:
            # No raw error or credential-bearing CLI diagnostics retained.
            raise EvidenceUnavailable("beads-command-unacknowledged")
        if "--silent" in arguments:
            value = completed.stdout.strip()
            if not re.fullmatch(r"bu-[a-z0-9]+(?:\.[a-z0-9]+)*", value):
                raise EvidenceUnavailable("beads-create-unacknowledged")
            return value
        try:
            return json.loads(completed.stdout)
        except ValueError as exc:
            raise EvidenceUnavailable("beads-readback-unavailable") from exc

    def find(self, external_ref: str) -> dict | None:
        rows = self.command(
            [
                "list",
                "--all",
                "--limit",
                "0",
                "--max-rows",
                "2",
                "--external-ref",
                external_ref,
                "--json",
            ]
        )
        if not isinstance(rows, list) or len(rows) > 1:
            raise EvidenceUnavailable("nonunique-external-reference")
        return rows[0] if rows else None

    def show(self, bead_id: str) -> dict:
        row = self.command(["show", bead_id, "--json"])
        if isinstance(row, list) and len(row) == 1:
            row = row[0]
        if not isinstance(row, dict) or row.get("id") != bead_id:
            raise EvidenceUnavailable("beads-identity-readback-mismatch")
        return row

    @staticmethod
    def metadata(row: dict) -> dict:
        value = row.get("metadata", {})
        if not isinstance(value, dict):
            raise EvidenceUnavailable("invalid-beads-metadata")
        return value

    @staticmethod
    def contract(row: dict) -> dict:
        """Complete readback projection, excluding only this subsystem's mutable state."""
        metadata = row.get("metadata", {})
        if not isinstance(metadata, dict):
            raise EvidenceUnavailable("invalid-beads-metadata")
        return {
            key: value
            for key, value in row.items()
            if key not in {"status", "metadata", "updated_at", "closed_at", "closed_reason"}
        } | {"metadata": {key: value for key, value in metadata.items() if key != "nightly"}}

    def owned(self, row: dict) -> None:
        if not self.owner or row.get("assignee") != self.owner:
            raise EvidenceUnavailable("foreign-or-unknown-bead-owner")
        if row.get("status") not in {"open", "in_progress", "blocked", "closed"}:
            raise EvidenceUnavailable("unknown-bead-status")

    def guarded_update(self, before: dict, fields: list[str], *, status: str | None = None) -> dict:
        """Actual CLI owner/status CAS plus independent whole-contract/edge readback."""
        self.owned(before)
        fresh = self.show(before["id"])
        if fresh != before:
            raise EvidenceUnavailable("bead-changed-before-mutation")
        self.command(
            [
                "update",
                before["id"],
                "--if-assignee",
                self.owner,
                "--if-status",
                before["status"],
                *fields,
                "--json",
            ]
        )
        after = self.show(before["id"])
        if self.contract(after) != self.contract(before) or after.get("status") != (
            status or before["status"]
        ):
            raise EvidenceUnavailable("bead-contract-or-relations-changed")
        self.owned(after)
        return after

    def cluster(self, path: str, assessment: Assessment) -> str | None:
        if (
            not re.fullmatch(r"(?:tests|roster)/[A-Za-z0-9_./-]+\.py", path)
            or ".." in Path(path).parts
        ):
            raise EvidenceUnavailable("unsafe-node-owner-path")
        external_ref = f"nightly-file:{digest(path)}"
        existing = self.find(external_ref)
        expected = {
            "version": VERSION,
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "path": path,
        }
        if existing:
            existing = self.show(existing["id"])
            if (
                existing.get("external_ref") != external_ref
                or self.metadata(existing).get("nightly_cluster") != expected
            ):
                raise EvidenceUnavailable("foreign-or-unknown-file-cluster")
            assignee, status = existing.get("assignee"), existing.get("status")
            if not isinstance(assignee, str) or not assignee:
                raise EvidenceUnavailable("file-cluster-owner-unavailable")
            if status == "closed":
                self.owned(existing)
                if not self.apply:
                    raise EvidenceUnavailable("closed-file-cluster-requires-reconciliation")
                existing = self.guarded_update(existing, ["--status", "open"], status="open")
                status = "open"
            if status not in {"open", "in_progress", "blocked"}:
                raise EvidenceUnavailable("file-cluster-not-active")
            self.cluster_states[existing["id"]] = {
                "status": status,
                "assignee": assignee,
                "classification": "active-owned"
                if assignee == self.owner
                else "active-foreign-owned",
            }
            return existing["id"]
        if not self.apply:
            return None
        # Body is source coordinates only. No test messages/parameter values.
        description = (
            f"Nightly assurance requires ownership of {path}.\n\n## Steps to Reproduce\n"
            f"Read the sanitized evidence for run {assessment.identity.run_id}, attempt "
            f"{assessment.identity.attempt}, head {assessment.identity.head}. Preserve every "
            "existing invariant; diagnose the real phase before a scoped fix.\n"
        )
        bead_id = self.command(
            [
                "create",
                f"Repair nightly file cluster: {path}",
                "--type",
                "bug",
                "--priority",
                "1",
                "--assignee",
                self.owner or "",
                "--labels",
                "nightly-file-cluster",
                "--external-ref",
                external_ref,
                "--body-file",
                "-",
                "--metadata",
                json.dumps({"nightly_cluster": expected}),
                "--acceptance",
                "Causal old red and current positive; "
                "owning invariants and exact-hosted proof retained.",
                "--silent",
            ],
            input_text=description,
        )
        created = self.show(bead_id)
        self.owned(created)
        if (
            created.get("external_ref") != external_ref
            or created.get("status") != "open"
            or self.metadata(created).get("nightly_cluster") != expected
        ):
            raise EvidenceUnavailable("cluster-external-reference-readback-mismatch")
        self.cluster_states[bead_id] = {
            "status": "open",
            "assignee": self.owner,
            "classification": "active-owned",
        }
        return bead_id

    def reconcile(self, *, issue: int, assessment: Assessment, recover: bool) -> dict:
        external_ref = f"gh-issue:{issue}"
        existing = self.find(external_ref)
        if existing:
            existing = self.show(existing["id"])
            old = self.metadata(existing).get("nightly", {})
            self.owned(existing)
            if old.get("repository") != REPOSITORY or old.get("workflow") != WORKFLOW:
                raise EvidenceUnavailable("foreign-existing-incident")
        else:
            old = {}
        if recover:
            if existing and self.apply and existing.get("status") != "closed":
                # bd close lacks owner/status guards. A guarded status update
                # is restricted to an unpinned ordinary incident with NO edges;
                # it cannot bypass a dependency, dependent, gate or pinned close.
                if (
                    existing.get("pinned")
                    or existing.get("issue_type", existing.get("type")) in {"gate", "epic"}
                    or any(existing.get(key) for key in ("dependencies", "dependents", "blocks"))
                ):
                    raise EvidenceUnavailable("incident-recovery-relations-require-coordinator")
                existing = self.guarded_update(existing, ["--status", "closed"], status="closed")
                if existing.get("status") != "closed":
                    raise EvidenceUnavailable("recovery-readback-unavailable")
            return {
                "incident_id": existing["id"] if existing else None,
                "status": "recovered",
                "episode_id": old.get("episode_id"),
                "owners": old.get("owners", {}),
            }
        owners = dict(old.get("owners", {}))
        self.cluster_states.clear()
        for node in owners:
            path = node.split(":", 1)[1].split("::", 1)[0]
            owners[node] = self.cluster(path, assessment)
        for node in assessment.failures:
            path = node.split(":", 1)[1].split("::", 1)[0]
            owners[node] = self.cluster(path, assessment)
        episode = old.get("episode_id") if existing and existing.get("status") != "closed" else None
        episode = episode or digest(
            {
                "issue": issue,
                "night": assessment.identity.night,
                "run_id": assessment.identity.run_id,
                "key": assessment.key,
            }
        )
        metadata = {
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "episode_id": episode,
            "key": assessment.key,
            "owners": owners,
            "owner_states": dict(self.cluster_states),
            "assessment": assessment.document(),
        }
        if not self.apply:
            return {
                "incident_id": existing["id"] if existing else None,
                "episode_id": episode,
                "status": "planned",
                "owners": owners,
            }
        if existing is None:
            # Create uses a single globally unique external_ref. Re-entry
            # always finds it before creating, including a lost CLI ACK.
            bead_id = self.command(
                [
                    "create",
                    "Own the second consecutive red nightly assurance run",
                    "--type",
                    "bug",
                    "--priority",
                    "1",
                    "--assignee",
                    self.owner or "",
                    "--labels",
                    "nightly-incident",
                    "--external-ref",
                    external_ref,
                    "--body-file",
                    "-",
                    "--acceptance",
                    "Repair all owned clusters and verify full recovery; "
                    "retain incomplete evidence honestly.",
                    "--metadata",
                    json.dumps({"nightly": metadata}),
                    "--silent",
                ],
                input_text=(
                    "Second consecutive verified red UTC scheduled night.\n\n"
                    "## Steps to Reproduce\nRead the immutable sanitized run artifacts "
                    f"for {assessment.identity.run_id}/{assessment.identity.attempt}.\n"
                ),
            )
        else:
            bead_id = existing["id"]
            arguments = ["--set-metadata", f"nightly={json.dumps(metadata)}"]
            status = existing["status"]
            if status == "closed":
                arguments += ["--status", "open"]
                status = "open"
            self.guarded_update(existing, arguments, status=status)
        readback = self.show(bead_id)
        self.owned(readback)
        stored = self.metadata(readback).get("nightly", {})
        if (
            readback.get("external_ref") != external_ref
            or stored != metadata
            or readback.get("status") not in {"open", "in_progress", "blocked"}
        ):
            raise EvidenceUnavailable("incident-readback-unavailable")
        return {"incident_id": bead_id, "episode_id": episode, "status": "open", "owners": owners}


def verify_coordinator_lock(fd: int, root: Path) -> None:
    if fd < 3:
        raise EvidenceUnavailable("missing-coordinator-lock")
    expected = root / ".beads.gate.lock"
    metadata, actual = expected.stat(follow_symlinks=False), os.fstat(fd)
    if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
        metadata.st_dev,
        metadata.st_ino,
    ):
        raise EvidenceUnavailable("invalid-coordinator-lock")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def reconcile(
    github: GitHubEvidence, beads: BeadsCoordinator, *, export: Path, receipt: Path
) -> None:
    assessments = []
    for run in github.runs():
        if run.get("event") == "schedule" and run.get("head_branch") == "main":
            assessments.append(github.read_assessment(run["id"]))
    nights = latest_nights(assessments)
    if not nights:
        raise EvidenceUnavailable("scheduled-history-unavailable")
    current = nights[-1]
    incidents, actions = [], []
    if second_red(assessments, current):
        if beads.apply:
            # A killed/broken CI assessor cannot suppress escalation. The
            # trusted host reconstructs terminal evidence independently.
            github.upsert_issue(current)
        else:
            actions.append({"status": "transport-reconciliation-required", "key": current.key})
    for issue in github.issues():
        if issue.get("pull_request"):
            continue
        try:
            marker = parse_marker(issue.get("body"))
        except EvidenceUnavailable:
            continue
        if marker["scope"] != "scheduled":
            continue
        bound = github.read_assessment(marker["run_id"])
        if (
            bound.identity.attempt != marker["attempt"]
            or bound.identity.head != marker["head"]
            or bound.key != marker["key"]
        ):
            continue  # stale/forged candidate never authorizes a command
        recover = current.state == "green" and current.identity.night >= bound.identity.night
        if not recover and not second_red(assessments, bound):
            continue
        action = beads.reconcile(issue=issue["number"], assessment=bound, recover=recover)
        actions.append(action)
        if action["incident_id"] and action["episode_id"]:
            incidents.append(
                {
                    "incident_id": action["incident_id"],
                    "episode_id": action["episode_id"],
                    "status": "recovered" if recover else "open",
                    "issue": issue["number"],
                    "run_id": bound.identity.run_id,
                    "attempt": bound.identity.attempt,
                    "head": bound.identity.head,
                    "night": bound.identity.night,
                    "failure_digest": bound.failure_digest,
                }
            )
        if recover and beads.apply:
            github.upsert_issue(current, recover=True)
    document = {
        "version": VERSION,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "as_of": datetime.now(UTC).isoformat(),
        "incidents": incidents,
    }
    validate_export(document, now=datetime.now(UTC))
    if beads.apply:
        atomic_json(export, document)
    atomic_json(
        receipt,
        {
            "mode": "applied" if beads.apply else "readonly-plan",
            "actions": actions,
            "current": current.document(),
            "remaining_requests": github.remaining,
        },
    )


def reconcile_conformance(
    github: GitHubEvidence, beads: BeadsCoordinator, *, run_id: int, export: Path, receipt: Path
) -> None:
    """Explicit trusted-host branch episode; never modifies scheduled streaks.

    The actual canary is independently fetched and must be a terminal branch
    workflow_dispatch in this same nightly workflow. Production export is not
    writable through this mode; root supplies a disposable observation door.
    """
    if export.resolve() == EXPORT_PATH:
        raise EvidenceUnavailable("conformance-requires-separate-export")
    current = github.read_assessment(run_id)
    if current.identity.event != "workflow_dispatch" or not current.identity.ref.startswith(
        "agent/"
    ):
        raise EvidenceUnavailable("not-branch-conformance")
    if current.state not in {"red", "green"} or not current.completed:
        raise EvidenceUnavailable("conformance-not-terminal")
    if current.state == "red" and beads.apply:
        github.upsert_issue(current)
    incidents, actions = [], []
    for issue in github.issues():
        if issue.get("pull_request"):
            continue
        try:
            marker = parse_marker(issue.get("body"))
        except EvidenceUnavailable:
            continue
        if marker["scope"] != current.identity.scope:
            continue
        bound = github.read_assessment(marker["run_id"])
        if (
            bound.identity.attempt != marker["attempt"]
            or bound.identity.head != marker["head"]
            or bound.key != marker["key"]
            or bound.identity.scope != current.identity.scope
        ):
            continue
        recovery = current.state == "green"
        if not recovery and bound.key != current.key:
            continue
        action = beads.reconcile(issue=issue["number"], assessment=bound, recover=recovery)
        actions.append(action)
        if action["incident_id"] and action["episode_id"]:
            incidents.append(
                {
                    "incident_id": action["incident_id"],
                    "episode_id": action["episode_id"],
                    "status": "recovered" if recovery else "open",
                    "issue": issue["number"],
                    "run_id": bound.identity.run_id,
                    "attempt": bound.identity.attempt,
                    "head": bound.identity.head,
                    "night": bound.identity.night,
                    "failure_digest": bound.failure_digest,
                }
            )
    if current.state == "green" and beads.apply:
        github.upsert_issue(current, recover=True)
    document = {
        "version": VERSION,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "as_of": datetime.now(UTC).isoformat(),
        "incidents": incidents,
    }
    validate_export(document, now=datetime.now(UTC))
    if beads.apply:
        atomic_json(export, document)
    atomic_json(
        receipt,
        {
            "mode": "applied-conformance" if beads.apply else "readonly-conformance",
            "scheduled_night_credit": False,
            "actions": actions,
            "current": current.document(),
            "remaining_requests": github.remaining,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinator-apply", action="store_true")
    parser.add_argument("--coordinator-lock-fd", type=int)
    parser.add_argument("--coordinator-owner")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--export", type=Path, default=EXPORT_PATH)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--conformance-run",
        type=int,
        help="Explicit terminal branch canary, never a scheduled night",
    )
    args = parser.parse_args()
    if args.coordinator_apply:
        if not args.coordinator_owner or args.coordinator_lock_fd is None:
            parser.error(
                "apply requires the existing coordinator owner and inherited lifecycle lock"
            )
        verify_coordinator_lock(args.coordinator_lock_fd, args.repo_root)
    try:
        observer = GitHubEvidence()
        coordinator = BeadsCoordinator(apply=args.coordinator_apply, owner=args.coordinator_owner)
        if args.conformance_run is not None:
            reconcile_conformance(
                observer,
                coordinator,
                run_id=args.conformance_run,
                export=args.export,
                receipt=args.receipt,
            )
        else:
            reconcile(observer, coordinator, export=args.export, receipt=args.receipt)
    except (EvidenceUnavailable, OSError, subprocess.TimeoutExpired):
        atomic_json(
            args.receipt,
            {
                "status": "unavailable",
                "mode": "applied" if args.coordinator_apply else "readonly-plan",
                "acknowledgement": "unknown",
            },
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
