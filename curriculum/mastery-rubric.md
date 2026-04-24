# Mastery Rubric

Use checkboxes as evidence of capability, not proof that a page was skimmed.

## Levels

| Level | Meaning | Typical evidence |
|---|---|---|
| `exposed` | You recognize the vocabulary and can define the major terms. | You can identify the concept in docs, code, tests, or logs. |
| `working` | You can reason with the concept in this repository's context. | You can answer sample Q&A and explain where the concept appears without notes. |
| `contribution-ready` | You can use the concept to make or review a safe change. | You can predict failure modes, pick tests, and explain trade-offs for this repo. |

## What `[ ]` And `[X]` Mean

- `[ ]` means the capability has not been demonstrated yet.
- `[X]` means you can explain the idea, answer the challenge questions, and connect it back to repository evidence without relying on the curriculum text.

## Depth Guidance

Use `exposed` familiarity for narrow or deferable surfaces such as WhatsApp sidecar internals if you are not changing them.

Use `working` familiarity for concepts needed to read the repo: MCP, daemon/session separation, modules/connectors, routing envelopes, JSONB, scheduler vocabulary, observability, and test topology.

Use `contribution-ready` familiarity for concepts that can cause data loss, security issues, duplicate side effects, broken migrations, task leaks, or misleading runtime behavior: async cancellation, migrations, schema isolation, credentials, OAuth, approvals, idempotency, scheduling advancement, model catalog authority, and memory provenance.

## Path-Level Completion

The path is complete when you can:

- [ ] Explain why every module exists in the curriculum.
- [ ] Trace a user message or scheduled trigger into an LLM runtime session.
- [ ] Identify the storage, trust, runtime, and test hazards for a proposed change.
- [ ] Pick a first contribution area that matches your current mastery.
- [ ] State which areas still require deeper study before you modify them.
