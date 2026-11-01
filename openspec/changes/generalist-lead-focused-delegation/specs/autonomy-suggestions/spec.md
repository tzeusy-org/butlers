## MODIFIED Requirements

### Requirement: MCP Tool Surface (Suggestions)

The autonomy suggestions feature SHALL add exactly 3 new MCP tools to the approvals module: `list_promotion_suggestions`, `confirm_promotion_suggestion`, `dismiss_promotion_suggestion`. `confirm_promotion_suggestion` and `dismiss_promotion_suggestion` are owner-decision tools: they SHALL NOT be LLM-presentable or grantable to any goal invocation and SHALL be reachable only through owner-authenticated dashboard calls and signed owner channel callbacks. `list_promotion_suggestions` MAY be granted as a read capability.

Source: RFC 0038 D4; RFC 0021
Scope: v1-mandatory

#### Scenario: New tools are registered

- **WHEN** the approvals module is registered with autonomy features enabled
- **THEN** the 3 suggestion tools MUST be available alongside the existing 13 approval tools
- **AND** the total tool count MUST be 16

#### Scenario: A goal cannot confirm a promotion

- **WHEN** a goal invocation searches its catalog for a tool that confirms or dismisses a promotion suggestion
- **THEN** neither `confirm_promotion_suggestion` nor `dismiss_promotion_suggestion` is listed or callable
- **AND** the suggestion stays pending until the owner decides through an owner channel
