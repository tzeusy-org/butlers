# Butler Credentials

Per-butler credential and environment variable management for the Butlers AI agent framework. Each butler declares the environment variables it requires in `butler.toml`, modules reference credentials via `credentials_env` in their config sections, and the LLM CLI Spawner passes through only explicitly declared variables to ephemeral LLM CLI instances. Actual secret values MUST never appear in config files -- only references to environment variable names.

---

## ADDED Requirements

### Requirement: Per-butler environment variable declaration

Each butler's `butler.toml` SHALL support a `[butler.env]` section that declares the environment variables the butler requires. The section MUST support two fields:

- `required` -- a list of environment variable names that MUST be present at startup. If any are missing, the butler MUST refuse to start.
- `optional` -- a list of environment variable names that SHOULD be present but do not block startup. Missing optional vars SHALL be logged as warnings.

Both fields accept arrays of strings representing environment variable names. If the `[butler.env]` section is omitted entirely, the butler SHALL treat it as having empty `required` and `optional` lists.

```toml
[butler.env]
required = ["ANTHROPIC_API_KEY", "SOME_SECRET"]
optional = ["SENTRY_DSN"]
```

#### Scenario: All required env vars are present

WHEN a butler's `butler.toml` declares `[butler.env].required = ["ANTHROPIC_API_KEY", "SOME_SECRET"]`
AND both `ANTHROPIC_API_KEY` and `SOME_SECRET` are set in the environment
THEN the butler SHALL start successfully
AND no env-related errors SHALL be raised.

#### Scenario: A required env var is missing

WHEN a butler's `butler.toml` declares `[butler.env].required = ["ANTHROPIC_API_KEY", "MISSING_VAR"]`
AND `MISSING_VAR` is not set in the environment
THEN the butler MUST refuse to start
AND the error message MUST name `MISSING_VAR` as the missing variable
AND the error message MUST indicate that it is required by the butler's `[butler.env]` section.

#### Scenario: Multiple required env vars are missing

WHEN a butler's `butler.toml` declares `[butler.env].required = ["VAR_A", "VAR_B", "VAR_C"]`
AND `VAR_A` and `VAR_C` are not set in the environment
THEN the butler MUST refuse to start
AND the error message MUST name both `VAR_A` and `VAR_C` as missing variables.

#### Scenario: An optional env var is missing

WHEN a butler's `butler.toml` declares `[butler.env].optional = ["SENTRY_DSN"]`
AND `SENTRY_DSN` is not set in the environment
THEN the butler SHALL start successfully
AND a warning SHALL be logged indicating that optional variable `SENTRY_DSN` is not set.

#### Scenario: All optional env vars are present

WHEN a butler's `butler.toml` declares `[butler.env].optional = ["SENTRY_DSN"]`
AND `SENTRY_DSN` is set in the environment
THEN the butler SHALL start successfully
AND no warning SHALL be logged for `SENTRY_DSN`.

#### Scenario: No butler.env section in butler.toml

WHEN a butler's `butler.toml` does not contain a `[butler.env]` section
THEN the butler SHALL treat `required` and `optional` as empty lists
AND no env-var-related validation errors SHALL be raised from this section
AND startup SHALL proceed to other validation checks (e.g., `ANTHROPIC_API_KEY` check).

---

### Requirement: Module credential references via credentials_env

Modules SHALL declare their credential requirements via a `credentials_env` field in their `[modules.<name>]` configuration section of `butler.toml`. The `credentials_env` field contains the name of an environment variable that holds the module's credentials.

```toml
[modules.email]
provider = "gmail"
credentials_env = "GMAIL_SWITCHBOARD_CREDS"
```

The butler MUST validate at startup that every environment variable referenced by `credentials_env` in any enabled module's config actually exists in the environment. If a module's `credentials_env` references a missing environment variable, startup MUST fail with a clear error naming the module and the missing variable.

#### Scenario: Module credentials_env references an existing env var

WHEN the `[modules.email]` section declares `credentials_env = "GMAIL_SWITCHBOARD_CREDS"`
AND `GMAIL_SWITCHBOARD_CREDS` is set in the environment
THEN the butler SHALL start successfully
AND the email module SHALL be loaded without credential errors.

#### Scenario: Module credentials_env references a missing env var

WHEN the `[modules.email]` section declares `credentials_env = "GMAIL_SWITCHBOARD_CREDS"`
AND `GMAIL_SWITCHBOARD_CREDS` is not set in the environment
THEN the butler MUST refuse to start
AND the error message MUST state that module `email` requires environment variable `GMAIL_SWITCHBOARD_CREDS`
AND the error message MUST clearly identify the module name and the missing variable name.

#### Scenario: Multiple modules with missing credentials

WHEN the `[modules.email]` section declares `credentials_env = "GMAIL_CREDS"`
AND the `[modules.telegram]` section declares `credentials_env = "BUTLER_TELEGRAM_TOKEN"`
AND both `GMAIL_CREDS` and `BUTLER_TELEGRAM_TOKEN` are missing from the environment
THEN the butler MUST refuse to start
AND the error message MUST name both modules and their respective missing variables.

#### Scenario: Module config without credentials_env

WHEN the `[modules.calendar]` section does not contain a `credentials_env` field
THEN no credential validation SHALL be performed for the calendar module
AND the butler SHALL proceed with startup for that module without credential errors.

---

### Requirement: Credential scoping in dev vs production mode

In production mode (one container per butler), each container has its own environment. Standard Docker environment variable injection provides natural credential isolation with no special scoping needed.

In dev mode (single process via `butlers up`), all butlers share the same OS environment. Credentials are naturally shared across all butlers in the process. In v1, credential access in dev mode is trust-based -- butlers only access the env vars they explicitly declare or that their modules reference, but this is not enforced at the runtime level. The `[butler.env]` declarations in `butler.toml` document which variables each butler needs.

The `butlers up` command SHALL log which environment variables each butler requires and whether those variables are present.

#### Scenario: Dev mode logs required env vars per butler

WHEN `butlers up` starts three butlers and each butler has different `[butler.env].required` declarations
THEN the CLI SHALL log, for each butler, the list of required environment variables and whether each is present or missing
AND the log output SHALL clearly identify which butler each env var listing belongs to.

#### Scenario: Dev mode logs module credentials_env per butler

WHEN `butlers up` starts a butler with `[modules.email]` declaring `credentials_env = "GMAIL_CREDS"`
THEN the CLI SHALL include `GMAIL_CREDS` in the logged env var summary for that butler
AND the log SHALL indicate that it is required by the `email` module.

#### Scenario: Production mode uses standard Docker env vars

WHEN a butler runs in production mode via `butlers run` inside a Docker container
AND the container's environment includes `ANTHROPIC_API_KEY` and `GMAIL_CREDS`
THEN the butler SHALL access those variables from the container's environment
AND no special scoping mechanism SHALL be applied beyond standard container isolation.

#### Scenario: Dev mode does not enforce cross-butler isolation in v1

WHEN `butlers up` starts two butlers in the same process
AND butler A declares `[butler.env].required = ["SECRET_A"]`
AND butler B declares `[butler.env].required = ["SECRET_B"]`
AND both `SECRET_A` and `SECRET_B` are set in the OS environment
THEN both butlers SHALL start successfully
AND no runtime enforcement SHALL prevent butler A from reading `SECRET_B` via `os.environ` in v1.

---

### Requirement: runtime instance credential passthrough

When the LLM CLI Spawner spawns an ephemeral LLM CLI instance, it MUST pass through a specific, restricted set of environment variables to the CC process. runtime instances MUST NOT receive the full host environment -- only explicitly declared variables SHALL be passed through. This is the credential lock-down mechanism for runtime instances.

The LLM CLI Spawner SHALL pass through the following env vars to each spawned CC process:

1. `ANTHROPIC_API_KEY` -- always required for CC to function.
2. All env vars listed in the butler's `[butler.env].required` list.
3. All env vars listed in the butler's `[butler.env].optional` list that are present in the environment.
4. All env vars referenced by `credentials_env` in any enabled module's config.

The LLM CLI Spawner SHALL construct an explicit environment dict containing only these variables and pass it to the CC process. No other host environment variables SHALL be forwarded to the runtime instance.

#### Scenario: runtime instance receives only declared env vars

WHEN a butler declares `[butler.env].required = ["ANTHROPIC_API_KEY", "CUSTOM_TOKEN"]`
AND the host environment contains `ANTHROPIC_API_KEY`, `CUSTOM_TOKEN`, `HOME`, `PATH`, `AWS_SECRET_KEY`, and `DATABASE_URL`
THEN the spawned runtime instance SHALL receive `ANTHROPIC_API_KEY` and `CUSTOM_TOKEN`
AND the runtime instance MUST NOT receive `HOME`, `PATH`, `AWS_SECRET_KEY`, or `DATABASE_URL`.

#### Scenario: runtime instance receives module credentials

WHEN a butler has `[modules.email]` with `credentials_env = "GMAIL_CREDS"`
AND `GMAIL_CREDS` is set in the host environment
THEN the spawned runtime instance SHALL receive `GMAIL_CREDS` in its environment.

#### Scenario: runtime instance receives ANTHROPIC_API_KEY

WHEN the LLM CLI Spawner spawns a runtime instance for any butler
THEN the runtime instance's environment MUST contain `ANTHROPIC_API_KEY`
AND the value MUST match the host environment's `ANTHROPIC_API_KEY`.

#### Scenario: runtime instance does not receive optional env vars that are missing

WHEN a butler declares `[butler.env].optional = ["SENTRY_DSN"]`
AND `SENTRY_DSN` is not set in the host environment
THEN the spawned runtime instance's environment SHALL NOT contain `SENTRY_DSN`.

#### Scenario: runtime instance receives optional env vars that are present

WHEN a butler declares `[butler.env].optional = ["SENTRY_DSN"]`
AND `SENTRY_DSN` is set in the host environment
THEN the spawned runtime instance SHALL receive `SENTRY_DSN` in its environment.

#### Scenario: Full environment is not leaked to CC

WHEN the host environment contains 50 environment variables
AND the butler declares 3 required vars, 1 optional var, and 1 module credentials_env var
THEN the runtime instance SHALL receive at most 5 environment variables (plus `TRACEPARENT` if an active trace exists)
AND the remaining 45+ host variables MUST NOT be present in the runtime instance's environment.

---

### Requirement: Startup validation sequence

On butler startup, the daemon MUST perform credential and environment variable validation as part of its startup sequence. This validation MUST occur after config loading and before database provisioning. The validation MUST follow this order:

1. Check that `ANTHROPIC_API_KEY` exists in the environment -- fail if missing. This variable is always required because every butler uses the LLM CLI Spawner, which needs it.
2. Check all `[butler.env].required` vars exist -- fail if any are missing.
3. Check all `[butler.env].optional` vars -- log a warning for each that is missing.
4. Check all enabled module `credentials_env` vars exist -- fail if any are missing.

If any check that triggers a failure finds missing variables, the daemon MUST collect all missing variables across all checks before failing, so that the error message reports all missing variables at once rather than failing on the first one.

#### Scenario: All validation passes

WHEN `ANTHROPIC_API_KEY` is set
AND all `[butler.env].required` vars are set
AND all `[butler.env].optional` vars are set
AND all module `credentials_env` vars are set
THEN the butler SHALL pass credential validation and proceed to database provisioning.

#### Scenario: ANTHROPIC_API_KEY is missing

WHEN `ANTHROPIC_API_KEY` is not set in the environment
THEN the butler MUST refuse to start
AND the error message MUST state that `ANTHROPIC_API_KEY` is required for the LLM CLI Spawner.

#### Scenario: Multiple missing vars reported at once

WHEN `ANTHROPIC_API_KEY` is missing
AND `[butler.env].required` includes `CUSTOM_SECRET` which is also missing
AND `[modules.email]` declares `credentials_env = "GMAIL_CREDS"` which is also missing
THEN the butler MUST refuse to start
AND the error message MUST list all three missing variables: `ANTHROPIC_API_KEY`, `CUSTOM_SECRET`, and `GMAIL_CREDS`
AND the error message MUST indicate which component requires each variable (LLM CLI Spawner, butler env, email module).

#### Scenario: Error messages name specific components

WHEN `[modules.telegram]` declares `credentials_env = "BUTLER_TELEGRAM_TOKEN"` and it is missing
THEN the error message MUST state that module `telegram` requires `BUTLER_TELEGRAM_TOKEN`
AND the error message MUST NOT be a generic "missing environment variable" without identifying the source.

#### Scenario: Optional vars produce warnings, not errors

WHEN all required vars and all module `credentials_env` vars are present
AND `[butler.env].optional = ["SENTRY_DSN", "DEBUG_KEY"]` and both are missing
THEN the butler SHALL start successfully
AND warnings SHALL be logged for both `SENTRY_DSN` and `DEBUG_KEY`
AND the warnings SHALL indicate they are optional variables.

#### Scenario: Validation occurs before database provisioning

WHEN `ANTHROPIC_API_KEY` is missing
THEN the butler MUST fail before attempting any database connection or provisioning
AND no database operations SHALL be executed.

---

### Requirement: No secrets in config files

The `butler.toml` file MUST NOT contain actual secret values -- only references to environment variable names. The credential management model is that `butler.toml` declares *which* env vars are needed, and the actual values are injected via the environment at runtime.

At startup, the butler SHALL scan all string values in `butler.toml` and log a warning if any value appears to be an inline secret rather than an env var reference. A value SHALL be flagged if it is a plain string (not an env var name pattern used in `credentials_env` or `[butler.env]`) and contains any of the following substrings (case-insensitive): `password`, `token`, `key`, `secret`.

This check is advisory only -- it SHALL produce warnings but MUST NOT block startup.

#### Scenario: Config with env var reference is clean

WHEN `butler.toml` contains `credentials_env = "GMAIL_SWITCHBOARD_CREDS"` under `[modules.email]`
THEN no secret-detection warning SHALL be logged for the `credentials_env` field
AND the value SHALL be treated as an environment variable name reference.

#### Scenario: Config with suspected inline secret triggers warning

WHEN `butler.toml` contains a field `api_token = "sk-abc123xyz"` under `[modules.email]`
THEN a warning SHALL be logged indicating that the field `api_token` in `[modules.email]` appears to contain an inline secret
AND the warning SHALL advise using environment variables instead.

#### Scenario: Secret detection is case-insensitive

WHEN `butler.toml` contains a field `Password = "hunter2"` under `[modules.email]`
THEN a warning SHALL be logged indicating a suspected inline secret.

#### Scenario: Env var name lists are exempt from secret detection

WHEN `butler.toml` contains `[butler.env].required = ["ANTHROPIC_API_KEY", "SOME_SECRET_TOKEN"]`
THEN no secret-detection warning SHALL be logged for these values
AND they SHALL be recognized as environment variable name declarations, not inline secrets.

#### Scenario: Secret detection does not block startup

WHEN `butler.toml` contains a suspected inline secret
THEN the butler SHALL still start successfully
AND the warning SHALL be logged for operator awareness only.

#### Scenario: Field names containing secret-related substrings in credentials_env are exempt

WHEN `butler.toml` contains `credentials_env = "MY_SECRET_KEY"` under `[modules.telegram]`
THEN no secret-detection warning SHALL be logged
AND the value SHALL be treated as an environment variable name to look up.
