# Module Implementation Pattern

Modules are the tool-surface extension mechanism. They implement the `Module` ABC from `src/butlers/modules/base.py`.

## Module ABC Contract

```python
class Module(ABC):
    @property
    def name(self) -> str: ...              # e.g., "steam", "email"
    @property
    def config_schema(self) -> type: ...    # Pydantic model class
    @property
    def dependencies(self) -> list[str]: ... # other module names (usually [])

    async def register_tools(self, mcp, config, db): ...  # register MCP tools
    def migration_revisions(self) -> str | None: ...       # Alembic branch label
    async def on_startup(self, config, db, credential_store): ...
    async def on_shutdown(self): ...
    def tool_metadata(self) -> dict | None: ...  # sensitivity metadata
```

## Config Schema Pattern

```python
class SteamConfig(BaseModel):
    default_account: str | None = None  # UUID or external ID override
    cache_ttl_seconds: int = 300
    # service-specific settings...

class SteamModule(Module):
    @property
    def config_schema(self):
        return SteamConfig
```

Configured via `butler.toml`:
```toml
[modules.steam]
cache_ttl_seconds = 300
```

## Credential Resolution in on_startup

```python
async def on_startup(self, config, db, credential_store):
    # Resolve from account registry (primary or configured account)
    account = await resolve_primary_account(db, "steam")
    if not account:
        logger.warning("No Steam account connected — degraded mode")
        self._degraded = True
        return
    # Resolve API key from companion entity
    self._api_key = await resolve_entity_info(db, account.entity_id, "steam_api_key")
    self._steam_id = account.steam_id
```

## Degraded Mode

When no account is configured, all tools return actionable errors:

```python
if self._degraded:
    return {"error": "no_steam_account",
            "message": "No Steam account connected.",
            "hint": "Connect one via the dashboard at /settings/integrations."}
```

## Default-to-Owner Pattern

When an ID parameter is omitted, default to the owner's primary account:

```python
@mcp.tool()
async def steam_get_owned_games(steam_id: str | None = None, include_free: bool = False):
    """Get a player's owned games. Defaults to your account if steam_id is omitted."""
    target_id = steam_id or self._steam_id
    # ... call API with target_id
```

## Privacy-Aware Error Responses

External APIs may return empty data due to privacy settings. Return structured errors:

```python
{"error": "profile_private",
 "message": "This player's game library is not publicly visible.",
 "hint": "The player must set 'Game details' to public in Steam privacy settings."}
```

## Tool Metadata (Sensitivity)

Mark credential parameters as sensitive to exclude them from session logs:

```python
def tool_metadata(self):
    return {"_internal_api_key": ToolMeta(sensitive_args=["api_key"])}
```

## Module Registration

Add to `src/butlers/modules/` and ensure the module registry discovers it. The registry auto-discovers `Module` subclasses from `butlers.modules.*` packages.

## Key Rules

- Modules ONLY add tools — never modify core infrastructure
- Module dependencies are resolved via topological sort
- Config is validated via Pydantic before `register_tools()` is called
- Blocking I/O (HTTP calls) must use `asyncio.to_thread` or async HTTP clients
