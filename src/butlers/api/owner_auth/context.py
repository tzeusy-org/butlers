"""Request authority shared by HTTP enforcement and audit attribution.

Keep this module independent of HTTP handlers, services and database clients:
deterministic domain code also uses audit attribution.
"""

from contextvars import ContextVar
from dataclasses import dataclass

verified_http_principal: ContextVar[str | None] = ContextVar("owner_http_principal", default=None)
in_http_request: ContextVar[bool] = ContextVar("owner_http_request", default=False)


CUSTODY_COMMAND_PREFIX = "/api/endpoint-custody/commands"


@dataclass(frozen=True, repr=False)
class OwnerCustodyProof:
    """Private HTTP cookie/CSRF digests captured before body handling.

    This is an input to the database's current-auth check, not a cached
    authorization verdict. Auth epochs are always selected by that engine.
    """

    session_digest: str
    csrf_digest: str
    origin: str
    rp_id: str
    key_generation: str | None

    def __reduce__(self):
        raise TypeError("private owner proof cannot be serialized")

    def sql_values(self) -> dict:
        return {
            "session_digest": self.session_digest,
            "csrf_digest": self.csrf_digest,
            "origin": self.origin,
            "rp_id": self.rp_id,
            "key_generation": self.key_generation,
        }


owner_custody_proof: ContextVar[OwnerCustodyProof | None] = ContextVar(
    "owner_custody_proof", default=None
)
