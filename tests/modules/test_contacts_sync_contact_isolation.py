"""One failing contact must not wedge an account's sync run (bu-z0gofs)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from butlers.modules.contacts.sync import (
    CONTACT_APPLY_FAILED_ERROR,
    CanonicalContact,
    ContactBatch,
    ContactsSyncEngine,
    ContactsSyncRuntime,
    ContactsSyncState,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
_SECRET = "Jordan Example <jordan@example.invalid>"


class _Store:
    def __init__(self, state: ContactsSyncState | None = None) -> None:
        self.state = state or ContactsSyncState()
        self.saves = 0

    async def load(self, **kwargs):
        return self.state.model_copy(deep=True)

    async def save(self, *, state, **kwargs):
        self.saves += 1
        self.state = state.model_copy(deep=True)


class _Provider:
    name = "google"

    def __init__(self, contacts, *, fail_fetch: Exception | None = None) -> None:
        self._contacts = contacts
        self._fail_fetch = fail_fetch

    async def full_sync(self, *, account_id, page_token=None):
        if self._fail_fetch is not None:
            raise self._fail_fetch
        return ContactBatch(contacts=self._contacts, next_sync_cursor="cursor-2")

    async def incremental_sync(self, *, account_id, cursor, page_token=None):
        return ContactBatch(contacts=self._contacts, next_sync_cursor="cursor-3")

    async def validate_credentials(self) -> None:
        return None


def _contact(external_id: str) -> CanonicalContact:
    return CanonicalContact(external_id=external_id, raw={"id": external_id})


def _engine(store, contacts, failing: set[str], *, fail_fetch=None):
    written: list[str] = []

    async def apply(contact):
        if contact.external_id in failing:
            raise RuntimeError(f"cannot write {_SECRET} ({contact.external_id})")
        written.append(contact.external_id)

    engine = ContactsSyncEngine(
        provider=_Provider(contacts, fail_fetch=fail_fetch), state_store=store, apply_contact=apply
    )
    return engine, written


async def test_a_failing_contact_is_isolated_and_the_cursor_advances(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _Store()
    contacts = [_contact("people/1"), _contact("people/2"), _contact("people/3")]
    engine, written = _engine(store, contacts, {"people/2"})

    with caplog.at_level(logging.WARNING, logger="butlers.modules.contacts.sync"):
        result = await engine.sync(account_id="acct", mode="full")

    assert written == ["people/1", "people/3"]
    assert (result.applied_contacts, result.failed_contacts) == (2, 1)
    assert store.state.sync_cursor == "cursor-2"
    # The failed contact has no recorded version, so a later full sync retries it.
    assert set(store.state.contact_versions) == {"people/1", "people/3"}
    assert store.state.last_error == CONTACT_APPLY_FAILED_ERROR
    assert "people/2" not in store.state.last_error and _SECRET not in store.state.last_error


async def test_the_failure_log_is_content_free() -> None:
    store = _Store()
    engine, _ = _engine(store, [_contact("people/1"), _contact("people/2")], {"people/2"})
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("butlers.modules.contacts.sync")
    handler = _Capture(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        await engine.sync(account_id="acct", mode="full")
    finally:
        logger.removeHandler(handler)

    (record,) = [r for r in records if r.getMessage() == "contact_sync_apply_failed"]
    assert (record.provider, record.account_id, record.external_id) == (
        "google",
        "acct",
        "people/2",
    )
    assert record.error_class == "RuntimeError"
    assert _SECRET not in repr(record.__dict__) and record.exc_info is None


async def test_a_run_level_failure_stores_a_content_free_last_error() -> None:
    store = _Store()
    boom = ConnectionError(f"upstream said {_SECRET} people/9")
    engine, _ = _engine(store, [], set(), fail_fetch=boom)

    with pytest.raises(ConnectionError):
        await engine.sync(account_id="acct", mode="full")

    assert store.state.last_error is not None
    assert _SECRET not in store.state.last_error and "people/9" not in store.state.last_error
    assert "ConnectionError" in store.state.last_error


async def test_when_every_contact_fails_the_run_fails_and_the_cursor_stays() -> None:
    store = _Store(ContactsSyncState(sync_cursor="cursor-1"))
    engine, written = _engine(
        store, [_contact("people/1"), _contact("people/2")], {"people/1", "people/2"}
    )

    with pytest.raises(RuntimeError):
        await engine.sync(account_id="acct", mode="full")

    assert written == [] and store.state.sync_cursor == "cursor-1"


async def test_an_incremental_run_keeps_the_retry_marker_and_a_clean_full_run_clears_it() -> None:
    store = _Store(ContactsSyncState(sync_cursor="cursor-1", last_error=CONTACT_APPLY_FAILED_ERROR))
    engine, _ = _engine(store, [_contact("people/1")], set())

    await engine.sync(account_id="acct", mode="incremental")
    assert store.state.last_error == CONTACT_APPLY_FAILED_ERROR

    await engine.sync(account_id="acct", mode="full")
    assert store.state.last_error is None


@pytest.mark.parametrize(
    ("last_full_age", "expected"),
    [(timedelta(minutes=10), "incremental"), (timedelta(hours=2), "full")],
)
async def test_the_runtime_retries_failed_contacts_with_a_bounded_full_sync(
    last_full_age: timedelta, expected: str
) -> None:
    last_full = (_NOW - last_full_age).isoformat()
    store = _Store(
        ContactsSyncState(
            sync_cursor="cursor-1",
            cursor_issued_at=_NOW.isoformat(),
            last_full_sync_at=last_full,
            last_error=CONTACT_APPLY_FAILED_ERROR,
        )
    )
    runtime = ContactsSyncRuntime(
        sync_engine=object(),  # type: ignore[arg-type]
        state_store=store,
        provider_name="google",
        account_id="acct",
        now_fn=lambda: _NOW,
    )

    assert await runtime._next_mode() == expected
