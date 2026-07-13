"""Unit tests for butlers.modules.contacts.email_identity_matching (bu-qeaou).

Covers:
- fetch_email_sender_stats: aggregation, normalization of raw headers,
  distinct-thread/day counting, row-limit truncation flag.
- is_bulk_or_noreply_address: bulk/noreply heuristic.
- derive_display_name_from_address: local-part -> display name heuristic.
- fetch_active_has_email_addresses: bulk has-email lookup.
- match_existing_person_entity: conservative name match (unambiguous only).

The DB layer is mocked (asyncpg pool) so these are fast unit tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.modules.contacts.email_identity_matching import (
    choose_display_name,
    clean_stored_display_name,
    derive_display_name_from_address,
    fetch_active_has_email_addresses,
    fetch_email_sender_stats,
    is_bulk_or_noreply_address,
    match_existing_person_entity,
)

pytestmark = pytest.mark.unit


def _event_row(
    address: str,
    *,
    thread_id: str | None,
    received_at: datetime,
    display_name: str | None = None,
) -> dict:
    return {
        "source_sender_identity": address,
        "source_sender_display_name": display_name,
        "source_thread_identity": thread_id,
        "received_at": received_at,
    }


class TestFetchEmailSenderStats:
    @pytest.mark.asyncio
    async def test_groups_by_normalized_address(self) -> None:
        """Same person under raw-header and bare-address forms must merge into one bucket."""
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                _event_row("John Doe <john@example.com>", thread_id="t1", received_at=now),
                _event_row("john@example.com", thread_id="t2", received_at=now - timedelta(days=1)),
                _event_row("JOHN@EXAMPLE.COM", thread_id="t2", received_at=now - timedelta(days=2)),
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert len(result.stats) == 1
        stat = result.stats[0]
        assert stat.address == "john@example.com"
        assert stat.event_count == 3
        assert stat.distinct_threads == 2  # t1, t2 (t2 seen twice, counted once)
        assert stat.distinct_days == 3
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_null_thread_id_does_not_count_as_a_thread(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[_event_row("a@example.com", thread_id=None, received_at=now)]
        )

        result = await fetch_email_sender_stats(pool)

        assert result.stats[0].distinct_threads == 0
        assert result.stats[0].event_count == 1

    @pytest.mark.asyncio
    async def test_row_limit_truncation_flag(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        # row_limit=1 but 2 rows returned (row_limit+1 requested) -> truncated
        pool.fetch = AsyncMock(
            return_value=[
                _event_row("a@example.com", thread_id="t1", received_at=now),
                _event_row("b@example.com", thread_id="t1", received_at=now),
            ]
        )

        result = await fetch_email_sender_stats(pool, row_limit=1)

        assert result.truncated is True
        assert len(result.stats) == 1

    @pytest.mark.asyncio
    async def test_no_rows_returns_empty_untruncated(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        result = await fetch_email_sender_stats(pool)

        assert result.stats == []
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_surfaces_most_recent_stored_display_name(self) -> None:
        """The newest (rows arrive DESC) usable stored display name wins per address."""
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                # newest row first (received_at DESC) — its name is preferred
                _event_row(
                    "john@example.com", thread_id="t1", received_at=now, display_name="John Doe"
                ),
                _event_row(
                    "john@example.com",
                    thread_id="t2",
                    received_at=now - timedelta(days=1),
                    display_name="J. Doe (old)",
                ),
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert result.stats[0].display_name == "John Doe"

    @pytest.mark.asyncio
    async def test_junk_stored_name_is_ignored_and_falls_to_legacy_then_none(self) -> None:
        """A stored name equal to the address is junk; with no legacy name, display_name is None."""
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                _event_row(
                    "a@example.com",
                    thread_id="t1",
                    received_at=now,
                    display_name="a@example.com",
                ),
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert result.stats[0].display_name is None

    @pytest.mark.asyncio
    async def test_legacy_raw_sender_identity_recovers_display_name(self) -> None:
        """Pre-column rows (display_name NULL) recover the name from the raw From header."""
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                # Legacy row: normalizer reduces identity to the bare address for
                # grouping, but the raw header still carries the display name.
                _event_row(
                    "Jane Roe <jane@example.com>",
                    thread_id="t1",
                    received_at=now,
                    display_name=None,
                ),
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert result.stats[0].address == "jane@example.com"
        assert result.stats[0].display_name == "Jane Roe"

    @pytest.mark.asyncio
    async def test_missing_display_name_column_key_is_tolerated(self) -> None:
        """Rows lacking the column key entirely (old mocks) yield display_name None, not error."""
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "source_sender_identity": "x@example.com",
                    "source_thread_identity": "t1",
                    "received_at": now,
                }
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert result.stats[0].display_name is None

    @pytest.mark.asyncio
    async def test_sorted_by_distinct_threads_descending(self) -> None:
        now = datetime(2026, 1, 10, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                _event_row("low@example.com", thread_id="t1", received_at=now),
                _event_row("high@example.com", thread_id="t1", received_at=now),
                _event_row("high@example.com", thread_id="t2", received_at=now),
                _event_row("high@example.com", thread_id="t3", received_at=now),
            ]
        )

        result = await fetch_email_sender_stats(pool)

        assert [s.address for s in result.stats] == ["high@example.com", "low@example.com"]


class TestIsBulkOrNoreplyAddress:
    @pytest.mark.parametrize(
        "address",
        [
            "noreply@example.com",
            "no-reply@example.com",
            "no.reply@example.com",
            "donotreply@example.com",
            "notifications@example.com",
            "newsletter@example.com",
            "mailer-daemon@example.com",
            "postmaster@example.com",
            "support@example.com",
            "billing@example.com",
            # bu: bare "notice"/"notify" were missed by the old denylist and
            # produced spurious "Notice" Person proposals in the approvals queue.
            "notice@example.com",
            "notices@example.com",
            "notify@example.com",
            "receipts@example.com",
            "invoice@example.com",
            "welcome@example.com",
            "verify@example.com",
            "verification@example.com",
            "confirmation@example.com",
        ],
    )
    def test_flags_known_bulk_patterns(self, address: str) -> None:
        assert is_bulk_or_noreply_address(address) is True

    @pytest.mark.parametrize(
        "address",
        [
            # Human-looking local-parts sent from dedicated sending subdomains —
            # the reported misfire (notice@email.anthropic.com) and its family.
            "notice@email.anthropic.com",
            "hey@mail.github.com",
            "someone@e.marketing.com",
            "person@mailer.example.com",
            "digest@news.example.com",
            # Known ESP registrable domains.
            "bob@bounce.sendgrid.net",
            "alice@u123.mail.amazonses.com",
            "carol@mg.mailgun.org",
        ],
    )
    def test_flags_bulk_sending_domains(self, address: str) -> None:
        assert is_bulk_or_noreply_address(address) is True

    @pytest.mark.parametrize(
        "address",
        [
            "john.doe@example.com",
            "jane_smith@example.com",
            "alex@example.com",
            # Apex/registrable domains for the same brands must NOT be flagged —
            # a real human at @anthropic.com is a valid candidate.
            "jane@anthropic.com",
            "dev@github.com",
        ],
    )
    def test_does_not_flag_plausible_human_addresses(self, address: str) -> None:
        assert is_bulk_or_noreply_address(address) is False


class TestDeriveDisplayNameFromAddress:
    def test_dotted_local_part(self) -> None:
        assert derive_display_name_from_address("john.doe@example.com") == "John Doe"

    def test_underscored_local_part(self) -> None:
        assert derive_display_name_from_address("jane_smith@example.com") == "Jane Smith"

    def test_digits_are_dropped(self) -> None:
        assert derive_display_name_from_address("john.doe123@example.com") == "John Doe"

    def test_all_digits_falls_back_to_local_part(self) -> None:
        assert derive_display_name_from_address("12345@example.com") == "12345"


class TestCleanStoredDisplayName:
    def test_usable_name_passes_through_stripped(self) -> None:
        assert clean_stored_display_name("  John Doe  ", "john@example.com") == "John Doe"

    def test_none_and_empty_return_none(self) -> None:
        assert clean_stored_display_name(None, "a@example.com") is None
        assert clean_stored_display_name("", "a@example.com") is None
        assert clean_stored_display_name("   ", "a@example.com") is None

    def test_name_equal_to_address_is_rejected(self) -> None:
        assert clean_stored_display_name("A@Example.com", "a@example.com") is None

    def test_display_name_containing_at_is_rejected(self) -> None:
        # A display name with '@' is an email address, not a human name — reject
        # it even when it differs from the sender's own address.
        assert clean_stored_display_name("A@Example.com", "a@example.com") is None
        assert clean_stored_display_name("b@example.com", "a@example.com") is None

    def test_all_punctuation_is_rejected(self) -> None:
        assert clean_stored_display_name("<<>>", "a@example.com") is None
        assert clean_stored_display_name("--", "a@example.com") is None

    def test_name_with_some_alphanumerics_survives(self) -> None:
        assert clean_stored_display_name("J.D.", "a@example.com") == "J.D."


class TestChooseDisplayName:
    def test_prefers_stored_name(self) -> None:
        assert choose_display_name("Real Name", "john.doe@example.com") == "Real Name"

    def test_falls_back_to_local_part_when_absent(self) -> None:
        assert choose_display_name(None, "john.doe@example.com") == "John Doe"

    def test_empty_string_falls_back(self) -> None:
        assert choose_display_name("", "john.doe@example.com") == "John Doe"


class TestFetchActiveHasEmailAddresses:
    @pytest.mark.asyncio
    async def test_empty_input_short_circuits(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=AssertionError("should not query"))

        result = await fetch_active_has_email_addresses(pool, [])

        assert result == set()
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_matched_subset(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"object": "a@example.com"}])

        result = await fetch_active_has_email_addresses(pool, ["a@example.com", "b@example.com"])

        assert result == {"a@example.com"}


class TestMatchExistingPersonEntity:
    @pytest.mark.asyncio
    async def test_exactly_one_match_returns_id(self) -> None:
        eid = uuid.uuid4()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"id": eid}])

        result = await match_existing_person_entity(pool, "John Doe")

        assert result == eid

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        result = await match_existing_person_entity(pool, "Nobody")

        assert result is None

    @pytest.mark.asyncio
    async def test_ambiguous_match_returns_none(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"id": uuid.uuid4()}, {"id": uuid.uuid4()}])

        result = await match_existing_person_entity(pool, "John Smith")

        assert result is None

    @pytest.mark.asyncio
    async def test_blank_display_name_returns_none_without_querying(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=AssertionError("should not query"))

        result = await match_existing_person_entity(pool, "   ")

        assert result is None
        pool.fetch.assert_not_called()
