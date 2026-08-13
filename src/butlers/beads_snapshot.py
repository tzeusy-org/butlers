"""Bounded, read-only projection of the mounted Beads JSONL export.

This module is intentionally not a Beads client.  Dashboard containers receive
one read-only export file and must never reach the host's live Dolt service,
``bd``, credentials, or an external tracker.  Parsed JSON is kept entirely
inside this module; callers receive safe dataclasses only.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# This is the existing compose bind-mount target.  Tests and non-standard
# deployments can provide a path directly to ``BeadSnapshotReader``.
DEFAULT_BEADS_EXPORT_PATH = Path(
    os.environ.get("BUTLERS_BEADS_EXPORT_PATH", "/app/.beads/issues.export.jsonl")
)
STALE_BEADS_EXPORT_AGE = timedelta(days=14)

# The real export is a few MiB and several thousand records.  These bounds are
# intentionally generous for that source while preventing this read-only route
# from becoming a raw-file memory or response amplification surface.
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_EXPORT_RECORDS = 20_000
MAX_LINE_BYTES = 1 * 1024 * 1024
MAX_IDENTIFIER_CHARS = 128
MAX_TITLE_CHARS = 4_096
MAX_TEXT_CHARS = 64 * 1024
MAX_LABELS = 50
MAX_LABEL_CHARS = 128
MAX_DIRECT_DEPENDENCIES = 20


@dataclass(frozen=True)
class SnapshotAvailability:
    """Safe source-health facts, with no raw filesystem exception text."""

    available: bool
    reason: str | None
    export_as_of: datetime | None


@dataclass(frozen=True)
class SnapshotDependency:
    """A safe, bounded summary of one direct dependency."""

    id: str
    title: str | None
    status: str | None
    priority: int | None
    type: str | None


@dataclass(frozen=True)
class SnapshotBeadDetail:
    """The only Bead detail representation that may leave the reader."""

    id: str
    title: str | None
    status: str | None
    priority: int | None
    type: str | None
    description: str | None
    design: str | None
    acceptance_criteria: str | None
    labels: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    started_at: datetime | None
    closed_at: datetime | None
    due_at: datetime | None
    dependencies: tuple[SnapshotDependency, ...]
    external_ref: str | None


@dataclass(frozen=True)
class SnapshotRead:
    """A detail lookup outcome that preserves availability before not-found."""

    availability: SnapshotAvailability
    detail: SnapshotBeadDetail | None


@dataclass(frozen=True)
class _SafeRecord:
    """Internal safe index entry; raw source mappings are never retained."""

    id: str
    title: str | None
    status: str | None
    priority: int | None
    type: str | None
    description: str | None
    design: str | None
    acceptance_criteria: str | None
    labels: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    started_at: datetime | None
    closed_at: datetime | None
    due_at: datetime | None
    dependency_ids: tuple[str, ...]
    external_ref: str | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalise a clock result without allowing a naive subtraction failure."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def assess_beads_export(
    path: Path,
    *,
    now: datetime,
) -> SnapshotAvailability:
    """Assess the mounted file before any lookup, exposing only safe reasons."""

    try:
        file_stat = path.stat()
    except FileNotFoundError:
        return SnapshotAvailability(False, "export_missing", None)
    except OSError:
        return SnapshotAvailability(False, "export_unreadable", None)

    try:
        export_as_of = datetime.fromtimestamp(file_stat.st_mtime, tz=UTC)
        if not stat.S_ISREG(file_stat.st_mode):
            return SnapshotAvailability(False, "export_unreadable", export_as_of)
        if file_stat.st_size > MAX_EXPORT_BYTES:
            return SnapshotAvailability(False, "export_oversized", export_as_of)
        if _as_utc(now) - export_as_of > STALE_BEADS_EXPORT_AGE:
            return SnapshotAvailability(False, "export_stale", export_as_of)
    except (OSError, OverflowError, ValueError):
        return SnapshotAvailability(False, "export_unreadable", None)

    return SnapshotAvailability(True, None, export_as_of)


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_CHARS:
        return None
    return value


def _safe_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value


def _safe_priority(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
        return None
    return value


def _safe_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _safe_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    labels: list[str] = []
    for candidate in value:
        if not isinstance(candidate, str) or len(candidate) > MAX_LABEL_CHARS:
            continue
        labels.append(candidate)
        if len(labels) == MAX_LABELS:
            break
    return tuple(labels)


def _direct_dependency_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    dependency_ids: list[str] = []
    seen: set[str] = set()
    for edge in value:
        if not isinstance(edge, Mapping):
            continue
        dependency_id = _safe_identifier(edge.get("depends_on_id"))
        if dependency_id is None or dependency_id in seen:
            continue
        dependency_ids.append(dependency_id)
        seen.add(dependency_id)
        if len(dependency_ids) == MAX_DIRECT_DEPENDENCIES:
            break
    return tuple(dependency_ids)


def _project_record(record: Mapping[str, object]) -> _SafeRecord | None:
    """Project one parsed object without retaining its mapping or extra fields."""

    record_id = _safe_identifier(record.get("id"))
    if record_id is None:
        return None
    raw_type = record.get("issue_type")
    if raw_type is None:
        raw_type = record.get("type")
    return _SafeRecord(
        id=record_id,
        title=_safe_text(record.get("title"), limit=MAX_TITLE_CHARS),
        status=_safe_text(record.get("status"), limit=MAX_LABEL_CHARS),
        priority=_safe_priority(record.get("priority")),
        type=_safe_text(raw_type, limit=MAX_LABEL_CHARS),
        description=_safe_text(record.get("description"), limit=MAX_TEXT_CHARS),
        design=_safe_text(record.get("design"), limit=MAX_TEXT_CHARS),
        acceptance_criteria=_safe_text(record.get("acceptance_criteria"), limit=MAX_TEXT_CHARS),
        labels=_safe_labels(record.get("labels")),
        created_at=_safe_timestamp(record.get("created_at")),
        updated_at=_safe_timestamp(record.get("updated_at")),
        started_at=_safe_timestamp(record.get("started_at")),
        closed_at=_safe_timestamp(record.get("closed_at")),
        due_at=_safe_timestamp(record.get("due_at")),
        dependency_ids=_direct_dependency_ids(record.get("dependencies")),
        external_ref=_safe_text(record.get("external_ref"), limit=MAX_TEXT_CHARS),
    )


class BeadSnapshotReader:
    """Read and project one detail from the bounded mounted snapshot only."""

    def __init__(
        self,
        export_path: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._export_path = export_path or DEFAULT_BEADS_EXPORT_PATH
        self._clock = clock

    def read(self, bead_id: str) -> SnapshotRead:
        """Return a safe detail or an availability result. Never returns raw JSON."""

        availability = assess_beads_export(self._export_path, now=self._clock())
        if not availability.available:
            return SnapshotRead(availability, None)

        records = self._read_safe_records()
        if records is None:
            unavailable = SnapshotAvailability(
                False,
                "export_unreadable",
                availability.export_as_of,
            )
            return SnapshotRead(unavailable, None)

        record = records.get(bead_id)
        if record is None:
            return SnapshotRead(availability, None)
        return SnapshotRead(availability, self._detail_from_record(record, records))

    def _read_safe_records(self) -> dict[str, _SafeRecord] | None:
        """Fully parse under bounds; any parse/read issue invalidates the source."""

        records: dict[str, _SafeRecord] = {}
        bytes_read = 0
        record_count = 0
        try:
            with self._export_path.open("rb") as export_file:
                while True:
                    raw_line = export_file.readline(MAX_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    bytes_read += len(raw_line)
                    if bytes_read > MAX_EXPORT_BYTES or len(raw_line) > MAX_LINE_BYTES:
                        return None
                    if not raw_line.strip():
                        continue
                    record_count += 1
                    if record_count > MAX_EXPORT_RECORDS:
                        return None
                    decoded_line = raw_line.decode("utf-8")
                    decoded_record: Any = json.loads(decoded_line)
                    if not isinstance(decoded_record, dict):
                        return None
                    safe_record = _project_record(decoded_record)
                    if safe_record is not None:
                        # Match the current Beads export reader's last-record-wins
                        # semantics without keeping any raw previous record.
                        records[safe_record.id] = safe_record
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return records

    @staticmethod
    def _detail_from_record(
        record: _SafeRecord,
        records: Mapping[str, _SafeRecord],
    ) -> SnapshotBeadDetail:
        dependencies: list[SnapshotDependency] = []
        for dependency_id in record.dependency_ids:
            dependency = records.get(dependency_id)
            dependencies.append(
                SnapshotDependency(
                    id=dependency_id,
                    title=dependency.title if dependency else None,
                    status=dependency.status if dependency else None,
                    priority=dependency.priority if dependency else None,
                    type=dependency.type if dependency else None,
                )
            )
        return SnapshotBeadDetail(
            id=record.id,
            title=record.title,
            status=record.status,
            priority=record.priority,
            type=record.type,
            description=record.description,
            design=record.design,
            acceptance_criteria=record.acceptance_criteria,
            labels=record.labels,
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            closed_at=record.closed_at,
            due_at=record.due_at,
            dependencies=tuple(dependencies),
            external_ref=record.external_ref,
        )
