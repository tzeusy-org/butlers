"""Explicit D13 boundary collaborator for pre-existing non-authentication owners.

Those owners retain their real SQL/codec/lineage/audit/memory/commit assertions,
including migrated-shape stand-ins that have no auth schema. This collaborator
does NOT prove a factor, nonce, privilege or admission. The separate migrated
dashboard-stepup owner exercises the real restricted issuer and signed factor.
Never apply this fixture to that authority owner.
"""

from butlers.modules.approvals.executor import _approval_write_transaction
from butlers.modules.approvals.stepup import _enlisted_write, _EnlistedWrite, _WriteResult


def admit_rule_boundary_for_non_auth_owner(monkeypatch):
    from butlers.api.routers import approvals as routes
    from butlers.modules.approvals import module, operations, stepup

    async def admitted(pool, kind, parameters, writer):
        parent = _enlisted_write.get()
        if parent is not None and parent.connection is pool:
            return await writer(pool)
        transaction = getattr(pool, "is_in_transaction", None)
        borrowed = callable(transaction) and transaction()
        async with _approval_write_transaction(pool) as connection:
            token = _enlisted_write.set(_EnlistedWrite(connection, kind))
            try:
                result = await writer(connection)
            finally:
                _enlisted_write.reset(token)
        return _WriteResult(result, deferred=borrowed)

    for consumer in (routes, stepup, module, operations):
        monkeypatch.setattr(consumer, "run_rule_write", admitted)
