from butlers.api.routers import audit


async def emit_audit(pool):
    await audit.append(pool, "switchboard", "fixture.audit")
