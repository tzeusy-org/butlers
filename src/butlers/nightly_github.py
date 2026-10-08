"""Bounded, allowlisted GitHub evidence transport using the existing gh identity.

No caller-supplied URL, executable, repository or command is accepted. Raw job
logs are never fetched. ZIP contents are read in memory with exact filenames.
"""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.nightly_assurance import (
    MAX_EVIDENCE_BYTES,
    REPOSITORY,
    VARIANTS,
    WORKFLOW,
    Assessment,
    EvidenceUnavailable,
    RunIdentity,
    assess,
    identity_from_github,
    positive_integer,
)

LABEL = "nightly-assurance"
MARKER = "<!-- butlers-nightly-v1\n"


class GitHubEvidence:
    """One finite API budget for a host/CI observation, with bounded calls."""

    def __init__(self, *, request_limit: int = 160) -> None:
        self.remaining = request_limit
        self._assessments: dict[tuple, Assessment] = {}

    def request(
        self, suffix: str, *, method: str = "GET", body: dict | None = None, binary: bool = False
    ) -> Any:
        self.remaining -= 1
        if self.remaining < 0:
            raise EvidenceUnavailable("github-request-budget")
        arguments = ["gh", "api", f"repos/{REPOSITORY}/{suffix}", "--method", method]
        if body is not None:
            arguments += ["--input", "-"]
        response = subprocess.run(
            arguments,
            input=json.dumps(body).encode() if body else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        if response.returncode or len(response.stdout) > MAX_EVIDENCE_BYTES:
            raise EvidenceUnavailable("github-api-unavailable")
        if binary:
            return response.stdout
        try:
            return json.loads(response.stdout)
        except (ValueError, UnicodeError) as exc:
            raise EvidenceUnavailable("github-invalid-json") from exc

    def pages(self, suffix: str, key: str | None, *, max_pages: int = 20) -> list[dict]:
        result = []
        for page in range(1, max_pages + 1):
            separator = "&" if "?" in suffix else "?"
            document = self.request(f"{suffix}{separator}per_page=100&page={page}")
            rows = document[key] if key else document
            if not isinstance(rows, list):
                raise EvidenceUnavailable("github-invalid-page")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise EvidenceUnavailable("github-pagination-incomplete")

    def runs(self, *, days: int = 14) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
        return self.pages(f"actions/workflows/nightly.yml/runs?created=>={cutoff}", "workflow_runs")

    def read_run(self, run_id: int) -> dict:
        return self.request(f"actions/runs/{positive_integer(run_id)}")

    def read_assessment(self, run_id: int, *, provisional: bool = False) -> Assessment:
        run = self.read_run(run_id)
        identity = identity_from_github(run)
        if not provisional and run.get("status") != "completed":
            raise EvidenceUnavailable("workflow-not-terminal")
        cache_key = (identity, run.get("status"), run.get("conclusion"), run.get("updated_at"))
        if cache_key in self._assessments:
            return self._assessments[cache_key]
        jobs = self.pages(
            f"actions/runs/{run_id}/attempts/{identity.attempt}/jobs?filter=all", "jobs"
        )
        artifacts = self.pages(f"actions/runs/{run_id}/artifacts", "artifacts")
        evidence = {}
        for variant in VARIANTS:
            name = f"nightly-{run_id}-{identity.attempt}-{variant}"
            matches = [
                row for row in artifacts if row.get("name") == name and not row.get("expired")
            ]
            if len(matches) != 1:
                continue
            try:
                body = self.request(
                    f"actions/artifacts/{positive_integer(matches[0]['id'])}/zip", binary=True
                )
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    if len(archive.infolist()) > 5:
                        raise EvidenceUnavailable("invalid-artifact-population")
                    if any(item.file_size > MAX_EVIDENCE_BYTES for item in archive.infolist()):
                        raise EvidenceUnavailable("oversized-artifact")
                    if set(archive.namelist()) != {"receipt.json", "junit.xml", "durations.txt"}:
                        raise EvidenceUnavailable("invalid-artifact-files")
                    receipt = json.loads(archive.read("receipt.json"))
                    import hashlib

                    if hashlib.sha256(archive.read("junit.xml")).hexdigest() != receipt.get(
                        "junit_sha256"
                    ):
                        raise EvidenceUnavailable("junit-digest-mismatch")
                    evidence[variant] = receipt
            except (EvidenceUnavailable, KeyError, ValueError, zipfile.BadZipFile):
                # Keep every other genuine failure; do not turn one bad
                # artifact into a manufactured all-clear.
                continue
        # New workflows register both folded variants; historical workflows
        # without either job remain the original four-species population.
        folded = any(job.get("name", "").startswith("folded-clock (") for job in jobs)
        assessment = assess(
            identity,
            official_run=run,
            official_jobs=jobs,
            evidence=evidence,
            folded_required=folded,
        )
        if assessment.completed:
            self._assessments[cache_key] = assessment
        return assessment

    def issues(self) -> list[dict]:
        return self.pages(f"issues?state=all&labels={LABEL}", None)

    def _ensure_label(self) -> None:
        labels = self.pages("labels", None)
        if not any(isinstance(label, dict) and label.get("name") == LABEL for label in labels):
            self.request(
                "labels",
                method="POST",
                body={
                    "name": LABEL,
                    "color": "b60205",
                    "description": "Sanitized nightly assurance evidence for host triage",
                },
            )
        label = self.request(f"labels/{LABEL}")
        if not isinstance(label, dict) or label.get("name") != LABEL:
            raise EvidenceUnavailable("incident-label-unacknowledged")

    def _issue_readback(
        self,
        number: int,
        assessment: Assessment,
        *,
        recovery: Assessment | None = None,
        closed: bool = False,
        canonical_issue: int | None = None,
    ) -> int:
        row = self.request(f"issues/{positive_integer(number)}")
        if (
            not isinstance(row, dict)
            or row.get("number") != number
            or row.get("pull_request")
            or row.get("state") != ("closed" if recovery or closed else "open")
        ):
            raise EvidenceUnavailable("incident-issue-unacknowledged")
        labels = row.get("labels")
        if not isinstance(labels, list) or not any(
            isinstance(label, dict) and label.get("name") == LABEL for label in labels
        ):
            raise EvidenceUnavailable("incident-label-unacknowledged")
        if parse_marker(row.get("body")) != marker_document(
            assessment, recovery=recovery, canonical_issue=canonical_issue
        ):
            raise EvidenceUnavailable("incident-marker-unacknowledged")
        return number

    def read_marker_assessment(self, marker: dict) -> Assessment:
        """Read both bindings independently; a copied marker never authorizes recovery."""
        bound = self.read_assessment(marker["run_id"])
        if RunIdentity.parse(marker) != bound.identity or not bound.completed:
            raise EvidenceUnavailable("incident-evidence-binding-unavailable")
        if marker["completed"]:
            if marker_document(bound) != {
                key: value
                for key, value in marker.items()
                if key not in {"recovery", "canonical_issue"}
            }:
                raise EvidenceUnavailable("incident-evidence-binding-unavailable")
        elif (
            marker["workflow_failure"]
            or marker["state"] == "green"
            or "recovery" in marker
            or "canonical_issue" in marker
        ):
            raise EvidenceUnavailable("invalid-provisional-incident")
        # A provisional verdict/key is explicitly not authority. Only its
        # immutable run envelope survives; the genuine terminal read above
        # supplies the completed failure set and every subsequent command.
        if "recovery" in marker:
            recovery = self.read_assessment(marker["recovery"]["run_id"])
            if marker_document(bound, recovery=recovery) != {
                key: value for key, value in marker.items() if key != "canonical_issue"
            }:
                raise EvidenceUnavailable("recovery-evidence-binding-unavailable")
        if "canonical_issue" in marker:
            row = self.request(f"issues/{positive_integer(marker['canonical_issue'])}")
            if not isinstance(row, dict) or not isinstance(row.get("labels"), list):
                raise EvidenceUnavailable("canonical-incident-binding-unavailable")
            canonical = parse_marker(row.get("body"))
            if (
                row.get("number") != marker["canonical_issue"]
                or row.get("pull_request")
                or "canonical_issue" in canonical
                or canonical["scope"] != bound.identity.scope
                or canonical["key"] != bound.key
                or not any(
                    isinstance(label, dict) and label.get("name") == LABEL
                    for label in row.get("labels", [])
                )
                or self.read_marker_assessment(canonical).key != bound.key
            ):
                raise EvidenceUnavailable("canonical-incident-binding-unavailable")
        return bound

    def upsert_issue(self, assessment: Assessment, *, recover: bool = False) -> int | None:
        """Evidence candidate only; the host re-verifies terminal authority later."""
        if assessment.completed and self.read_assessment(assessment.identity.run_id) != assessment:
            raise EvidenceUnavailable("terminal-evidence-binding-unavailable")

        def matches_assessment(marker: dict) -> bool:
            return marker["scope"] == assessment.identity.scope and (
                recover
                or (marker["completed"] and marker["key"] == assessment.key)
                or (not marker["completed"] and RunIdentity.parse(marker) == assessment.identity)
            )

        matches = []
        for issue in self.issues():
            if issue.get("pull_request"):
                continue
            try:
                marker = parse_marker(issue.get("body"))
            except EvidenceUnavailable:
                continue
            if "canonical_issue" in marker:
                if matches_assessment(marker):
                    self.read_marker_assessment(marker)
                continue
            if matches_assessment(marker):
                matches.append(issue)
        if recover:
            if assessment.state != "green" or not assessment.completed:
                raise EvidenceUnavailable("recovery-not-proven")
            verified = self.read_assessment(assessment.identity.run_id)
            if verified != assessment:
                raise EvidenceUnavailable("recovery-evidence-binding-unavailable")
            # Validate every candidate before the first mutation. Recovery
            # retains each original failure-set key, with a separate green
            # binding; overwriting it with the empty green set breaks recurrence.
            recovered = []
            for issue in matches:
                marker = parse_marker(issue.get("body"))
                bound = self.read_marker_assessment(marker)
                if "recovery" in marker and _run_order(assessment.identity) < _run_order(
                    RunIdentity.parse(marker["recovery"])
                ):
                    raise EvidenceUnavailable("recovery-evidence-stale")
                # Genuine terminal-green triage has no established red key;
                # close it without inventing an escalation or replacing red.
                if bound.state == "green":
                    if _run_order(assessment.identity) < _run_order(bound.identity):
                        raise EvidenceUnavailable("recovery-evidence-stale")
                    recovery = None
                else:
                    recovery = assessment
                body = marker_body(bound, recovery=recovery)
                recovered.append((issue, bound, body, recovery))
            for issue, bound, body, recovery in recovered:
                self.request(
                    f"issues/{positive_integer(issue['number'])}",
                    method="PATCH",
                    body={"state": "closed", "body": body},
                )
                self._issue_readback(issue["number"], bound, recovery=recovery, closed=True)
            return None
        body = marker_body(assessment)
        if matches:
            if not assessment.completed and len(matches) > 1:
                raise EvidenceUnavailable("duplicate-provisional-incident")
            # Provisional evidence may arrive for the same set after an older
            # terminal incident. Never replace that stable terminal marker
            # with a caller/provisional verdict or reopen before genuine red.
            stable = [issue for issue in matches if parse_marker(issue["body"])["completed"]]
            if not assessment.completed and stable:
                issue = min(stable, key=lambda row: positive_integer(row["number"]))
                marker = parse_marker(issue["body"])
                bound = self.read_marker_assessment(marker)
                recovery = (
                    self.read_assessment(marker["recovery"]["run_id"])
                    if "recovery" in marker
                    else None
                )
                return self._issue_readback(
                    issue["number"], bound, recovery=recovery, closed=issue["state"] == "closed"
                )
            number = min(positive_integer(issue["number"]) for issue in stable or matches)
            closed = assessment.completed and assessment.state == "green"
            self.request(
                f"issues/{number}",
                method="PATCH",
                body={"body": body, "state": "closed" if closed else "open"},
            )
            self._issue_readback(number, assessment, closed=closed)
            for duplicate in matches:
                other = positive_integer(duplicate["number"])
                if other == number:
                    continue
                # A closed, terminal-bound evidence alias is never a second
                # Bead. The host independently verifies its canonical key.
                self.request(
                    f"issues/{other}",
                    method="PATCH",
                    body={
                        "body": marker_body(assessment, canonical_issue=number),
                        "state": "closed",
                    },
                )
                self._issue_readback(other, assessment, closed=True, canonical_issue=number)
            return number
        # Re-query immediately before create. Workflow-scoped concurrency is
        # the serialization boundary; this also handles create-before-ACK.
        for issue in self.issues():
            try:
                marker = parse_marker(issue.get("body"))
            except EvidenceUnavailable:
                continue
            if "canonical_issue" not in marker and matches_assessment(marker):
                # A committed create-before-ACK candidate is updated and then
                # independently read back; stale version/fields are not credit.
                number = positive_integer(issue["number"])
                self.request(
                    f"issues/{number}", method="PATCH", body={"body": body, "state": "open"}
                )
                return self._issue_readback(number, assessment)
        self._ensure_label()
        created = self.request(
            "issues",
            method="POST",
            body={
                "title": "Nightly assurance evidence requires coordinator triage",
                "body": body,
                "labels": [LABEL],
            },
        )
        return self._issue_readback(positive_integer(created["number"]), assessment)


def _run_order(identity: RunIdentity) -> tuple:
    return identity.night, identity.run_id, identity.attempt


def marker_document(
    assessment: Assessment,
    *,
    recovery: Assessment | None = None,
    canonical_issue: int | None = None,
) -> dict:
    # A fixed-size transport marker references the full independently fetched
    # artifact census. Large failure sets cannot suppress second-red ingress
    # by exceeding GitHub's issue-body limit. No supplied text is authority.
    document = {
        **assessment.identity.document(),
        "scope": assessment.identity.scope,
        "state": assessment.state,
        "workflow_failure": assessment.workflow_failure,
        "completed": assessment.completed,
        "failure_digest": assessment.failure_digest,
        "key": assessment.key,
    }
    if recovery is not None:
        if (
            assessment.state not in {"red", "unknown"}
            or not assessment.completed
            or recovery.state != "green"
            or not recovery.completed
            or recovery.identity.scope != assessment.identity.scope
            or _run_order(recovery.identity) <= _run_order(assessment.identity)
        ):
            raise EvidenceUnavailable("recovery-not-proven")
        document["recovery"] = marker_document(recovery)
    if canonical_issue is not None:
        if not assessment.completed or recovery is not None:
            raise EvidenceUnavailable("invalid-canonical-incident")
        document["canonical_issue"] = positive_integer(canonical_issue)
    return document


def marker_body(
    assessment: Assessment,
    *,
    recovery: Assessment | None = None,
    canonical_issue: int | None = None,
) -> str:
    encoded = json.dumps(
        marker_document(assessment, recovery=recovery, canonical_issue=canonical_issue),
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded) > 4096:
        raise EvidenceUnavailable("issue-marker-too-large")
    return (
        "Nightly evidence transport. Beads authority requires independent terminal host readback.\n"
        f"{MARKER}{encoded}\n-->\n"
    )


def parse_marker(body: Any) -> dict:
    if not isinstance(body, str) or len(body) > 8192 or body.count(MARKER) != 1:
        raise EvidenceUnavailable("invalid-issue-marker")
    try:
        value = json.loads(body.split(MARKER, 1)[1].split("\n-->", 1)[0])
        identity = RunIdentity.parse(value)
        fields = set(identity.document()) | {
            "scope",
            "state",
            "workflow_failure",
            "completed",
            "failure_digest",
            "key",
        }
        if (
            set(value) not in (fields, fields | {"recovery"}, fields | {"canonical_issue"})
            or value["scope"] != identity.scope
            or value["state"] not in {"green", "red", "unknown"}
        ):
            raise EvidenceUnavailable("invalid-issue-state")
        if type(value["completed"]) is not bool or type(value["workflow_failure"]) is not bool:
            raise EvidenceUnavailable("invalid-issue-verdict")
        import re

        if not isinstance(value["failure_digest"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", value["failure_digest"]
        ):
            raise EvidenceUnavailable("invalid-issue-digest")
        from butlers.nightly_assurance import digest

        expected = digest(
            {
                "workflow": WORKFLOW,
                "scope": identity.scope,
                "failure_digest": value["failure_digest"],
            }
        )
        if value["key"] != expected:
            raise EvidenceUnavailable("invalid-issue-digest")
        if "recovery" in value:
            recovery = value["recovery"]
            if not isinstance(recovery, dict) or "recovery" in recovery:
                raise EvidenceUnavailable("invalid-recovery-marker")
            parsed = parse_marker(f"{MARKER}{json.dumps(recovery)}\n-->\n")
            if (
                value["state"] not in {"red", "unknown"}
                or value["completed"] is not True
                or parsed["state"] != "green"
                or parsed["completed"] is not True
                or parsed["scope"] != identity.scope
                or _run_order(RunIdentity.parse(parsed)) <= _run_order(identity)
            ):
                raise EvidenceUnavailable("invalid-recovery-marker")
        if "canonical_issue" in value:
            positive_integer(value["canonical_issue"])
            if value["completed"] is not True:
                raise EvidenceUnavailable("invalid-canonical-incident")
        return value
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise EvidenceUnavailable("invalid-issue-marker") from exc
