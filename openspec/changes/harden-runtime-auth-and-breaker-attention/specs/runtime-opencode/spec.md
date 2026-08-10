## MODIFIED Requirements

### Requirement: Model Selection

The adapter SHALL retain the resolved catalog model identifier as the canonical
caller-visible identity for routing, pricing, spend enforcement, token-ledger
history, and provenance. At the OpenCode CLI execution boundary it SHALL derive
a separate execution identifier with a named, pure mapping: a canonical
`opencode-go/<native-id>` identifier is passed to `--model` as `<native-id>`;
other qualified identifiers and existing bare identifiers are passed unchanged.
The mapping SHALL be used by normal OpenCode invocation, generated runtime
configuration, and the OpenCode CLI-auth health command. It SHALL not mutate a
catalog row, pricing key, historical dispatch record, or provider label.

ID: REQ-runtime-opencode-001
Source: runtime-opencode Model Selection; model-catalog REQ-model-catalog-002; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Canonical OpenCode Go Minimax identity has a native execution argument

- **WHEN** the resolved catalog model is
  `opencode-go/minimax-m2.7`
- **THEN** the command includes `--model minimax-m2.7` before the prompt
- **AND** all caller-visible routing, pricing, and provenance identity remains
  `opencode-go/minimax-m2.7`

#### Scenario: Canonical OpenCode Go Mimo identity has a native execution argument

- **WHEN** the resolved catalog model is `opencode-go/mimo-v2.5`
- **THEN** the command includes `--model mimo-v2.5` before the prompt
- **AND** all caller-visible routing, pricing, and provenance identity remains
  `opencode-go/mimo-v2.5`

#### Scenario: Other provider syntax is preserved

- **WHEN** a qualified OpenCode provider model such as
  `anthropic/claude-sonnet-4-5`, an unrelated runtime model, or an existing
  bare model string is selected
- **THEN** the adapter does not strip, prepend, or otherwise rewrite it
- **AND** the mapping is not applied outside the OpenCode CLI execution
  boundary

#### Scenario: CLI-auth health command shares the execution mapping

- **WHEN** the OpenCode Go CLI-auth health check invokes its pinned canonical
  catalog/provider model
- **THEN** it derives the exact same native `--model` argument as the adapter
- **AND** its check does not create catalog verification or routed dispatch
  provenance

#### Scenario: No model specified

- **WHEN** no model is provided
- **THEN** the `--model` flag is omitted and OpenCode uses its configured
  default
