# Butler Dev Debug Error Patterns

Use this file when the symptom already resembles a known failure mode.

## `Missing input.context.notify_request in messenger route.execute request`

Cause: something called `route_to_butler(butler="messenger", ...)` instead of `notify()`. Messenger's `route.execute` expects a structured `notify_request` envelope in `input.context`.

Fix: use the `notify` MCP tool for outbound delivery. Do not route to `messenger` via `route_to_butler`.

## Session shows `success = true` but a tool call failed

The LLM completed, but an individual tool call did not. Inspect `tool_calls` JSONB for entries whose embedded result or outcome shows an error.

## `Unsupported channel` or `No butler with module found`

`deliver()` checks the butler registry for butlers that expose the required module. Verify the target butler is registered and its `butler.toml` enables the correct module.
