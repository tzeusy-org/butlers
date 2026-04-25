# Adapter Integration Testing

## Purpose
Defines the standard integration test contract that every LLM runtime adapter must implement, the shared test harness, and per-adapter nightly test suites that verify parser correctness against real CLI output.

## ADDED Requirements

### Requirement: Standard Integration Test Contract
Every registered RuntimeAdapter SHALL have a corresponding nightly integration test file at `tests/adapters/test_<adapter_name>_integration.py`. Each file SHALL be marked with `@pytest.mark.nightly` and `@pytest.mark.skipif` when the adapter's binary is not on PATH.

#### Scenario: Integration test file exists per adapter
- **WHEN** a new RuntimeAdapter is registered via `register_adapter()`
- **THEN** a matching `test_<name>_integration.py` file SHALL exist in `tests/adapters/`
- **AND** it SHALL be marked `@pytest.mark.nightly`
- **AND** it SHALL be skipped if the adapter's binary is not available

#### Scenario: Integration tests excluded from default CI
- **WHEN** the default pytest addopts include `-m 'not nightly'`
- **THEN** all adapter integration tests SHALL be deselected
- **AND** zero integration tests SHALL execute during normal CI runs

### Requirement: Text Extraction Verification
Each adapter integration test suite SHALL verify that the adapter's parser extracts non-empty, non-None text from the real CLI's output when given a prompt that expects a text response.

#### Scenario: Simple text response parsed
- **WHEN** the CLI is invoked with a simple text prompt (e.g., "What is 2+2? One word.")
- **THEN** the parser SHALL return a non-None `result_text`
- **AND** the `result_text` SHALL be a non-empty string

#### Scenario: Multi-part text concatenated
- **WHEN** the CLI emits multiple text events in a single session
- **THEN** the parser SHALL concatenate all text parts into a single `result_text`

### Requirement: Tool Call Extraction Verification
Each adapter integration test suite SHALL verify that tool calls are extracted with non-empty `name`, `id`, and `input` fields when the CLI executes a tool.

#### Scenario: Tool call with name and input extracted
- **WHEN** the CLI is invoked with a prompt that triggers tool use (e.g., "Use the shell to run: echo 'test'")
- **THEN** the parser SHALL return at least one tool call dict
- **AND** each tool call SHALL have a non-empty `name` string
- **AND** each tool call SHALL have a non-empty `id` string
- **AND** each tool call SHALL have an `input` that is a non-empty dict

#### Scenario: Tool call input contains expected keys
- **WHEN** a shell/bash tool call is extracted
- **THEN** the `input` dict SHALL contain the command or equivalent invocation parameter

### Requirement: Token Usage Extraction Verification
Each adapter integration test suite SHALL verify token usage extraction. For adapters whose CLI emits token counts, usage SHALL contain positive integer `input_tokens` and `output_tokens`. For adapters whose CLI does not emit tokens, the test SHALL explicitly assert `usage is None` to document the known limitation.

#### Scenario: Token usage extracted (token-emitting adapters)
- **WHEN** the CLI is invoked and the adapter's CLI emits token usage data
- **THEN** the parser SHALL return a non-None `usage` dict
- **AND** `usage["input_tokens"]` SHALL be a positive integer
- **AND** `usage["output_tokens"]` SHALL be a positive integer

#### Scenario: Token usage documented as None (non-emitting adapters)
- **WHEN** the adapter's CLI does not emit token usage (e.g., Gemini)
- **THEN** the parser SHALL return `usage = None`
- **AND** the integration test SHALL explicitly assert `usage is None`

#### Scenario: Multi-step token accumulation
- **WHEN** the CLI performs multiple steps (e.g., tool call then text response)
- **THEN** token usage from all steps SHALL be accumulated into the final `usage` dict

### Requirement: Process Metadata Verification
Each subprocess-based adapter integration test suite SHALL verify that `last_process_info` is populated after `invoke()` with `pid`, `exit_code`, `runtime_type`, and `command` fields.

#### Scenario: Process info populated after successful invoke
- **WHEN** `adapter.invoke()` completes successfully
- **THEN** `adapter.last_process_info` SHALL be a non-None dict
- **AND** `last_process_info["pid"]` SHALL be a positive integer
- **AND** `last_process_info["exit_code"]` SHALL be 0
- **AND** `last_process_info["runtime_type"]` SHALL equal the adapter's registered name

#### Scenario: SDK-based adapter skips process info
- **WHEN** the adapter uses an SDK (not subprocess) for invocation
- **THEN** process metadata verification SHALL be skipped for that adapter

### Requirement: Raw Output Format Verification
Each subprocess-based adapter integration test suite SHALL include tests that validate the raw JSON/event structure from the CLI binary, independent of the parser. This catches format changes at the source before they reach parser logic.

#### Scenario: Events contain expected envelope/structure
- **WHEN** the CLI is invoked with `--format json` (or equivalent)
- **THEN** each output event SHALL contain the expected structural keys for that adapter's format
- **AND** event types SHALL match the set the parser handles

#### Scenario: Text events contain text payload
- **WHEN** the CLI emits a text event
- **THEN** the event SHALL contain a text payload at the expected nesting level

#### Scenario: Tool events contain tool metadata
- **WHEN** the CLI emits a tool use event
- **THEN** the event SHALL contain tool name, call ID, and input at the expected nesting level

### Requirement: Shared Test Harness
A shared set of fixtures and helpers SHALL be provided in `tests/adapters/conftest.py` to reduce boilerplate across adapter integration tests.

#### Scenario: CLI runner fixture available
- **WHEN** an integration test needs to invoke a CLI binary
- **THEN** it SHALL use the shared `run_cli(binary, args, prompt)` fixture
- **AND** the fixture SHALL return `(stdout, stderr, returncode)` with configurable timeout

#### Scenario: JSONL event parser available
- **WHEN** an integration test needs to parse raw CLI JSON output
- **THEN** it SHALL use the shared `parse_jsonl_events(stdout)` helper
- **AND** the helper SHALL return a list of parsed event dicts, skipping non-JSON lines

#### Scenario: Binary availability skip helper
- **WHEN** an adapter's CLI binary is not installed
- **THEN** the `skip_if_no_binary(name)` helper SHALL cause tests to be skipped with a descriptive reason message

### Requirement: Structural Assertions Only
Integration tests SHALL assert on structural properties of extracted data (non-None, non-empty, correct type, positive values). Tests SHALL NOT assert on LLM behavioral output (specific text content, exact token counts, tool call ordering).

#### Scenario: Text assertion is structural
- **WHEN** asserting on `result_text`
- **THEN** the test SHALL check `result_text is not None` and `len(result_text) > 0`
- **AND** SHALL NOT check for specific words or phrases in the response

#### Scenario: Token assertion is structural
- **WHEN** asserting on `usage`
- **THEN** the test SHALL check `isinstance(usage["input_tokens"], int)` and `> 0`
- **AND** SHALL NOT check for specific token count values

#### Scenario: Tool call assertion is structural
- **WHEN** asserting on `tool_calls`
- **THEN** the test SHALL check `len(tool_calls) >= 1` and field presence
- **AND** SHALL NOT assert exact tool call count or ordering
