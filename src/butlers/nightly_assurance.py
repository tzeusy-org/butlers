"""Content-blind nightly evidence shared by CI, the trusted host and Switchboard.

GitHub evidence is not an authenticated Beads command. Only the existing host
coordinator reconciles completed runs; runtime consumers receive its bounded
export. This module has no network, database or lifecycle-writing capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY = "tzeusy-org/butlers"
WORKFLOW = ".github/workflows/nightly.yml"
VERSION = 1
BASE_VARIANTS = ("schema", "offset-45", "offset-120", "exact-image")
FOLDED_VARIANTS = ("folded-hour", "folded-minute")
EXACT_IMAGE_FUNCTIONS = (
    "test_exact_image_bubblewrap_handshake_runs_only_when_explicitly_enabled",
    "test_exact_image_bubblewrap_sandbox_kills_detached_descendants_before_persistence",
    "test_exact_image_concurrent_sandboxes_cannot_read_write_or_inspect_each_other",
    "test_exact_image_bubblewrap_sandbox_denies_signer_and_protected_environment",
)
EXACT_IMAGE_MANIFEST = frozenset(
    f"tests/cli/test_runtime_cli_sandbox.py::{name}::case-1" for name in EXACT_IMAGE_FUNCTIONS
)
VARIANTS = BASE_VARIANTS + FOLDED_VARIANTS
EXPORT_PATH = Path("/run/butlers-nightly/incidents.json")
MAX_EXPORT_BYTES = 256 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_BEAD = re.compile(r"bu-[a-z0-9]+(?:\.[a-z0-9]+)*\Z")
_NODE = re.compile(
    r"(?:tests|roster)/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)+::case-[0-9]+\Z"
)
_STATES = {"green", "red", "unknown"}


class EvidenceUnavailable(ValueError):
    """Fixed classification; never include an untrusted field in its message."""


def digest(document: Any) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def atomic_json(path: Path, document: Any) -> None:
    """Replace a host/runner-owned file without following an existing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(document)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise EvidenceUnavailable("evidence-too-large")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path, *, limit: int = MAX_EVIDENCE_BYTES) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise EvidenceUnavailable("not-bounded-regular-file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise EvidenceUnavailable("evidence-too-large")
        document = json.loads(body)
        if not isinstance(document, dict):
            raise EvidenceUnavailable("invalid-document")
        return document
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceUnavailable("invalid-json") from exc
    finally:
        os.close(fd)


def positive_integer(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise EvidenceUnavailable("invalid-positive-integer")
    return value


def utc_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError) as exc:
        raise EvidenceUnavailable("invalid-utc-time") from exc
    if parsed.utcoffset() != timedelta(0):
        raise EvidenceUnavailable("invalid-utc-time")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class RunIdentity:
    run_id: int
    attempt: int
    head: str
    event: str
    ref: str
    night: str

    def __post_init__(self) -> None:
        positive_integer(self.run_id)
        positive_integer(self.attempt)
        if not isinstance(self.head, str) or not _SHA.fullmatch(self.head):
            raise EvidenceUnavailable("invalid-head")
        if not isinstance(self.event, str) or self.event not in {"schedule", "workflow_dispatch"}:
            raise EvidenceUnavailable("invalid-event")
        if self.event == "schedule" and self.ref != "main":
            raise EvidenceUnavailable("invalid-scheduled-ref")
        if not isinstance(self.ref, str) or not re.fullmatch(
            r"(?:main|agent/[A-Za-z0-9._-]+)", self.ref
        ):
            raise EvidenceUnavailable("invalid-ref")
        try:
            date.fromisoformat(self.night)
        except (ValueError, TypeError) as exc:
            raise EvidenceUnavailable("invalid-night") from exc

    @property
    def scope(self) -> str:
        return "scheduled" if self.event == "schedule" else f"canary:{self.ref}"

    def document(self) -> dict[str, Any]:
        return {"version": VERSION, "repository": REPOSITORY, "workflow": WORKFLOW, **asdict(self)}

    @classmethod
    def parse(cls, value: dict[str, Any]) -> RunIdentity:
        if (value.get("version"), value.get("repository"), value.get("workflow")) != (
            VERSION,
            REPOSITORY,
            WORKFLOW,
        ):
            raise EvidenceUnavailable("foreign-evidence")
        try:
            return cls(**{key: value[key] for key in cls.__dataclass_fields__})
        except (KeyError, TypeError) as exc:
            raise EvidenceUnavailable("invalid-run-identity") from exc


@dataclass(frozen=True)
class Assessment:
    identity: RunIdentity
    state: str
    failures: tuple[str, ...]
    unavailable: tuple[str, ...]
    workflow_failure: bool
    completed: bool

    @property
    def failure_digest(self) -> str:
        return digest({"failures": self.failures, "unavailable": self.unavailable})

    @property
    def key(self) -> str:
        return digest(
            {
                "workflow": WORKFLOW,
                "scope": self.identity.scope,
                "failure_digest": self.failure_digest,
            }
        )

    def document(self) -> dict[str, Any]:
        return {
            **self.identity.document(),
            "state": self.state,
            "failures": list(self.failures),
            "unavailable": list(self.unavailable),
            "workflow_failure": self.workflow_failure,
            "completed": self.completed,
            "failure_digest": self.failure_digest,
            "key": self.key,
        }


def assess(
    identity: RunIdentity,
    *,
    official_run: dict[str, Any],
    official_jobs: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    folded_required: bool,
) -> Assessment:
    """Bind every variant to GitHub readback; receipt exit alone never proves green.

    The final CI assessor runs before its own workflow completes. Its result is
    provisional. The host repeats this function after terminal readback before
    it can count a red night or mutate Beads.
    """
    actual = identity_from_github(official_run)
    if actual != identity:
        raise EvidenceUnavailable("run-identity-mismatch")
    required = VARIANTS if folded_required else BASE_VARIANTS
    if set(evidence) - set(VARIANTS):
        raise EvidenceUnavailable("unexpected-variant")
    failures: set[str] = set()
    unavailable: set[str] = set()
    by_variant: dict[str, dict[str, Any]] = {}
    for job in official_jobs:
        name = job.get("name")
        variant = {
            "nightly": "schema",
            "faketime-matrix (+45d)": "offset-45",
            "faketime-matrix (+120d)": "offset-120",
            "exact-image-sandbox": "exact-image",
            "folded-clock (hour)": "folded-hour",
            "folded-clock (minute)": "folded-minute",
        }.get(name)
        if variant:
            if variant in by_variant:
                raise EvidenceUnavailable("duplicate-official-variant")
            by_variant[variant] = job
    for variant in required:
        try:
            job, item = by_variant[variant], evidence[variant]
            if RunIdentity.parse(item) != identity or item.get("variant") != variant:
                raise EvidenceUnavailable("variant-identity-mismatch")
            manifest = item["manifest"]
            if not isinstance(manifest, list) or not manifest or len(manifest) > 100_000:
                raise EvidenceUnavailable("invalid-manifest")
            if len(set(manifest)) != len(manifest) or any(
                not isinstance(node, str)
                or not _NODE.fullmatch(node)
                or ".." in Path(node.split("::", 1)[0]).parts
                for node in manifest
            ):
                raise EvidenceUnavailable("invalid-manifest")
            if item.get("manifest_digest") != digest(manifest):
                raise EvidenceUnavailable("manifest-digest-mismatch")
            outcomes = item["outcomes"]
            if not isinstance(outcomes, dict) or set(outcomes) - set(manifest):
                raise EvidenceUnavailable("invalid-outcomes")
            for node, status in outcomes.items():
                if not isinstance(status, str):
                    raise EvidenceUnavailable("invalid-outcome")
                if status in {"FAILED", "SETUP_ERROR", "TEARDOWN_ERROR"}:
                    failures.add(f"{variant}:{node}:{status}")
                elif status not in {"PASSED", "SKIPPED"}:
                    raise EvidenceUnavailable("invalid-outcome")
            if variant == "exact-image" and (
                set(manifest) != EXACT_IMAGE_MANIFEST
                or any(outcomes.get(node) != "PASSED" for node in EXACT_IMAGE_MANIFEST)
            ):
                raise EvidenceUnavailable("exact-image-original-proofs-unavailable")
            if (
                variant.startswith(("offset-", "folded-"))
                and item.get("clock_conformance_verified") is not True
            ):
                raise EvidenceUnavailable("clock-conformance-unavailable")
            if variant == "folded-minute":
                barrier = item.get("boundary")
                if (
                    not isinstance(barrier, dict)
                    or set(barrier) != {"0", "1"}
                    or not all(
                        isinstance(witness, dict) and witness.get("actual_boundary_crossed") is True
                        for witness in barrier.values()
                    )
                ):
                    raise EvidenceUnavailable("minute-boundary-unavailable")
            if (
                job.get("status") != "completed"
                or job.get("conclusion") != "success"
                or type(item.get("exit_code")) is not int
                or item.get("exit_code") != 0
                or item.get("controller_complete") is not True
                or set(outcomes) != set(manifest)
            ):
                unavailable.add(f"{variant}:incomplete-or-non-success")
        except (KeyError, TypeError, EvidenceUnavailable):
            unavailable.add(f"{variant}:evidence-unavailable")
    completed = official_run.get("status") == "completed"
    workflow_failure = completed and official_run.get("conclusion") == "failure"
    green = (
        completed and official_run.get("conclusion") == "success" and not (unavailable or failures)
    )
    return Assessment(
        identity,
        "green" if green else "red" if workflow_failure else "unknown",
        tuple(sorted(failures)),
        tuple(sorted(unavailable)),
        workflow_failure,
        completed,
    )


def identity_from_github(run: dict[str, Any]) -> RunIdentity:
    if (
        not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != REPOSITORY
        or run.get("path") != WORKFLOW
    ):
        raise EvidenceUnavailable("foreign-official-run")
    return RunIdentity(
        run["id"],
        run["run_attempt"],
        run["head_sha"],
        run["event"],
        run["head_branch"],
        utc_time(run["created_at"]).date().isoformat(),
    )


def latest_nights(assessments: list[Assessment]) -> list[Assessment]:
    """One final attempt per UTC scheduled date; canaries never enter this stream."""
    nights: dict[str, Assessment] = {}
    for item in assessments:
        if item.identity.scope != "scheduled":
            continue
        prior = nights.get(item.identity.night)
        if prior is None or (item.identity.run_id, item.identity.attempt) > (
            prior.identity.run_id,
            prior.identity.attempt,
        ):
            nights[item.identity.night] = item
    return [nights[night] for night in sorted(nights)]


def second_red(history: list[Assessment], current: Assessment) -> bool:
    if current.state != "red" or not current.completed:
        return False
    if current.identity.scope != "scheduled":
        return False  # Canary lifecycle must be explicitly invoked by the host.
    prior_date = (date.fromisoformat(current.identity.night) - timedelta(days=1)).isoformat()
    prior = next((row for row in latest_nights(history) if row.identity.night == prior_date), None)
    return prior is not None and prior.state == "red" and prior.completed


def validate_export(document: dict[str, Any], *, now: datetime) -> list[dict[str, Any]]:
    if set(document) != {"version", "repository", "workflow", "as_of", "incidents"}:
        raise EvidenceUnavailable("invalid-export-fields")
    if (document["version"], document["repository"], document["workflow"]) != (
        VERSION,
        REPOSITORY,
        WORKFLOW,
    ):
        raise EvidenceUnavailable("foreign-export")
    age = now - utc_time(document["as_of"])
    if age < timedelta(minutes=-5) or age > timedelta(hours=6):
        raise EvidenceUnavailable("stale-export")
    incidents = document["incidents"]
    if not isinstance(incidents, list) or len(incidents) > 100:
        raise EvidenceUnavailable("invalid-incident-population")
    fields = {
        "incident_id",
        "episode_id",
        "status",
        "issue",
        "run_id",
        "attempt",
        "head",
        "night",
        "failure_digest",
    }
    seen = set()
    for item in incidents:
        if not isinstance(item, dict) or set(item) != fields:
            raise EvidenceUnavailable("invalid-incident-fields")
        if not isinstance(item["incident_id"], str) or not _BEAD.fullmatch(item["incident_id"]):
            raise EvidenceUnavailable("invalid-incident-id")
        if not isinstance(item["episode_id"], str) or not isinstance(item["failure_digest"], str):
            raise EvidenceUnavailable("invalid-incident-digest")
        if not _DIGEST.fullmatch(item["episode_id"]) or not _DIGEST.fullmatch(
            item["failure_digest"]
        ):
            raise EvidenceUnavailable("invalid-incident-digest")
        if not isinstance(item["status"], str) or item["status"] not in {"open", "recovered"}:
            raise EvidenceUnavailable("invalid-incident-status")
        positive_integer(item["issue"])
        RunIdentity(
            item["run_id"], item["attempt"], item["head"], "schedule", "main", item["night"]
        )
        key = item["incident_id"], item["episode_id"]
        if key in seen:
            raise EvidenceUnavailable("duplicate-incident")
        seen.add(key)
    return incidents
