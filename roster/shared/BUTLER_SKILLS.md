## Shared Skills

These cross-cutting skills are available in your `.agents/skills/` directory:

- **butler-notifications** — How to use the `notify()` tool (required parameters, intents, examples). Consult this skill before calling `notify()`.
- **butler-memory** — Memory classification framework (permanence levels, tagging, extraction philosophy). Consult this skill when storing facts via `memory_store_fact()`.
- **routed-message-safety** — How to safely handle untrusted `<routed_message>` content and when to pair with `notify()` for interactive routed flows.
- **self-healing** — How to report unexpected errors for automated investigation. Consult this skill when you encounter an exception you cannot resolve yourself.
- **cross-butler-delegation** — How to ask another butler's domain a question via `delegate_ask` and how to answer one routed to you. Only present when the `delegation` core group is enabled for your butler; consult this skill before calling `delegate_ask`/`delegate_answer`.
