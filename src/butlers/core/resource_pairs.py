"""Composite (pair) resource scope for a capability grant (bu-jv1moa.64).

A grant's resource scope is an exact-id set per argument name. Two arguments that only mean
something together, such as a mail ``(message_id, attachment_id)``, would otherwise be scoped
as two independent sets whose cross product permits recombining one admitted ref's message with
another's attachment. A descriptor that declares ``resource_pair`` is instead scoped by one
synthetic grant key whose values are encoded pairs.

The encoding is length-prefixed, ``"<len(first)>:<first><second>"``, so it parses one way only:
no separator inside an id can move the boundary or forge another pair. The grant's exact-id rule
(no ``* ? [ ]``) still applies to the encoded value, so an id with those characters can never be
admitted into a scope.
"""

from __future__ import annotations

import json
from collections.abc import Iterable


def pair_key(pair: tuple[str, str]) -> str:
    """The grant resource key that scopes ``pair`` (never a real argument name)."""
    return "+".join(pair)


def encode_pair(first: object, second: object) -> str | None:
    """The scope value for one pair, or ``None`` unless both are non-empty strings."""
    if type(first) is not str or type(second) is not str or not first or not second:
        return None
    return f"{len(first)}:{first}{second}"


def pair_scope(
    pair: tuple[str, str], refs: Iterable[tuple[str, str]]
) -> tuple[str, tuple[str, ...]]:
    """A ``Grant.resources`` entry that scopes ``pair`` to exactly ``refs``."""
    values = {encode_pair(first, second) for first, second in refs}
    if None in values or not values:
        raise ValueError("a pair scope needs non-empty string ids")
    return pair_key(pair), tuple(sorted(v for v in values if v is not None))


def lazy_attachment_refs(attachments: object) -> tuple[tuple[str, str], ...]:
    """The distinct ``(source_message_id, source_attachment_id)`` refs of an attachment list.

    The one reading of a message's stored attachments, shared by the routing context check and
    admission, so they cannot disagree about which refs a request carries. Accepts the list or its
    JSON text; anything else, and any entry without both string ids, yields nothing.
    """
    if isinstance(attachments, str):
        try:
            attachments = json.loads(attachments)
        except ValueError:
            return ()
    if not isinstance(attachments, list):
        return ()
    refs = {
        (att["source_message_id"], att["source_attachment_id"])
        for att in attachments
        if isinstance(att, dict)
        and isinstance(att.get("source_message_id"), str)
        and isinstance(att.get("source_attachment_id"), str)
    }
    return tuple(sorted(refs))
