# Performance Discipline

Performance work in Butlers should be evidence-driven. The goal is better
latency, throughput, or efficiency without damaging clarity, correctness, or
operability.

## Core Rules

- Measure before optimizing.
- Optimize the real bottleneck, not the most visible code.
- Prefer simple structural wins over clever micro-optimizations.
- Preserve diagnosability while improving speed.

## Anti-patterns

- reducing verification depth in the name of throughput
- suppressing logs or traces just because they are noisy
- changing behavior to look faster while losing guarantees

## Performance Change Checklist

Before claiming a performance improvement, be able to answer:

1. What was slow or wasteful?
2. How was it measured?
3. What invariant stayed protected?
4. What verification proved behavior did not regress?
5. What should operators watch after the change?

If those answers are weak, the optimization is not ready.

## Reasoning Architecture Evaluation

The [goal architecture](../heart-and-soul/architecture.md) makes agent
allocation and model choice replaceable execution choices. Compare direct lead
execution, focused delegation, and model tiers on equivalent tool/context
access and disclosed compute budgets. Report whole-goal success, constraint
preservation, useful proactive actions, missed obligations, owner corrections,
latency, and total cost including discovery, retries, recovery and idle capacity.

Include simple work that should finish without children and cross-domain work
with conflicting constraints. Token price, tool count, agent count and search
hit rate are diagnostic inputs, not success criteria. Future model capability
and cost assumptions must be labeled and evaluated across plausible scenarios;
a vendor benchmark is not evidence that Butlers improved.
