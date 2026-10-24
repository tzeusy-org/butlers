"""Explicit broker-only session listener for the isolated control process.

No profile, daemon or CLI starts this listener implicitly. In particular the
KEY_ABSENT_STAGE listener remains refusal-only. A production control owner must
supply its real Broker, actual catalog/policy/domain adapters and signer; this
class neither resolves keys nor creates authenticated runner invocations.
"""

from __future__ import annotations

from butlers.control import CONTROL_SESSION_PORT, SessionRefusalListener

from .server import create_app
from .service import Broker


class BrokerSessionListener(SessionRefusalListener):
    """Reuse the existing owned socket/startup/shutdown bounds with MCP lifespan.

    Every route is the Broker's authenticated goal endpoint. Service, health,
    token issuance and approval/owner routes are never mounted here.
    """

    def __init__(self, broker: Broker, *, host: str, port: int = CONTROL_SESSION_PORT):
        broker.require_available()
        super().__init__(host=host, port=port)
        self._app = create_app(broker)

    def _application(self):
        return self._app

    def _application_lifespan(self):
        # Starlette's supported context owns the actual FastMCP session manager.
        # The listener enters/exits it on ONE retained lifetime task.
        app = self._app.app
        return app.router.lifespan_context(app)
