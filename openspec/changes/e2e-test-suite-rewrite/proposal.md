## Why

The existing E2E test suite validates butler ecosystem behavior but lacks model-level benchmarking — we can't answer "which model routes best?" or "which model calls the right tools downstream?" There's also no structured OAuth staging flow, and no UX-bound tests that exercise real ingress channels (email, Telegram) through to downstream effects. This rewrite keeps the scenario-driven spirit but adds three dimensions: (1) UX-bound channel tests with simulated ingress and verified effects, (2) model benchmarking with scored comparisons, and (3) proper ecosystem staging with OAuth. This suite is explicitly designed for real token burn — it does NOT run in CI/CD.

## What Changes

- **Rewrite ecosystem staging**: Replace ad-hoc `butler_ecosystem` fixture with a staged bootstrap that spins up all roster butlers, prompts for OAuth logins (Google, etc.) where needed, and validates connectivity before tests run.
- **Add model benchmarking harness**: For each model `y` in a configurable set, run all switchboard routing prompts and all downstream tool-call prompts with that model pinned for _both_ switchboard and all butlers simultaneously (avoiding y×z fanout).
- **Switchboard routing scorecard**: For a set of prompts `x`, assert which butler gets routed to. Score each model on classification accuracy. Generate per-model scorecard artifacts.
- **Downstream tool-call scorecard**: For forwarded prompts, assert correct tool calls are made (e.g., calendar event → `calendar_create`, meal log → `log_meal`, interactive → `notify`). Score each model on tool-call accuracy.
- **Scorecard generation**: Produce JSON + human-readable markdown scorecards after each run, broken down by model, scenario category, and butler.
- **Ingress injection tests**: Construct realistic `ingest.v1` envelopes (email-shaped, telegram-shaped) and inject directly into `ingest_v1()` at the switchboard boundary. Verify downstream effects end-to-end (e.g., email envelope about a meeting → calendar event created, telegram envelope "log my run" → health measurement recorded, interactive message → reply sent via notify). Bypasses connector normalization (already unit-tested) to focus on the interesting path: switchboard → classification → butler → tool → effect.
- **Explicitly not CI/CD**: This suite burns real tokens and requires real credentials. It's the manual validation suite run locally or in a staging environment, not gated in pipelines.
- **Simplify scenario definitions**: Consolidate `E2EScenario` and routing test definitions into a single prompt corpus with expected routing, expected tool-call, and expected channel-effect annotations.

## Capabilities

### New Capabilities
- `e2e-ecosystem-staging`: Staged butler ecosystem bootstrap with OAuth prompting, connectivity validation, and teardown. Replaces current `butler_ecosystem` fixture.
- `model-benchmark-harness`: Configurable model-pinning harness that runs the same test corpus across multiple models, standardizing the model for switchboard + all butlers per run.
- `routing-scorecard`: Switchboard routing accuracy evaluation — for each prompt, compare actual butler routing against expected. Produce per-model accuracy scores.
- `tool-call-scorecard`: Downstream tool-call accuracy evaluation — for forwarded prompts, compare actual tool calls against expected. Produce per-model accuracy scores.
- `scorecard-reporting`: JSON + markdown scorecard generation with per-model, per-category, per-butler breakdowns.
- `ingress-injection`: Construct realistic `ingest.v1` envelopes (email-shaped, telegram-shaped) and inject directly into `ingest_v1()`. Verify downstream effects: DB state changes, outbound tool calls, reply delivery. Tests the full path from switchboard boundary → classification → butler → tool → effect without external service dependencies.

### Modified Capabilities
- _(none — existing specs are not modified at the requirements level; existing E2E tests are reimplemented using new infrastructure)_

## Impact

- **Code**: `tests/e2e/` — full rewrite of conftest.py, scenarios.py, and scenario runner. New benchmark harness module. Existing non-benchmarking test files adapted to new staging.
- **Configuration**: New pytest markers (`benchmark`, `routing-accuracy`, `tool-accuracy`). Model list configurable via env var or `pyproject.toml`.
- **Dependencies**: May need `pytest-benchmark` or custom reporting; `testcontainers` dependency stays.
- **Runtime**: This suite is run manually (not in CI/CD). Requires interactive terminal for OAuth credential setup on first run; cached credentials reused subsequently. Real LLM tokens are consumed.
- **External services**: Tests interact with real LLM APIs (token burn). Ingress is injected at `ingest_v1()` — no real Gmail/Telegram round-trips needed. OAuth credentials required for butler modules that call external APIs (Google Calendar, etc.).
