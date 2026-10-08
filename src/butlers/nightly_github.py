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

    def _issue_readback(self, number: int, assessment: Assessment, *, closed: bool = False) -> int:
        row = self.request(f"issues/{positive_integer(number)}")
        if (
            not isinstance(row, dict)
            or row.get("number") != number
            or row.get("pull_request")
            or row.get("state") != ("closed" if closed else "open")
        ):
            raise EvidenceUnavailable("incident-issue-unacknowledged")
        labels = row.get("labels")
        if not isinstance(labels, list) or not any(
            isinstance(label, dict) and label.get("name") == LABEL for label in labels
        ):
            raise EvidenceUnavailable("incident-label-unacknowledged")
        if parse_marker(row.get("body")) != marker_document(assessment):
            raise EvidenceUnavailable("incident-marker-unacknowledged")
        return number

    def upsert_issue(self, assessment: Assessment, *, recover: bool = False) -> int | None:
        """Evidence candidate only; the host re-verifies terminal authority later."""
        matches = []
        for issue in self.issues():
            if issue.get("pull_request"):
                continue
            try:
                marker = parse_marker(issue.get("body"))
            except EvidenceUnavailable:
                continue
            if marker["scope"] == assessment.identity.scope and (
                recover or marker["key"] == assessment.key
            ):
                matches.append(issue)
        if recover:
            if assessment.state != "green" or not assessment.completed:
                raise EvidenceUnavailable("recovery-not-proven")
            for issue in matches:
                self.request(
                    f"issues/{positive_integer(issue['number'])}",
                    method="PATCH",
                    body={"state": "closed", "body": marker_body(assessment)},
                )
                self._issue_readback(issue["number"], assessment, closed=True)
            return None
        body = marker_body(assessment)
        if len(matches) > 1:
            raise EvidenceUnavailable("duplicate-incident-issue")
        if matches:
            number = positive_integer(matches[0]["number"])
            self.request(f"issues/{number}", method="PATCH", body={"body": body, "state": "open"})
            return self._issue_readback(number, assessment)
        # Re-query immediately before create. Workflow-scoped concurrency is
        # the serialization boundary; this also handles create-before-ACK.
        for issue in self.issues():
            try:
                marker = parse_marker(issue.get("body"))
            except EvidenceUnavailable:
                continue
            if marker["key"] == assessment.key and marker["scope"] == assessment.identity.scope:
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


def marker_document(assessment: Assessment) -> dict:
    # A fixed-size transport marker references the full independently fetched
    # artifact census. Large failure sets cannot suppress second-red ingress
    # by exceeding GitHub's issue-body limit. No supplied text is authority.
    return {
        **assessment.identity.document(),
        "scope": assessment.identity.scope,
        "state": assessment.state,
        "workflow_failure": assessment.workflow_failure,
        "completed": assessment.completed,
        "failure_digest": assessment.failure_digest,
        "key": assessment.key,
    }


def marker_body(assessment: Assessment) -> str:
    encoded = json.dumps(marker_document(assessment), sort_keys=True, separators=(",", ":"))
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
            set(value) != fields
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
        return value
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise EvidenceUnavailable("invalid-issue-marker") from exc
