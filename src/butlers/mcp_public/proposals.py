"""``propose_fact``: the one public write, an inbox INSERT (RFC 0039 D13 step 1).

The reader parses the closed argument set first (``invalid_argument`` writes nothing), then
inserts one row into ``mcp_inbox.proposals`` with ``ON CONFLICT DO NOTHING``. The server role
has column-limited INSERT and no SELECT, so the call never reads the inbox: an identical retry
is a no-op the database absorbs, and the same ``proposal_id`` is returned either way.

``proposal_id`` is ``uuid5(NS, name)`` where ``name`` is the RFC 8785 (JCS) text of the JSON
array ``[sub, cid, idempotency_key, payload_sha256]``: four strings, each quoted and escaped, so
no two tuples share a ``name`` (a bare ``‖`` join could). ``payload_sha256`` is the lowercase hex
SHA-256 of the JCS text of the payload object (see :func:`payload_of`). ``sub`` and ``cid`` come
from the verified assertion (``ReadContext``), never from arguments.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import rfc8785

from butlers.knowledge_read.core import Arguments, ReadContext, ReadError
from butlers.knowledge_read.proposable import proposable_predicates

# Fixed forever: changing it re-ids every proposal and breaks retry idempotence. Pinned in a test.
# It is uuid5(NAMESPACE_URL, "urn:butlers:public-mcp:propose_fact:v1").
NS = uuid.UUID("91aad1a5-9534-5720-865f-e229e55a3b92")
ARGUMENTS = frozenset(
    {
        "subject_entity_id",
        "predicate",
        "object_entity_id",
        "evidence_url",
        "note",
        "idempotency_key",
    }
)
EVIDENCE_URL_MAX = 2048
NOTE_MAX = 500
KEY_MAX = 64
# The inbox's CHECK (length BETWEEN 1 AND 256) on ``sub`` and ``cid``. Both come from the verified
# assertion, so an over-long one is no argument fault and no transient one: the INSERT would
# raise a CheckViolation the host maps to a retryable ``unavailable``, and the client would retry
# forever. ``blocked`` is the one closed D17 code that is final and blames no argument.
IDENTITY_MAX = 256
INBOX_FULL = "public MCP inbox full"  # mcp_inbox.cap_proposals() (RFC 0039 D13 hardening)
INSERT = (
    "INSERT INTO mcp_inbox.proposals (proposal_id, sub, cid, idempotency_key, "
    "subject_entity_id, predicate, object_entity_id, evidence_url, note) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT DO NOTHING"
)


def _evidence(url: str | None) -> str | None:
    """An absolute http(s) URL with a host, no credentials and no whitespace or control."""
    if url is None:
        return None
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in url):
        raise ReadError("invalid_argument", argument="evidence_url")
    try:
        parts = urlsplit(url)
        usable = (
            parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        usable = False
    if not usable:
        raise ReadError("invalid_argument", argument="evidence_url")
    return url


def payload_of(values: dict[str, Any]) -> dict[str, str | None]:
    """The digested payload: every proposal field but the key, absent optionals as null."""
    return {
        "subject_entity_id": values["subject_entity_id"],
        "predicate": values["predicate"],
        "object_entity_id": values["object_entity_id"],
        "evidence_url": values["evidence_url"],
        "note": values["note"],
    }


def proposal_id(sub: str, cid: str, idempotency_key: str, payload: dict[str, str | None]) -> str:
    digest = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    name = rfc8785.dumps([sub, cid, idempotency_key, digest]).decode()
    return str(uuid.uuid5(NS, name))


async def propose_fact(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    args = Arguments(arguments, ARGUMENTS)
    args.uuid("subject_entity_id")
    args.choice("predicate", proposable_predicates())
    args.uuid("object_entity_id")
    args.text("evidence_url", max_length=EVIDENCE_URL_MAX)
    _evidence(args.values["evidence_url"])
    args.text("note", max_length=NOTE_MAX)
    args.text("idempotency_key", max_length=KEY_MAX, required=True)
    context.require("proposals")
    if not context.cid:
        raise ReadError("unavailable")  # the host always sets it; never write an unowned row
    if len(context.sub) > IDENTITY_MAX or len(context.cid) > IDENTITY_MAX:
        raise ReadError("blocked")
    values = args.values
    pid = proposal_id(context.sub, context.cid, values["idempotency_key"], payload_of(values))
    try:
        await conn.execute(
            INSERT,
            uuid.UUID(pid),
            context.sub,
            context.cid,
            values["idempotency_key"],
            uuid.UUID(values["subject_entity_id"]),
            values["predicate"],
            uuid.UUID(values["object_entity_id"]),
            values["evidence_url"],
            values["note"],
        )
    except asyncpg.RaiseError as exc:
        if getattr(exc, "message", None) != INBOX_FULL:
            raise
        raise ReadError("unavailable") from None
    return {"proposal_id": pid, "status": "received"}
