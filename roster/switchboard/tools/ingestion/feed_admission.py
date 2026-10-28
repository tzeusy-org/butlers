"""Closed service-ingest seam; no subscription engine, runner or live caller."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from butlers.core.goal_admission import EvidenceRefused, Grant, Obligation, digest


class FeedRecorder(Protocol):
    async def record(self, source_ref: str, payload: dict) -> str:
        """Idempotently record in the receiving domain's existing store.

        Only the trusted service implementation supplies this object. It runs
        with that domain's own authority, never Switchboard foreign-schema SQL.
        A returned locator proves deterministic recording, not reasoning.
        """
        ...


@dataclass(frozen=True)
class FeedChannel:
    connector_type: str
    endpoint: str
    owner_domain: str
    recorder: FeedRecorder


@dataclass(frozen=True)
class FeedSubscription:
    connector_type: str
    endpoint: str
    owner_domain: str
    event_type: str
    declaration_digest: str
    capabilities: tuple[Grant, ...] | None


def read_subscriptions(path: Path) -> tuple[FeedSubscription, ...]:
    """Read a Git-owned file selected by server construction, never envelope paths."""
    try:
        declaration = tomllib.loads(path.read_text())
        if (
            set(declaration) != {"version", "subscription"}
            or type(declaration["version"]) is not int
            or declaration["version"] != 1
        ):
            raise ValueError
        result = []
        for row in declaration["subscription"]:
            if set(row) - {
                "connector_type",
                "endpoint",
                "owner_domain",
                "event_type",
                "capabilities",
            }:
                raise ValueError
            if any(
                type(row.get(k)) is not str or not row[k]
                for k in ("connector_type", "endpoint", "owner_domain", "event_type")
            ):
                raise ValueError
            capabilities = row.get("capabilities")
            if capabilities is not None and (
                type(capabilities) is not list
                or any(type(c) is not str for c in capabilities)
                or len(set(capabilities)) != len(capabilities)
            ):
                raise ValueError
            result.append(
                FeedSubscription(
                    row["connector_type"],
                    row["endpoint"],
                    row["owner_domain"],
                    row["event_type"],
                    digest(row),
                    None if capabilities is None else tuple(Grant(c) for c in capabilities),
                )
            )
        keys = [(s.connector_type, s.endpoint, s.event_type) for s in result]
        if len(set(keys)) != len(keys):
            raise ValueError
        return tuple(result)
    except Exception:
        raise EvidenceRefused("feed_declaration_unavailable") from None


class DeterministicFeeds:
    def __init__(self, channels: tuple[FeedChannel, ...], declarations: Path):
        self.channels, self.declarations = channels, declarations
        keys = [(c.connector_type, c.endpoint) for c in channels]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate deterministic feed receiver")

    async def record(self, source, connector_type, endpoint, payload):
        channel = next(
            (
                c
                for c in self.channels
                if (c.connector_type, c.endpoint) == (connector_type, endpoint)
            ),
            None,
        )
        if channel is None:
            raise EvidenceRefused("feed_receiver_unavailable")
        subscriptions = read_subscriptions(self.declarations)
        raw = payload.get("payload", {}).get("raw")
        if not isinstance(raw, dict):
            raise EvidenceRefused("feed_record_unavailable")
        # Domain service owns its durable write/idempotence. No legacy event
        # receiver is invoked: that receiver schedules a reasoning session.
        receipt = await channel.recorder.record(source.id, payload)
        if not isinstance(receipt, str) or not receipt:
            raise EvidenceRefused("feed_record_unavailable")
        event_type = raw.get("event_type")
        subscription = next(
            (
                s
                for s in subscriptions
                if (s.connector_type, s.endpoint, s.owner_domain, s.event_type)
                == (connector_type, endpoint, channel.owner_domain, event_type)
            ),
            None,
        )
        if subscription is None:
            return None
        return Obligation(
            subscription.owner_domain,
            subscription.declaration_digest,
            subscription.capabilities,
            created_exposed=False,
        )
