"""Shared identity resolution utilities.

Provides ``resolve_contact_by_channel`` — the canonical reverse-lookup that
maps a channel identifier (type + value) to a known entity and their
associated roles and entity_id.

Migration bead 7 (bu-akads): reads from ``relationship.entity_facts`` triples
(predicate ``has-handle``, ``has-email``, ``has-phone``) joined to
``public.entities`` as the primary resolution path.

Used by:
- Switchboard ingestion path (before routing) to inject sender identity preambles.
- notify() to resolve outbound recipients from contact_id.
- Approval gate to replace name-heuristic with role-based target resolution.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any
from uuid import UUID

import asyncpg

# WhatsApp individual-chat JID suffix for s.whatsapp.net domain.
_WHATSAPP_INDIVIDUAL_JID_SUFFIX = "@s.whatsapp.net"
# Regex to extract the E.164-prefix phone number from a WhatsApp individual JID.
# Matches numeric prefixes like "1234567890" from "1234567890@s.whatsapp.net".
_WHATSAPP_JID_PHONE_RE = re.compile(r"^(\d+)@s\.whatsapp\.net$")

# Telegram channel types whose values may be usernames (with or without @-prefix).
# These also get the case-insensitive username-variant normalization below.
_TELEGRAM_USERNAME_CHANNEL_TYPES: frozenset[str] = frozenset({"telegram", "telegram_username"})

# All Telegram channel types.  Telegram ``has-handle`` triples are stored
# canonically with a ``telegram:`` prefix (migration rel_019), but callers pass
# the bare value — a numeric chat id ("206570151"), an @username, or an already
# prefixed value.  Every telegram channel therefore gets the ``telegram:``-prefix
# resolution fallback (not just ``telegram_user_client``), so a numeric chat id
# from ``telegram_send_message`` or an inbound ``telegram_bot`` sender resolves to
# its prefixed triple.
_TELEGRAM_PREFIX_CHANNEL_TYPES: frozenset[str] = frozenset(
    {
        "telegram",
        "telegram_user_id",
        "telegram_user_client",
        "telegram_username",
        "telegram_bot",
        "telegram_chat_id",
    }
)


def _telegram_prefixed_value(channel_value: str) -> str:
    """Return the canonical ``telegram:<bare>`` form for a Telegram channel value."""
    bare = channel_value
    if bare.startswith("telegram:"):
        bare = bare[len("telegram:") :]
    bare = bare.lstrip("@")
    return f"telegram:{bare}"


def normalize_email_sender(raw: str) -> str:
    """Reduce a raw email ``From:`` header (or bare address) to a lowercased address.

    Ingestion connectors and identity/backfill code paths need ONE canonical
    form for an email sender identity so that grouping, deduplication, and
    ``has-email`` fact matching all agree.  A raw RFC-5322 header looks like
    ``"John Doe <john@example.com>"``; ``has-email`` facts (and
    ``entity_facts``-driven matching generally) store bare, lowercased
    addresses.  This is the single shared normalizer for that reduction —
    used at ingest (``connectors/gmail.py``), by the chronicler comms
    projection adapter, and by the email identity enrichment/backfill jobs
    (bu-qeaou).

    Falls back to the stripped/lowercased raw value when no ``<...>``-
    delimited address is present — :func:`email.utils.parseaddr` already
    handles the bare-address case by returning it verbatim in the second
    tuple slot, so calling this on an already-normalized address is a no-op.
    """
    _, address = parseaddr(raw or "")
    return (address or raw or "").strip().lower()


def parse_email_sender(raw: str) -> tuple[str | None, str]:
    """Split a raw email ``From:`` header into ``(display_name, address)``.

    Companion to :func:`normalize_email_sender`. Both delegate to
    :func:`email.utils.parseaddr`, which returns ``(display_name, address)`` for
    a header like ``"John Doe <john@example.com>"`` — but ``normalize_email_sender``
    deliberately discards slot 0. This helper preserves it so the ingest write
    path can persist the real display name alongside the normalized address
    (bu-vs9cr), instead of downstream code guessing a name from the address
    local-part.

    The returned ``address`` is byte-for-byte identical to what
    ``normalize_email_sender`` would return for the same input (bare, stripped,
    lowercased) so the two never disagree on the canonical address form. The
    returned ``display_name`` is stripped; empty / whitespace-only display names
    collapse to ``None``. No further junk filtering (name == address,
    all-punctuation) happens here — that is a consumer concern (see
    ``email_identity_matching.clean_stored_display_name``) so this helper stays a
    faithful parse of the header.
    """
    display_name, address = parseaddr(raw or "")
    normalized_address = (address or raw or "").strip().lower()
    display_name = (display_name or "").strip()
    return (display_name or None, normalized_address)


def channel_value_for_storage(channel_type: str, channel_value: str) -> str:
    """Return the canonical ``relationship.entity_facts`` storage form for a value.

    Telegram channel handles are normalised to the ``telegram:<bare>`` form (any
    pre-existing ``telegram:`` prefix is stripped and a leading ``@`` removed,
    then the prefix is re-applied) so that storage, resolution
    (``resolve_contact_by_channel``), and delivery
    (``daemon._resolve_entity_channel_identifier``: ``LIKE 'telegram:%'``) all
    agree on ONE format.  This is the write-side counterpart of the read-side
    telegram-prefix fallback and mirrors the Phase 2 backfill (rel_028) and
    ingress writer (:func:`assert_sender_channel_fact`).

    Non-telegram channel values are returned unchanged.

    Parameters
    ----------
    channel_type:
        Source channel type (e.g. ``"telegram"``, ``"telegram_chat_id"``,
        ``"email"``).
    channel_value:
        The raw identifier as entered/observed.
    """
    if channel_type in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        return _telegram_prefixed_value(channel_value)
    return channel_value


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel-type → relationship.entity_facts predicate mapping (bead 7 cut-over)
# Must stay in sync with
# relationship_assert_fact._CI_TYPE_TO_PREDICATE
# relationship_jobs._CI_TYPE_TO_PREDICATE (and its SQL CASE expression)
#
# Seam law (RFC 0004 Amendment 3, bu-oluyt.1): relationship.entity_facts is
# the single source of truth for ALL non-secret facts / identifiers /
# relationships.  public.entity_info holds ONLY secured=True credentials.
# telegram_chat_id is a non-secret routing handle — its canonical home is a
# has-handle triple (prefixed 'telegram:<id>') in entity_facts, NOT entity_info.
# ---------------------------------------------------------------------------
_CHANNEL_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_user_client": "has-handle",
    "telegram_username": "has-handle",
    "telegram_chat_id": "has-handle",  # non-secret routing handle → entity_facts
    "telegram_bot": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
    "whatsapp_jid": "has-handle",
}


def _extract_whatsapp_jid_phone(jid: str) -> str | None:
    """Extract E.164-prefix phone number from a WhatsApp individual JID.

    Parameters
    ----------
    jid:
        WhatsApp JID string (e.g., ``"1234567890@s.whatsapp.net"``).

    Returns
    -------
    str | None
        The phone number string (e.g., ``"1234567890"``), or ``None`` if the
        JID is not an individual JID (e.g., group JIDs ending in ``@g.us``).
    """
    m = _WHATSAPP_JID_PHONE_RE.match(jid)
    return m.group(1) if m else None


def _telegram_username_candidates(value: str) -> list[str]:
    """Return the ordered list of username variants to try for a Telegram lookup.

    The canonical storage form (set by the contacts backfill) strips the leading
    ``@``.  However outbound tools (e.g. ``telegram_send_message``) accept
    ``@Username`` (the user-facing form).  Telegram usernames are also
    case-insensitive on the platform.

    This function generates the candidate set that covers all practical
    permutations so that ``resolve_contact_by_channel`` and
    ``approvals._shared.is_primary_contact`` can normalise on the fly without
    requiring the caller to know the canonical storage form.

    The first entry is always the original value (exact-match wins) so that
    numeric chat IDs (``"206570151"``) succeed on the first attempt and never
    enter the username-variant loop.  Purely numeric bare values are NOT
    expanded with @-prefix variants because Telegram usernames must begin with
    a letter; only alphanumeric or @-prefixed values get the full candidate set.

    Parameters
    ----------
    value:
        The raw channel value (e.g. ``"@Tzeusy"``, ``"Tzeusy"``, ``"tzeusy"``,
        ``"206570151"``).

    Returns
    -------
    list[str]
        Ordered candidate list.  Exact match first, then @-stripped, then
        @-prefixed, then lowercase variants of each.  For numeric-only bare
        values, only the exact value is returned (no username expansion).
        Duplicates are removed while preserving order.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(v: str) -> None:
        if v and v not in seen:
            seen.add(v)
            candidates.append(v)

    bare = value.lstrip("@")

    # Numeric-only values are Telegram chat IDs (not usernames).  Telegram
    # usernames must start with a letter.  Adding @-prefix variants for
    # pure-numeric values would be incorrect and would add spurious DB queries.
    # Negative IDs (supergroups) start with '-' followed by digits; handle both.
    if bare.lstrip("-").isdigit() and bare:
        _add(value)
        return candidates

    prefixed = f"@{bare}"

    _add(value)  # exact (succeeds immediately when the caller knows the form)
    _add(bare)  # without @ (canonical storage form from the backfill)
    _add(prefixed)  # with @
    _add(value.lower())
    _add(bare.lower())
    _add(prefixed.lower())

    return candidates


@dataclass(frozen=True)
class ResolvedContact:
    """Resolved contact identity from a channel reverse-lookup.

    Attributes
    ----------
    contact_id:
        UUID of the resolved contact in public.contacts.  May be ``None``
        after the bead-7 cut-over when resolution goes through
        ``relationship.entity_facts`` (entity_id is the authoritative key).
    name:
        Display name of the contact (may be ``None`` if not set).
    roles:
        List of roles assigned to the linked entity (e.g., ``['owner']``).
        Sourced from ``public.entities.roles``.
    entity_id:
        UUID of the linked entity in public.entities, or ``None`` if not linked.
    """

    contact_id: UUID | None
    name: str | None
    roles: list[str]
    entity_id: UUID | None


async def _resolve_entity_by_triple(
    pool: asyncpg.Pool,
    predicate: str,
    object_value: str,
) -> asyncpg.Record | None:
    """Query ``relationship.entity_facts`` for an active triple and join entity info.

    Returns a row with ``entity_id``, ``name`` (canonical_name), and ``roles``,
    or ``None`` when not found or on DB error.
    """
    try:
        return await pool.fetchrow(
            """
            SELECT ef.subject                     AS entity_id,
                   e.canonical_name               AS name,
                   COALESCE(e.roles, '{}')        AS roles
            FROM   relationship.entity_facts ef
            JOIN   public.entities e ON e.id = ef.subject
            WHERE  ef.predicate    = $1
              AND  ef.object       = $2
              AND  ef.object_kind  = 'literal'
              AND  ef.validity     = 'active'
            LIMIT  1
            """,
            predicate,
            object_value,
        )
    except Exception:  # noqa: BLE001
        return None


async def resolve_contact_by_channel(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> ResolvedContact | None:
    """Resolve an entity from a channel identifier via ``relationship.entity_facts``.

    Queries ``relationship.entity_facts`` to map a channel identifier to a known
    entity.  Roles and canonical name are read from ``public.entities``.
    Returns ``None`` when no entity is found for the given (type, value) pair.

    Migration bead 7 (bu-akads): this function now queries the triples store
    (``relationship.entity_facts``) directly, using predicates ``has-handle``,
    ``has-email``, and ``has-phone``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The executing role must have at minimum
        ``SELECT`` on ``relationship.entity_facts`` and ``public.entities``.
    channel_type:
        The channel type (e.g., ``"telegram"``, ``"email"``).
    channel_value:
        The channel value (e.g., a Telegram chat ID string or an email address).

    Returns
    -------
    ResolvedContact | None
        A populated ``ResolvedContact`` on success, or ``None`` if no match
        is found or the tables do not yet exist.

    Notes
    -----
    - ``entity_id`` is the authoritative key post bead 7.  ``contact_id`` on
      the returned dataclass will be ``None`` since we no longer query
      ``public.contacts``.
    - This function is safe to call if the migration has not yet run —
      it returns ``None`` gracefully.
    """
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    row: asyncpg.Record | None = None

    if predicate is not None:
        try:
            row = await _resolve_entity_by_triple(pool, predicate, channel_value)
        except Exception:  # noqa: BLE001
            logger.debug(
                "resolve_contact_by_channel: DB query failed "
                "(table may not exist yet); returning None",
                exc_info=True,
            )
            return None

    if row is None and channel_type in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        # Telegram canonical-prefix fallback: handles are stored as telegram:<bare>
        # (rel_019).  Try the prefixed form so a numeric chat id from
        # telegram_send_message or an inbound telegram_bot sender resolves to its
        # triple.  (@-username variants are handled separately below.)
        telegram_value = _telegram_prefixed_value(channel_value)
        try:
            row = await _resolve_entity_by_triple(pool, "has-handle", telegram_value)
        except Exception:  # noqa: BLE001
            logger.debug(
                "resolve_contact_by_channel: telegram prefix fallback query failed",
                exc_info=True,
            )
            return None

    if row is None and channel_type in _TELEGRAM_USERNAME_CHANNEL_TYPES:
        # Telegram username normalization fallback (bu-c4f7f).
        # telegram_send_message accepts @Username (user-facing form); the backfill
        # stores the bare username without @ (canonical form). Telegram usernames
        # are also case-insensitive on the platform.  Try all normalised variants
        # so that '@Tzeusy', 'Tzeusy', 'tzeusy' all resolve to the same entity.
        # The exact-match attempt above (first candidate) has already run; start
        # from the second candidate to avoid a redundant query.
        for candidate in _telegram_username_candidates(channel_value)[1:]:
            try:
                row = await _resolve_entity_by_triple(pool, "has-handle", candidate)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "resolve_contact_by_channel: telegram username variant query failed "
                    "(candidate=%r); returning None",
                    candidate,
                    exc_info=True,
                )
                return None
            if row is not None:
                logger.debug(
                    "resolve_contact_by_channel: telegram username normalised from %r to %r",
                    channel_value,
                    candidate,
                )
                break

    if row is None:
        # WhatsApp JID fallback: if no direct match, try phone-number cross-reference.
        # Extracts the E.164 phone prefix from "<number>@s.whatsapp.net" JIDs and
        # queries has-phone to link against entities from other providers
        # (e.g. Google Contacts) that share the same number.
        if channel_type == "whatsapp_jid":
            phone = _extract_whatsapp_jid_phone(channel_value)
            if phone is not None:
                try:
                    row = await _resolve_entity_by_triple(pool, "has-phone", phone)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "resolve_contact_by_channel: phone fallback query failed; returning None",
                        exc_info=True,
                    )
                    return None
        if row is None:
            return None

    entity_id = row["entity_id"]
    if not isinstance(entity_id, UUID):
        try:
            entity_id = UUID(str(entity_id))
        except (ValueError, AttributeError):
            return None

    raw_roles = row["roles"]
    if isinstance(raw_roles, (list, tuple)):
        roles = [str(r) for r in raw_roles]
    else:
        roles = []

    return ResolvedContact(
        contact_id=None,  # entity_id is now the authoritative key (bead 7)
        name=row["name"] or None,
        roles=roles,
        entity_id=entity_id,
    )


def _channel_candidates(channel_type: str, channel_value: str) -> list[tuple[str, str]]:
    """Return the ordered ``(predicate, object)`` candidate list for one channel.

    Mirrors the fallback chain :func:`resolve_contact_by_channel` walks
    sequentially (primary predicate → telegram canonical-prefix → telegram
    username variants → whatsapp phone cross-reference), but as a flat,
    priority-ordered list so :func:`resolve_contacts_by_channel_bulk` can
    batch every candidate for a whole page into a single query.
    """
    candidates: list[tuple[str, str]] = []
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    if predicate is not None:
        candidates.append((predicate, channel_value))

    if channel_type in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        candidates.append(("has-handle", _telegram_prefixed_value(channel_value)))

    if channel_type in _TELEGRAM_USERNAME_CHANNEL_TYPES:
        # Skip index 0 — it's the exact value already covered by the primary
        # predicate candidate above.
        for variant in _telegram_username_candidates(channel_value)[1:]:
            candidates.append(("has-handle", variant))

    if channel_type == "whatsapp_jid":
        phone = _extract_whatsapp_jid_phone(channel_value)
        if phone is not None:
            candidates.append(("has-phone", phone))

    return candidates


async def resolve_contacts_by_channel_bulk(
    pool: asyncpg.Pool,
    channels: list[tuple[str, str]],
) -> dict[tuple[str, str], ResolvedContact | None]:
    """Batch variant of :func:`resolve_contact_by_channel` for list views.

    Built for the ingestion timeline list endpoint (bu-4utdw.3): resolving
    every row's sender via the single-item helper would fire one query per
    row (an N+1 storm on a 50-row page). This computes the same candidate
    fallback chain per ``(channel_type, channel_value)`` pair, then issues
    **one** grouped query against ``relationship.entity_facts`` /
    ``public.entities`` for the union of all candidates across the page.

    Args:
        pool: asyncpg pool with ``SELECT`` on ``relationship.entity_facts``
            and ``public.entities`` (the same shared pool
            :func:`resolve_contact_by_channel` is called with).
        channels: List of ``(channel_type, channel_value)`` pairs to resolve.
            Duplicates are fine — the underlying query is deduplicated.

    Returns:
        A dict mapping every input pair to its ``ResolvedContact`` (or
        ``None`` when unresolved). Fail-open: on DB error, all pairs map to
        ``None`` rather than raising.
    """
    result: dict[tuple[str, str], ResolvedContact | None] = dict.fromkeys(channels)
    if not channels:
        return result

    candidates_by_pair: dict[tuple[str, str], list[tuple[str, str]]] = {}
    wanted_pairs: set[tuple[str, str]] = set()
    for channel_type, channel_value in channels:
        if not channel_type or not channel_value:
            continue
        pair_candidates = _channel_candidates(channel_type, channel_value)
        if not pair_candidates:
            continue
        candidates_by_pair[(channel_type, channel_value)] = pair_candidates
        wanted_pairs.update(pair_candidates)

    if not wanted_pairs:
        return result

    predicates, objects = map(list, zip(*wanted_pairs))

    try:
        rows = await pool.fetch(
            """
            SELECT ef.predicate            AS predicate,
                   ef.object               AS object,
                   ef.subject              AS entity_id,
                   e.canonical_name        AS name,
                   COALESCE(e.roles, '{}') AS roles
            FROM   relationship.entity_facts ef
            JOIN   public.entities e ON e.id = ef.subject
            JOIN   unnest($1::text[], $2::text[]) AS wanted(predicate, object)
              ON   ef.predicate = wanted.predicate AND ef.object = wanted.object
            WHERE  ef.object_kind = 'literal'
              AND  ef.validity    = 'active'
            """,
            predicates,
            objects,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "resolve_contacts_by_channel_bulk: DB query failed "
            "(table may not exist yet); returning all-unresolved",
            exc_info=True,
        )
        return result

    match_by_candidate: dict[tuple[str, str], asyncpg.Record] = {}
    for row in rows:
        match_by_candidate.setdefault((row["predicate"], row["object"]), row)

    for pair, pair_candidates in candidates_by_pair.items():
        for candidate in pair_candidates:
            row = match_by_candidate.get(candidate)
            if row is None:
                continue
            entity_id = row["entity_id"]
            if not isinstance(entity_id, UUID):
                try:
                    entity_id = UUID(str(entity_id))
                except (ValueError, AttributeError):
                    continue
            raw_roles = row["roles"]
            roles = [str(r) for r in raw_roles] if isinstance(raw_roles, (list, tuple)) else []
            result[pair] = ResolvedContact(
                contact_id=None,
                name=row["name"] or None,
                roles=roles,
                entity_id=entity_id,
            )
            break

    return result


async def resolve_owner_channel_via_definer(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> tuple[ResolvedContact, bool] | None:
    """Resolve an OWNER channel through the ``public.resolve_owner_triple`` function.

    :func:`resolve_contact_by_channel` and :func:`is_primary_contact` read
    ``relationship.entity_facts`` directly. A non-relationship butler runs under a
    schema-isolated role (``SET ROLE butler_<schema>_rw``) that cannot read that
    table, so those helpers return ``None`` even for owner-directed sends, and the
    approval gate parks the message as "unresolvable target".

    This helper instead calls the ``SECURITY DEFINER`` lookup added in migration
    ``core_145``, which runs as its owner (a role with relationship-schema read
    access) and returns only owner matches. The channel-type → predicate mapping
    and value normalisation live here (mirroring
    :func:`resolve_contact_by_channel`); the function receives the predicate plus
    pre-normalised candidate object values.

    Returns ``(owner_contact, is_primary)`` when *channel_value* is one of the
    owner's registered handles for *channel_type*, else ``None`` (not an owner
    channel, unknown channel type, or the function is unavailable).
    """
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    if predicate is None:
        return None

    # Candidate object values: verbatim, plus telegram canonical-prefix and
    # username variants — the same normalisation resolve_contact_by_channel applies.
    candidates: list[str] = [channel_value]
    if channel_type in _TELEGRAM_USERNAME_CHANNEL_TYPES:
        for variant in _telegram_username_candidates(channel_value):
            if variant not in candidates:
                candidates.append(variant)
    if channel_type in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        for variant in list(candidates):
            prefixed = _telegram_prefixed_value(variant)
            if prefixed not in candidates:
                candidates.append(prefixed)

    try:
        row = await pool.fetchrow(
            "SELECT entity_id, is_primary FROM public.resolve_owner_triple($1, $2)",
            predicate,
            candidates,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "resolve_owner_channel_via_definer: lookup failed "
            "(function may not exist yet); returning None",
            exc_info=True,
        )
        return None

    if row is None:
        return None

    entity_id = row.get("entity_id")
    if entity_id is None:
        return None
    if not isinstance(entity_id, UUID):
        try:
            entity_id = UUID(str(entity_id))
        except (ValueError, AttributeError):
            return None

    owner_contact = ResolvedContact(
        contact_id=None,
        name=None,
        roles=["owner"],
        entity_id=entity_id,
    )
    return owner_contact, bool(row["is_primary"])


async def create_temp_contact(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
    display_name: str | None = None,
    *,
    reservation_state_key: str | None = None,
) -> ResolvedContact | None:
    """Create a temporary entity for an unknown sender.

    Creates a ``public.entities`` entry with ``metadata.unidentified = true``.
    Phase 7 (bu-jnaa3): it no longer writes a ``public.contacts`` row — the
    returned ``ResolvedContact.contact_id`` is always ``None`` and ``entity_id``
    is the authoritative identity. It does NOT write the sender's channel triple
    to ``relationship.entity_facts``.

    entity-v3 (bu-hvrt1): the channel-triple assertion — the existing-sender
    dedup key ``resolve_contact_by_channel()`` reads — was moved OUT of this
    function (and off the Switchboard ingress path) into a deterministic
    post-resolution hook in the routing pipeline:
    ``relationship.tools.relationship_assert_fact.assert_sender_channel_fact()``.
    Switchboard ingress must never write ``relationship.entity_facts``
    (switchboard-identity invariant). Existing-sender detection still queries the
    triple store via ``resolve_contact_by_channel()`` (bead-7 read cut-over).

    Parameters
    ----------
    pool:
        asyncpg connection pool.  Role must have INSERT on public.entities.
    channel_type:
        Channel type (e.g., ``"telegram"``).
    channel_value:
        Channel value (the raw sender identifier).
    display_name:
        Optional human-readable name for the contact.  Defaults to a
        synthesized ``"Unknown ({channel_type} {channel_value})"`` label.
    reservation_state_key:
        Optional caller-owned ``state`` key used to atomically reserve the
        temporary entity for one sender.  The Switchboard passes this only for
        its unknown-sender ingress path, where concurrent first messages can
        arrive before the relationship-owned channel-fact hook has established
        its normal reverse-lookup key.  The reservation and entity insert share
        one transaction; this helper still never writes
        ``relationship.entity_facts``.

    Returns
    -------
    ResolvedContact | None
        The newly created (or pre-existing) contact, or ``None`` on error.
    """
    name = display_name or f"Unknown ({channel_type} {channel_value})"

    try:
        # Re-check via the triple store to avoid double-creation: if the channel
        # identifier already resolves to an entity, return that instead of
        # minting a duplicate.  This mirrors resolve_contact_by_channel().
        existing_resolved = await resolve_contact_by_channel(pool, channel_type, channel_value)
        if existing_resolved is not None:
            return existing_resolved

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Re-check under the transaction (on the acquired connection) to
                # close the duplicate-creation race: two concurrent callers can
                # both pass the pre-transaction lookup above and each mint a
                # duplicate unidentified entity/contact for the same channel.
                # Re-resolving here on ``conn`` collapses that window — if the
                # channel now resolves, return it instead of creating a dup.
                existing_in_txn = await resolve_contact_by_channel(
                    conn, channel_type, channel_value
                )
                if existing_in_txn is not None:
                    return existing_in_txn

                if reservation_state_key is not None:
                    # The relationship-owned post-resolution hook establishes
                    # the normal channel-fact dedup key only after this helper
                    # returns.  Reserve this sender's entity in the
                    # Switchboard-local state table first, so concurrent first
                    # messages cannot both mint an entity during that gap.
                    #
                    # ``ON CONFLICT DO NOTHING`` waits for a concurrent winner
                    # to commit or abort.  The following row lock therefore
                    # sees either the committed winner's entity UUID or an
                    # empty reservation this transaction now owns.
                    await conn.execute(
                        """
                        INSERT INTO state (key, value, updated_at, version)
                        VALUES ($1, '{}'::jsonb, now(), 1)
                        ON CONFLICT (key) DO NOTHING
                        """,
                        reservation_state_key,
                    )
                    reservation = await conn.fetchrow(
                        "SELECT value FROM state WHERE key = $1 FOR UPDATE",
                        reservation_state_key,
                    )
                    if reservation is None:
                        raise RuntimeError(
                            "Temporary-entity reservation disappeared before it could be read"
                        )

                    reservation_value = reservation["value"]
                    raw_reserved_entity_id = (
                        reservation_value.get("entity_id")
                        if isinstance(reservation_value, dict)
                        else None
                    )
                    if raw_reserved_entity_id is not None:
                        try:
                            reserved_entity_id = UUID(str(raw_reserved_entity_id))
                        except (TypeError, ValueError, AttributeError):
                            logger.warning(
                                "create_temp_contact: invalid temporary-entity reservation for %s",
                                reservation_state_key,
                            )
                            reserved_entity_id = None

                        if reserved_entity_id is not None:
                            reserved_entity = await conn.fetchrow(
                                """
                                SELECT canonical_name, roles
                                FROM public.entities
                                WHERE id = $1
                                """,
                                reserved_entity_id,
                            )
                            if reserved_entity is not None:
                                raw_roles = reserved_entity["roles"]
                                return ResolvedContact(
                                    contact_id=None,
                                    name=reserved_entity["canonical_name"] or None,
                                    roles=(
                                        [str(role) for role in raw_roles]
                                        if isinstance(raw_roles, (list, tuple))
                                        else []
                                    ),
                                    entity_id=reserved_entity_id,
                                )

                            logger.warning(
                                "create_temp_contact: temporary-entity reservation %s references "
                                "missing entity %s; replacing it",
                                reservation_state_key,
                                reserved_entity_id,
                            )

                        # A manually deleted/corrupt reservation must not
                        # strand this sender. The row lock serializes repair
                        # with another first-message worker before a replacement
                        # entity is minted below.
                        await conn.execute(
                            """
                            UPDATE state
                            SET value = '{}'::jsonb, updated_at = now(), version = version + 1
                            WHERE key = $1
                            """,
                            reservation_state_key,
                        )

                    # A fact may have landed while this worker waited on a
                    # sender reservation. Prefer that canonical resolution over
                    # minting another transitory entity.
                    existing_after_reservation = await resolve_contact_by_channel(
                        conn, channel_type, channel_value
                    )
                    if existing_after_reservation is not None:
                        return existing_after_reservation

                # Create an unidentified entity so facts can be anchored.
                entity_metadata: dict[str, Any] = {
                    "unidentified": True,
                    "source_channel": channel_type,
                    "source_value": channel_value,
                }
                entity_id: UUID = await conn.fetchval(
                    """
                    INSERT INTO public.entities
                        (canonical_name, entity_type, aliases, metadata, roles)
                    VALUES ($1, 'person', '{}', $2, '{}')
                    RETURNING id
                    """,
                    name,
                    entity_metadata,
                )

                if reservation_state_key is not None:
                    await conn.execute(
                        """
                        UPDATE state
                        SET value = jsonb_build_object('entity_id', $2::text),
                            updated_at = now(),
                            version = version + 1
                        WHERE key = $1
                        """,
                        reservation_state_key,
                        str(entity_id),
                    )

        # Phase 7 (bu-jnaa3): create_temp_contact NO LONGER writes a
        # public.contacts row — the contact object is being retired and
        # ``entity_id`` is the authoritative identity. ``contact_id`` is always
        # ``None`` for freshly-minted senders; callers key off ``entity_id``.
        #
        # entity-v3 (bu-hvrt1): it also does NOT write the sender's channel
        # triple to relationship.entity_facts. Switchboard ingress must not write
        # entity_facts (switchboard-identity invariant); the channel-triple
        # assertion — the existing-sender dedup key resolve_contact_by_channel()
        # reads — is asserted deterministically from the routing pipeline via
        # ``relationship.tools.relationship_assert_fact.assert_sender_channel_fact``.

        return ResolvedContact(
            contact_id=None,
            name=name,
            roles=[],
            entity_id=entity_id,
        )

    except Exception:  # noqa: BLE001
        logger.warning(
            "create_temp_contact: failed to create temporary contact for %s/%s",
            channel_type,
            channel_value,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Outbound preferred-channel resolution (entity-keyed-preferred-channel, group 2)
# ---------------------------------------------------------------------------
#
# notify() lets the caller force a delivery channel. When the caller leaves the
# channel unspecified for a contact-targeted notification, this helper picks the
# channel: it honours the contact entity's active ``prefers-channel`` fact when
# that channel is currently deliverable, otherwise it falls back to the existing
# contact-fact precedence (telegram handle → email). Reachability validation and
# the channel→has-* mapping are reused from the group-1 fact writer rather than
# duplicated here, so "prefer a channel you can't be reached on" stays
# unrepresentable in exactly one place.

#: Outbound channel precedence for the no-preference / preference-not-deliverable
#: fallback. Ordered most- to least-preferred; mirrors the historical
#: telegram-then-email default. Each entry must be a member of the deliverable
#: set passed to :func:`resolve_outbound_channel` to be selected.
_OUTBOUND_FALLBACK_PRECEDENCE: tuple[str, ...] = ("telegram", "email")


async def _active_prefers_channel(
    conn: asyncpg.Connection,
    entity_id: UUID,
    prefers_channel_predicate: str,
) -> str | None:
    """Return *entity_id*'s active ``prefers-channel`` object, or ``None``.

    ``prefers-channel`` is single-valued (group 1 supersession), so at most one
    active row exists; ``LIMIT 1`` is belt-and-suspenders.
    """
    object_value = await conn.fetchval(
        """
        SELECT object
        FROM relationship.entity_facts
        WHERE subject     = $1
          AND predicate   = $2
          AND object_kind = 'literal'
          AND validity    = 'active'
        LIMIT 1
        """,
        entity_id,
        prefers_channel_predicate,
    )
    if not object_value:
        return None
    channel = str(object_value).strip()
    return channel or None


async def resolve_outbound_channel(
    pool: asyncpg.Pool,
    entity_id: UUID,
    *,
    deliverable_channels: set[str],
    conn: asyncpg.Connection | None = None,
) -> str | None:
    """Pick the outbound channel for an entity-targeted notification.

    Used by :func:`notify` only when the caller did NOT force a ``channel``.

    Resolution (entity-keyed-preferred-channel, core-notify spec):

    1. Read the entity's active ``prefers-channel`` fact. When set, the channel
       is honoured ONLY when it is both in *deliverable_channels* and currently
       reachable (the entity has the matching ``has-*`` contact fact, validated
       by the group-1 ``_entity_has_reachability_fact`` helper). A preference for
       a non-deliverable channel (e.g. ``discord``) or an unreachable one is
       skipped without error.
    2. Otherwise fall back to the existing reachability precedence
       (``_OUTBOUND_FALLBACK_PRECEDENCE``: telegram handle → email), returning the
       first deliverable channel the entity is reachable on.

    Parameters
    ----------
    pool:
        asyncpg connection pool (used when *conn* is ``None``).
    entity_id:
        UUID of the target entity in ``public.entities``. Unknown / unreachable
        entity → return ``None`` (caller decides default).
    deliverable_channels:
        The set of channels delivery can currently reach (notify's supported set,
        today ``{"telegram", "email"}``). A preference outside this set is
        ignored; fallback candidates outside it are skipped.
    conn:
        Optional open connection (pass when already inside a transaction).

    Returns
    -------
    str | None
        The chosen channel name, or ``None`` when no deliverable channel could be
        resolved for the entity.
    """
    # Reuse group-1's reachability check and predicate name rather than
    # re-implementing the channel→has-* mapping (single source of truth).
    from butlers.tools.relationship.relationship_assert_fact import (
        PREFERS_CHANNEL_PREDICATE,
        _entity_has_reachability_fact,
    )

    async def _resolve(c: asyncpg.Connection) -> str | None:
        # 1. Honour an active preference when deliverable AND reachable.
        preferred = await _active_prefers_channel(c, entity_id, PREFERS_CHANNEL_PREDICATE)
        if (
            preferred is not None
            and preferred in deliverable_channels
            and await _entity_has_reachability_fact(c, entity_id, preferred)
        ):
            return preferred

        # 2. Fallback precedence: first deliverable, reachable channel.
        for candidate in _OUTBOUND_FALLBACK_PRECEDENCE:
            if candidate not in deliverable_channels:
                continue
            if await _entity_has_reachability_fact(c, entity_id, candidate):
                return candidate
        return None

    try:
        if conn is not None:
            return await _resolve(conn)
        async with pool.acquire() as acquired_conn:
            return await _resolve(acquired_conn)
    except Exception:  # noqa: BLE001
        # Schema-not-ready or transient DB error: degrade to "no resolution" so
        # notify() falls through to its caller-supplied / owner-default path
        # rather than failing the notification outright.
        logger.debug(
            "resolve_outbound_channel: resolution failed for entity_id=%s; returning None",
            entity_id,
            exc_info=True,
        )
        return None


def build_identity_preamble(
    resolved: ResolvedContact | None,
    channel: str,
    temp_contact_id: UUID | None = None,
    temp_entity_id: UUID | None = None,
) -> str:
    """Build the structured identity preamble for a routed prompt.

    Migration bead 7 (bu-akads): ``contact_id`` is no longer included in the
    preamble output.  ``entity_id`` is the canonical identifier.

    Parameters
    ----------
    resolved:
        A ``ResolvedContact`` for a known sender, or ``None`` for unknown.
    channel:
        The source channel (e.g., ``"telegram"``).
    temp_contact_id:
        contact_id of the temporary contact (may be ``None``). Used as a
        fallback identifier in the preamble for unknown senders when
        ``temp_entity_id`` is not available (``create_temp_contact`` always
        returns a contact_id), emitted as ``(contact_id: <uuid>)``.
    temp_entity_id:
        entity_id of the temporary contact (may be ``None``).

    Returns
    -------
    str
        A formatted preamble line, e.g.:
        - ``"[Source: Owner (entity_id: <uuid>), via telegram]"``
        - ``"[Source: Chloe (entity_id: <uuid>), via telegram]"``
        - ``"[Source: Unknown sender (entity_id: <uuid>), via telegram --
          pending disambiguation]"``
    """
    if resolved is not None:
        eid = resolved.entity_id
        if "owner" in resolved.roles:
            if eid is not None:
                return f"[Source: Owner (entity_id: {eid}), via {channel}]"
            return f"[Source: Owner, via {channel}]"
        # Known non-owner
        name = resolved.name or "Unknown Contact"
        if eid is not None:
            return f"[Source: {name} (entity_id: {eid}), via {channel}]"
        return f"[Source: {name}, via {channel}]"

    # Unknown sender
    if temp_entity_id is not None:
        return (
            f"[Source: Unknown sender (entity_id: {temp_entity_id}), "
            f"via {channel} -- pending disambiguation]"
        )
    if temp_contact_id is not None:
        # Fallback for create_temp_contact which always returns a contact_id
        return (
            f"[Source: Unknown sender (contact_id: {temp_contact_id}), "
            f"via {channel} -- pending disambiguation]"
        )

    return f"[Source: Unknown sender, via {channel} -- pending disambiguation]"


__all__ = [
    "ResolvedContact",
    "build_identity_preamble",
    "create_temp_contact",
    "normalize_email_sender",
    "parse_email_sender",
    "resolve_contact_by_channel",
    "resolve_contacts_by_channel_bulk",
    "resolve_outbound_channel",
    # Telegram normalization helpers — consumed by approvals._shared to keep
    # is_primary_contact consistent with resolve_contact_by_channel.
    "_telegram_username_candidates",
    "_TELEGRAM_USERNAME_CHANNEL_TYPES",
    "_telegram_prefixed_value",
    "channel_value_for_storage",
    "_TELEGRAM_PREFIX_CHANNEL_TYPES",
    "_CHANNEL_TYPE_TO_PREDICATE",
]
