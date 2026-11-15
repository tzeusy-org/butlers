"""Identity, templates and exemptions for external MCP fact proposals (RFC 0039 D13).

An outside MCP client may propose a Relationship fact. The deterministic
Relationship importer parks it through
:func:`butlers.modules.approvals.park.park_external_proposal` under the
deduplication key ``external_mcp:<proposal_id>``. That key prefix is the one
marker the approve paths read: an external proposal records no autonomy history,
never feeds a promotion suggestion and never becomes a standing rule, so outside
clients cannot steer approval policy for internal writes (REQ-module-approvals-010,
REQ-public-mcp-proposals-008).

Nothing here stores outside-authored text. Evidence carries the client label, the
evidence URL's scheme and host, and the SHA-256 of the full URL; the URL itself
and the note stay in ``relationship.external_fact_proposals``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

EXTERNAL_PROPOSAL_KEY_PREFIX = "external_mcp:"
#: The evidence ``ref`` prefix that carries the proposing client's label.
EXTERNAL_PROPOSAL_CLIENT_REF_PREFIX = "external_mcp:client="
EXTERNAL_PROPOSAL_TOOL = "relationship_assert_fact"
EXTERNAL_PROPOSAL_TTL = timedelta(days=14)

#: Provenance and authority are server-recorded in the Relationship fact approval
#: context. They are never stored arguments of an external proposal.
EXTERNAL_PROPOSAL_FORBIDDEN_ARGS = frozenset(
    {
        "src",
        "content_authority",
        "observed_at",
        "verified",
        "confirmed_at",
        "confirmation_source",
        "evidence",
        "why",
    }
)

EXTERNAL_PROPOSAL_RULE_REFUSAL = (
    "An external MCP proposal cannot create a standing approval rule; "
    "approve or reject this proposal on its own."
)

# A client label is what ``capability_host.assertion.client_label`` makes of a ``cid``: a CIMD
# host name, or ``dcr:`` and 12 hex digits. No space, slash, ``@`` or other colon, so a raw cid
# (a URL with a path) can never pass as a label.
_CLIENT_LABEL_RE = re.compile(r"(?:dcr:[0-9a-f]{12}|[A-Za-z0-9][A-Za-z0-9._-]{0,63})")
#: A registry predicate name; the only predicate shape an external proposal stores.
PREDICATE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_PREDICATE_RE = PREDICATE_NAME_RE


def external_proposal_key(proposal_id: uuid.UUID) -> str:
    """Return the deduplication key that marks one external proposal's action."""
    if not isinstance(proposal_id, uuid.UUID):
        raise ValueError("proposal_id must be a UUID")
    return f"{EXTERNAL_PROPOSAL_KEY_PREFIX}{proposal_id}"


def is_external_proposal_key(deduplication_key: Any) -> bool:
    """True when a pending action's deduplication key marks an external proposal."""
    return isinstance(deduplication_key, str) and deduplication_key.startswith(
        EXTERNAL_PROPOSAL_KEY_PREFIX
    )


def is_external_proposal_row(row: Any) -> bool:
    """True for a ``pending_actions`` row (record or mapping) parked from outside."""
    if row is None:
        return False
    try:
        key = row["deduplication_key"]
    except (KeyError, IndexError):
        return False
    return is_external_proposal_key(key)


async def is_external_proposal_action(connection: Any, action_id: uuid.UUID) -> bool:
    """Read whether *action_id* is an external proposal; unknown ids are not."""
    row = await connection.fetchrow(
        "SELECT deduplication_key FROM pending_actions WHERE id = $1", action_id
    )
    return is_external_proposal_row(row)


def validate_client_label(client_label: str) -> str:
    """Bound the client label, the only client-derived text an action stores."""
    if not isinstance(client_label, str) or not _CLIENT_LABEL_RE.fullmatch(client_label):
        raise ValueError("client_label must be a client host name or a dcr: digest label")
    return client_label


def evidence_url_host(evidence_url: str) -> str:
    """Return ``scheme://host[:port]`` of an http(s) URL, never userinfo or path."""
    parts = urlsplit(evidence_url)
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in {"http", "https"} or not host:
        raise ValueError("evidence_url must be an absolute http(s) URL")
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("evidence_url has an invalid port") from exc
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def external_proposal_evidence(
    client_label: str, evidence_url: str | None
) -> tuple[dict[str, str], ...]:
    """Digest-only evidence as typed ``{type, ref, note}`` references.

    The typed shape is the one the dashboard dossier model and the Relationship
    evidence ledger accept on approved replay; a free-form ``kind`` object would
    fail both. Each value is server-derived: the label is bounded, the host is
    parsed, and the URL survives only as its SHA-256.
    """
    label = validate_client_label(client_label)
    evidence: list[dict[str, str]] = [
        {
            "type": "text",
            "ref": f"{EXTERNAL_PROPOSAL_CLIENT_REF_PREFIX}{label}",
            "note": "Label of the external AI client that proposed this fact.",
        }
    ]
    if evidence_url is not None:
        if not isinstance(evidence_url, str):
            raise ValueError("evidence_url must be a string")
        evidence.append(
            {
                "type": "text",
                "ref": f"external_mcp:url_host={evidence_url_host(evidence_url)}",
                "note": "Scheme and host of the evidence URL; the full URL stays private.",
            }
        )
        digest = hashlib.sha256(evidence_url.encode("utf-8")).hexdigest()
        evidence.append(
            {
                "type": "text",
                "ref": f"external_mcp:url_sha256={digest}",
                "note": "SHA-256 of the full evidence URL.",
            }
        )
    return tuple(evidence)


def external_proposal_texts(tool_args: dict[str, Any]) -> tuple[str, str]:
    """Deterministic ``(agent_summary, why)`` templates; no outside free text."""
    predicate = tool_args.get("predicate")
    if not isinstance(predicate, str) or not _PREDICATE_RE.fullmatch(predicate):
        raise ValueError("external proposal predicate must be a registry predicate name")
    summary = f"External proposal: relationship fact '{predicate}'"
    why = (
        f"An external AI client proposed a '{predicate}' relationship between two "
        "existing people. It is written only if you approve it, keeps third-party "
        "authority when written, and expires unanswered after 14 days."
    )
    return summary, why


def validate_external_tool_args(action_id: uuid.UUID, tool_args: dict[str, Any]) -> None:
    """Refuse provenance-bearing or unreplayable stored arguments before any write."""
    if not isinstance(tool_args, dict):
        raise ValueError("tool_args must be a JSON object")
    forbidden = sorted(EXTERNAL_PROPOSAL_FORBIDDEN_ARGS & set(tool_args))
    if forbidden:
        raise ValueError(
            "external proposal arguments must not carry server-recorded provenance: "
            + ", ".join(forbidden)
        )
    for key in ("subject", "predicate", "object", "object_kind"):
        if not isinstance(tool_args.get(key), str) or not tool_args[key]:
            raise ValueError(f"external proposal argument {key!r} must be a non-empty string")
    # Without its own action id the replay would re-park or write as a fresh,
    # unattributed call instead of executing the owner's decision.
    if tool_args.get("approval_action_id") != str(action_id):
        raise ValueError("external proposal arguments must carry their own approval_action_id")


__all__ = [
    "EXTERNAL_PROPOSAL_CLIENT_REF_PREFIX",
    "EXTERNAL_PROPOSAL_FORBIDDEN_ARGS",
    "EXTERNAL_PROPOSAL_KEY_PREFIX",
    "EXTERNAL_PROPOSAL_RULE_REFUSAL",
    "EXTERNAL_PROPOSAL_TOOL",
    "EXTERNAL_PROPOSAL_TTL",
    "PREDICATE_NAME_RE",
    "evidence_url_host",
    "external_proposal_evidence",
    "external_proposal_key",
    "external_proposal_texts",
    "is_external_proposal_action",
    "is_external_proposal_key",
    "is_external_proposal_row",
    "validate_client_label",
    "validate_external_tool_args",
]
