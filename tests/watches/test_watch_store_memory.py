"""The in-memory watch store against the shared contract."""

from __future__ import annotations

from butlers.watches.memory import InMemoryWatchStore
from tests.watches.store_contract import WatchStoreContract


class TestInMemoryWatchStoreContract(WatchStoreContract):
    def make_store(self):
        return InMemoryWatchStore()
