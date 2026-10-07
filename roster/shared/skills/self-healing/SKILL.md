---
name: self-healing
description: Report unexpected errors to QA through report_error and inspect read-only healing history.
---

# Report unexpected errors

Every roster declares the `self_healing` relay required by accepted RFC 0015.
Use its tools when available and enabled. QA owns discovery, triage, investigation,
recovery and publication; the reporting butler does not launch a healing agent.

When an unexpected exception prevents ordinary work, preserve the original error
and relevant session evidence. Supply the exception class, a short safe message,
call site and useful diagnostic reasoning. Do not include credentials, user data
or personal information. `error_message`, `traceback` and `context` are sensitive
arguments, but that metadata does not make secret disclosure safe.

```python
report_error(
    error_type="ValueError",
    error_message="Unexpected synthetic response shape",
    traceback="optional safe formatted traceback",
    call_site="module.py:parse_response",
    context="Expected a response object; observed a list. No user data included.",
    tool_name="example_tool",
    severity_hint="medium",
)
```

The seven actual arguments are `error_type`, `error_message`, optional `traceback`,
`call_site`, `context`, `tool_name` and `severity_hint` (`critical`, `high`, `medium`,
`low`). Omit unavailable optional values. The daemon supplies your identity; you
cannot choose the target or reporting butler. The relay sends at most 200 message
characters through Switchboard MCP to QA's `report_finding` tool.

`accepted=true` means QA confirmed reception into its volatile report buffer. It
is not durable finding storage, an investigation, a PR, a merge or a deployed fix.
The returned fingerprint is a reporter hint; QA independently canonicalizes its
received input and may derive a different fingerprint for truncated or missing
call-site input.

`accepted=false` includes an explicit reason: `disabled`, `qa_unavailable`,
`relay_failed` or `relay_timeout`. The complete relay is bounded by two seconds.
An uncertain route or timeout may occur after QA received the report; do not
blindly retry or claim that no central finding exists. Preserve ordinary evidence
and continue whatever work remains safe. There is no local fallback, retry tool,
worktree, watchdog or investigation queue in this module.

# Read history

```python
get_healing_status(fingerprint="<reporter fingerprint>")
get_healing_status()  # the five most recent attempts for this butler
```

This is read-only historical attempt status. A missing row does not prove that QA
never received a report, and an existing legacy active row does not block relay.
A merged PR does not prove deployment. Do not use status or relay reception to
promise a fixed running system. QA retains its own recursion barrier for QA-origin
reports and its existing trusted investigation/publication workflow.

# Configuration and unavailable tools

`[modules.self_healing]` enables relay admission by default; `enabled=false` refuses
reports. The five legacy dispatch threshold keys remain accepted but cannot set
QA policy. Failed or user-disabled module state stays visible through daemon state.
If the tools are unavailable, preserve the source evidence; do not create a local
investigation or use unregistered tool names. `retry_healing` is not a relay tool.
