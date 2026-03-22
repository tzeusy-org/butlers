## ADDED Requirements

### Requirement: Healing module disable flag

The daemon SHALL support a `healing.enabled` configuration option (default: `true`). When set to `false`, the daemon SHALL skip healing module initialization, worktree reaping, and healing tool registration without raising errors.

#### Scenario: Healing disabled via config
- **WHEN** the daemon starts with `healing.enabled = false` (from butler.toml or env var `BUTLERS_HEALING_ENABLED=false`)
- **THEN** the self-healing module is not loaded
- **AND** no worktree-related operations are attempted
- **AND** the butler starts normally with all other modules

#### Scenario: Healing disabled does not affect other modules
- **WHEN** `healing.enabled = false` and other modules depend on core infrastructure
- **THEN** all non-healing modules initialize normally

### Requirement: Read-only roster directory tolerance

The daemon SHALL tolerate a read-only config directory (e.g., ConfigMap mount at `/etc/butler`). All config reads from `butler.toml`, `CLAUDE.md`, `MANIFESTO.md`, and skill directories SHALL work normally. Write operations to `AGENTS.md` SHALL use the DB fallback (see `agents-md-db-fallback` spec).

#### Scenario: Daemon starts with read-only roster
- **WHEN** the config directory is mounted read-only
- **THEN** the daemon starts successfully
- **AND** config loading, system prompt reading, and skill discovery work normally

### Requirement: File logging disabled by default in k8s

The daemon SHALL respect `BUTLERS_DISABLE_FILE_LOGGING=1` to suppress all file-based log handlers, relying on stdout/stderr for log collection. This is the recommended setting for k8s deployments.

#### Scenario: File logging disabled via env var
- **WHEN** `BUTLERS_DISABLE_FILE_LOGGING=1` is set
- **THEN** no `FileHandler` instances are created
- **AND** all log output goes to stdout/stderr only
