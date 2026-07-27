from butlers.api.routers import audit as audit_router


async def emit_audit(pool):
    await audit_router.append(pool, "switchboard", "fixture.audit")
