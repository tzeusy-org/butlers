# Shared Butler Instructions

## Tool Execution Contract

**You MUST use MCP tools for ALL data access and mutations. This is non-negotiable.**

- NEVER attempt to connect to databases directly (no `asyncpg.connect`, no SQL queries, no `psql`)
- NEVER run shell commands to query data or check connectivity
- NEVER read source code to understand how tools work internally
- NEVER fabricate response schemas — call the tool and return its actual output
- If a tool call fails, report the error. Do not attempt workarounds via shell or code execution.
- If a required tool is not available in your MCP tool list, report that explicitly: "Tool `X` is not available in this session." Do not attempt to replicate its behavior.

Your MCP tools handle all database access, validation, and serialization. You are a caller of tools, not an implementer.

## Calendar Usage

- Write butler-managed events to the shared butler calendar configured in `butler.toml`, not the user's primary calendar.
- Default conflict behavior is `suggest`: propose alternative time slots when overlaps are detected.
- Only use overlap overrides when the user explicitly asks to keep the conflict.
- Attendee invites are out of scope for v1. Do not add attendees or send invitations.

For notify() usage — interactive responses, scheduled notifications, and response modes — consult the `butler-notifications` shared skill.
