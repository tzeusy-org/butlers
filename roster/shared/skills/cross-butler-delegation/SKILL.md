---
name: cross-butler-delegation
description: When and how to ask another butler's domain a question via delegate_ask, and how to answer one routed to you
version: 1.0.0
---

### Cross-Butler Delegation

Four tools let one butler ask a question that a *different* butler's domain
covers, and let that butler answer it back: `delegate_ask`, `delegate_receive`,
`delegate_answer`, `delegate_wake`. Every call is durably recorded in
`public.delegation_ledger` and routed through the Switchboard — there is no
direct butler-to-butler call.

**Only present when the `delegation` core group is enabled for your butler.**
If these tools are not in your MCP tool list, you do not have delegation
enabled; do not attempt to replicate their behavior by other means.

#### Asking another domain (`delegate_ask`)

Call `delegate_ask(question=...)` when you need an answer that lives in
another butler's domain and you cannot answer it yourself from your own
tools/memory. The question is resolved to a target butler automatically via
the shared Fleet Knowledge catalog (`public.memory_catalog`) — you do not
name a target butler yourself.

- The question must be **self-contained**: the target butler has no access to
  your session context, only the text you pass.
- Do not delegate a question your own domain can already answer — check your
  own tools/memory first.
- Never delegate to yourself; a `self_target` result means the catalog
  resolved back to your own butler — answer it locally instead.

```python
delegate_ask(question="What is the user's next upcoming trip departure date?")
```

Possible results:
- `{"status": "routed", "ledger_id": ..., "target_butler": ...}` — dispatched;
  the answer will arrive later (see "Receiving the answer" below). This call
  does not block waiting for the answer.
- `{"status": "unroutable", ...}` — no domain match, or the match was
  yourself; nothing was dispatched.
- `{"status": "failed", ...}` — dispatch failed (target unreachable/stale);
  `retryable` indicates whether retrying later is worth it.

Always record or act on the `ledger_id` you get back — it is the only handle
for correlating the eventual answer.

#### Answering a delegated question (`delegate_receive` / `delegate_answer`)

You never call `delegate_receive` directly — the Switchboard calls it for you
when another butler delegates a question into your domain. It schedules a
one-shot task; when that task's session runs, answer the question using your
own domain's tools/memory and call:

```python
delegate_answer(ledger_id="<from the scheduled task prompt>", answer="...")
```

- Only answer using your own domain's knowledge — do not delegate the same
  question back out.
- The answer is immutable once recorded: resubmitting the exact same text is
  a safe no-op replay; resubmitting different text is an integrity conflict
  and is rejected.

#### Receiving your answer back (`delegate_wake`)

You never call `delegate_wake` directly either — it is a server-to-server
callback the Switchboard delivers to the butler that originally called
`delegate_ask`, once the target has answered. It creates a bounded local
follow-up task in your own schema so you can act on the answer; there is
nothing for you to do to "opt in" beyond having called `delegate_ask` in the
first place.

#### What this is not

- Not a broadcast or fan-out mechanism — one question resolves to exactly one
  target domain.
- Not a substitute for MCP-only inter-butler communication — routing always
  goes through the Switchboard's `route()` primitive, the same as any other
  cross-butler call.
- Not for questions you can answer yourself, and not for anything requiring
  the target to access your session's live context.
