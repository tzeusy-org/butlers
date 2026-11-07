"""Dark RFC 0038 capability broker for the butlers-control session listener.

Importing this package starts nothing. ``server.session_app`` mounts the goal
endpoint only when the default-off ``BrokerSwitch`` is on; until the M6 switch
release nothing constructs one. See ``docs/architecture/capability-broker.md``.
"""

from .contracts import BrokerRefused, Reason

__all__ = ["BrokerRefused", "Reason"]
