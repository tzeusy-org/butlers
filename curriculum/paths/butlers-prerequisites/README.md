# Butlers Prerequisites

Total estimated smart-human study time: 57 hours

Goal: build enough technical background to understand the Butlers repository and make safe, scoped first contributions.

Learner profile: Python-capable developer who may be unfamiliar with MCP, LLM runtime adapters, async service orchestration, multi-schema PostgreSQL, approval gates, or retrieval-backed memory systems.

## Section Overview

| Order | Module | Why you need it | Hours | Prerequisites | Progress |
|---:|---|---|---:|---|---|
| 1 | System boundaries | Explains the architecture before local names blur it. | 8 | Basic Python/web concepts | [ ] |
| 2 | Async runtime and failure semantics | Explains queues, subprocesses, retries, and task safety. | 9 | Module 1 | [ ] |
| 3 | PostgreSQL storage and migrations | Explains the data model and migration hazards. | 9 | Modules 1-2 | [ ] |
| 4 | Identity, secrets, and approvals | Explains trust, credentials, and side effects. | 8 | Modules 1 and 3 | [ ] |
| 5 | Time, scheduling, and autonomous workflows | Explains recurring work, calendar projection, and recurrence. | 7 | Modules 2-4 | [ ] |
| 6 | Observability, operations, and test topology | Explains how to debug and verify changes. | 8 | Modules 1-5 | [ ] |
| 7 | Memory, retrieval, and domain surfaces | Explains memory/provenance plus broader product surfaces. | 8 | Modules 1-6 | [ ] |

## Stop Here If

Stop after Module 3 if your goal is only to read the core architecture and not change behavior.

Stop after Module 4 if your first change is documentation or a small non-scheduled, non-memory tool fix.

Complete all seven modules before changing routing, migrations, runtime adapters, scheduler/calendar behavior, credentials, approvals, memory, deployment, or test infrastructure.

## Path Mastery

- [ ] I can explain the system boundaries without opening the repo.
- [ ] I can identify whether a proposed change touches runtime, storage, trust, scheduling, ops, tests, memory, or UI/API contracts.
- [ ] I can name the tests and docs I would inspect before modifying a high-risk area.
- [ ] I can defer provider-specific API details until I am actually changing that provider.
