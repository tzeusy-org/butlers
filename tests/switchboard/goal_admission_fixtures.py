"""Explicit test policies and verified-source stand-ins, never production defaults."""

from dataclasses import replace

from butlers.core.goal_admission import Capability, CatalogView, Evidence


def limits(*, new_goals=20, per_source=10, window_seconds=3600):
    return {
        "version": "fixture-v1",
        "origins": [
            {
                "origin": origin,
                "new_goals": new_goals,
                "per_source": per_source,
                "window_seconds": window_seconds,
                "budget": {
                    "max_invocations": 4,
                    "max_children": 2,
                    "max_calls": 10,
                    "max_tokens": 1000,
                    "max_spend": "1.25",
                    "deadline_seconds": 600,
                    "invocation_max_turns": 5,
                    "invocation_max_tokens": 500,
                },
            }
            for origin in ("owner", "obligation", "third_party", "staffer")
        ],
    }


class Sources:
    def __init__(self, facts=None):
        self.facts = facts or Evidence("message", "mail")

    async def load(self, connection, source):
        return replace(self.facts, kind=source.kind)


def catalog():
    return CatalogView(
        digest="catalog-v1",
        capabilities=(
            Capability("health.read", "health", "read"),
            Capability("health.record", "health", "record"),
            Capability("health.profile", "health", "record", owner_authority=True),
            Capability("messenger.notify", "messenger", "communicate", owner_default=True),
            Capability("messenger.send", "messenger", "communicate"),
            Capability("home.actuate", "home", "actuate"),
            Capability("health.schedule", "health", "administer"),
            Capability("finance.pay", "finance", "transact"),
            Capability("health.approve", "health", "administer", presentable=False),
        ),
    )


class Catalogs:
    """Explicit pinned protocol fixture, not proof of a real catalog publisher."""

    async def load(self, connection):
        return catalog()
