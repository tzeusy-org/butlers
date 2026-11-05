"""The D6 stamp account a domain reports with a broker-forwarded read (RFC 0038 D6, S1).

A stamped read's label comes from the records it returned: owner-authored,
untainted records are owner data, and a tainted record carries its writer
invocation's label, resolved by the broker through the record's
``(taint_goal_id, taint_invocation_id)`` pointer. The domain accounts for each
record; the broker joins the labels.

Domain side. ``AssertionVerificationMiddleware`` binds an empty
``StampAccount`` around exactly one verified call (``account_scope``). Every
D6 store reader passes the rows it returns to ``account_records``, which is a
no-op outside a verified call. After the handler returns, the middleware puts
the account into the result's ``_meta`` under ``STAMP_ACCOUNT_META``
(``attach_report``), bound to the verified ``capability``, ``call_id`` and
``args_digest``. A handler's own value under that key is always removed, so a
proxied or handler-built ``_meta`` never reaches the broker as a report.

Broker side. ``parse_report`` accepts a report only when it is exactly this
shape and names the call the broker sent. Anything else, including no report,
is no account, so the read keeps the unknown label (``{world}``, all six
categories). The report is read only from ``_meta``, never from the tool's
content, so stored text cannot speak for itself.

A record is clean only when it carries both stamp columns, both are null, and
the store's own authority column says the owner wrote it. A record with both
pointers set is accounted by its writer. Every other record, including one
whose columns the reader did not select, makes the account not
owner-untainted. A reader that never calls ``account_records`` leaves the
account unopened, and no report is sent.

What a report cannot prove is that a tool's readers saw every record it
returns. That is a property of the tool's code, and the catalog owns it: a
capability's receipt is read as stamped only when its descriptor says
``output_trust = stamped``, which the catalog check still refuses for every
tool.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from butlers.core.goal_labels import OWNER_AUTHORITATIVE

#: The ``_meta`` key carrying a domain's stamp account to the broker.
STAMP_ACCOUNT_META = "butlers/stamp_account"
REPORT_VERSION = 1
#: Distinct writers one report may name. A larger read reports no account.
MAX_WRITERS = 256

_REPORT_KEYS = frozenset(
    {"version", "capability", "call_id", "args_digest", "owner_untainted", "writers"}
)
_TAINT_COLUMNS = ("taint_goal_id", "taint_invocation_id")

Writers = tuple[tuple[UUID, UUID], ...]
#: No account: the broker reads the output as unknown.
NO_ACCOUNT: tuple[bool, Writers] = (False, ())


@dataclass(slots=True)
class StampAccount:
    """The records one verified call's readers returned, as stamps."""

    opened: bool = False
    owner_untainted: bool = True
    writers: set[tuple[UUID, UUID]] = field(default_factory=set)

    def add(self, record: Mapping[str, Any], owner_authored: bool) -> None:
        self.opened = True
        if not all(column in record for column in _TAINT_COLUMNS):
            self.owner_untainted = False  # The reader did not select the stamp.
            return
        goal_id, invocation_id = (_uuid(record.get(column)) for column in _TAINT_COLUMNS)
        if goal_id is not None and invocation_id is not None:
            self.writers.add((goal_id, invocation_id))
        elif goal_id is not None or invocation_id is not None or not owner_authored:
            self.owner_untainted = False  # A half pointer, or not the owner's.
        elif any(record.get(column) is not None for column in _TAINT_COLUMNS):
            self.owner_untainted = False  # An unreadable pointer.

    def report(self, capability: str, call_id: UUID, args_digest: str) -> dict[str, Any]:
        clean = self.owner_untainted and len(self.writers) <= MAX_WRITERS
        return {
            "version": REPORT_VERSION,
            "capability": capability,
            "call_id": str(call_id),
            "args_digest": args_digest,
            "owner_untainted": clean,
            "writers": sorted([str(g), str(i)] for g, i in self.writers) if clean else [],
        }


def _uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


_account: contextvars.ContextVar[StampAccount | None] = contextvars.ContextVar(
    "d6_stamp_account", default=None
)


@contextmanager
def account_scope() -> Iterator[StampAccount]:
    """Bind a fresh account for one verified call."""
    account = StampAccount()
    token = _account.set(account)
    try:
        yield account
    finally:
        _account.reset(token)


def content_authority_is_owner(record: Mapping[str, Any]) -> bool:
    """The memory and relationship rule: the store's server-stamped ``content_authority``."""
    return record.get("content_authority") in OWNER_AUTHORITATIVE


def account_records(
    records: Iterable[Mapping[str, Any]],
    owner_authored: Callable[[Mapping[str, Any]], bool] = content_authority_is_owner,
) -> None:
    """Account for every record a D6 store reader returns; a no-op outside a verified call."""
    account = _account.get()
    if account is None:
        return
    account.opened = True  # An empty read is a complete, clean account.
    for record in records:
        account.add(record, owner_authored(record))


def attach_report(result: Any, account: StampAccount, lineage: Any) -> Any:
    """Return ``result`` with the account in ``_meta``, replacing any handler value."""
    from fastmcp.tools import ToolResult

    if not isinstance(result, ToolResult):
        return result
    meta = {key: value for key, value in (result.meta or {}).items() if key != STAMP_ACCOUNT_META}
    if account.opened and not result.is_error:
        meta[STAMP_ACCOUNT_META] = account.report(
            lineage.capability, lineage.call_id, lineage.args_digest
        )
    if meta == (result.meta or {}):
        return result
    return ToolResult(
        content=result.content,
        structured_content=result.structured_content,
        meta=meta or None,
        is_error=result.is_error,
    )


def parse_report(
    meta: Any, *, capability: str, call_id: UUID, args_digest: str
) -> tuple[bool, Writers]:
    """The account in a result's ``_meta`` if it names exactly this call, else ``NO_ACCOUNT``."""
    if not isinstance(meta, Mapping):
        return NO_ACCOUNT
    report = meta.get(STAMP_ACCOUNT_META)
    if not isinstance(report, Mapping) or set(report) != _REPORT_KEYS:
        return NO_ACCOUNT
    if (
        type(report["version"]) is not int
        or report["version"] != REPORT_VERSION
        or report["capability"] != capability
        or report["call_id"] != str(call_id)
        or report["args_digest"] != args_digest
        or report["owner_untainted"] is not True
    ):
        return NO_ACCOUNT
    entries = report["writers"]
    if not isinstance(entries, list) or len(entries) > MAX_WRITERS:
        return NO_ACCOUNT
    writers = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2:
            return NO_ACCOUNT
        pointer = tuple(_canonical_uuid(part) for part in entry)
        if None in pointer:
            return NO_ACCOUNT
        writers.append(pointer)
    return True, tuple(writers)  # type: ignore[arg-type]


def _canonical_uuid(value: Any) -> UUID | None:
    parsed = _uuid(value) if isinstance(value, str) else None
    return parsed if parsed is not None and str(parsed) == value else None


__all__ = [
    "MAX_WRITERS",
    "NO_ACCOUNT",
    "REPORT_VERSION",
    "STAMP_ACCOUNT_META",
    "StampAccount",
    "account_records",
    "account_scope",
    "attach_report",
    "content_authority_is_owner",
    "parse_report",
]
