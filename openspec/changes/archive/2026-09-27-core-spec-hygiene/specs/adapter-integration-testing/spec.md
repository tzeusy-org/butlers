## MODIFIED Requirements

### Requirement: Shared Test Harness
A shared set of fixtures and helpers SHALL be provided in `tests/adapters/conftest.py` to reduce boilerplate across adapter integration tests.

#### Scenario: CLI runner helper available
- **WHEN** an integration test needs to invoke a CLI binary
- **THEN** it SHALL use the shared `run_cli(binary, args, prompt, timeout=120, cwd="/tmp")` module-level helper function from conftest (a plain function, not a pytest fixture)
- **AND** the helper SHALL return `(stdout, stderr, returncode)` via `subprocess.run()`
- **AND** the helper SHALL default `cwd` to `/tmp` to avoid polluting the working directory

#### Scenario: JSONL event parser available
- **WHEN** an integration test needs to parse raw CLI JSON output
- **THEN** it SHALL use the shared `parse_jsonl_events(stdout)` helper
- **AND** the helper SHALL return a list of parsed event dicts, skipping non-JSON lines

#### Scenario: Binary availability skip
- **WHEN** an adapter's CLI binary is not installed on PATH
- **THEN** the adapter's integration tests SHALL be skipped via a module-level `@pytest.mark.skipif(not shutil.which("<binary>"), reason="<binary> not on PATH")` marker (the Claude adapter additionally guards on `ANTHROPIC_API_KEY`)
