# Performance Discipline

Performance work in Butlers should be evidence-driven. The goal is better
latency, throughput, or efficiency without damaging clarity, correctness, or
operability.

## Core Rules

- Measure before optimizing.
- Optimize the real bottleneck, not the most visible code.
- Prefer simple structural wins over clever micro-optimizations.
- Preserve diagnosability while improving speed.

## Good Performance Work

Examples of the right kind of improvement:

- removing N+1 query patterns in overview and analytics paths
- batching DB reads instead of per-row follow-up queries
- reducing duplicate async work in pollers or recovery loops
- tightening test scope to speed iteration without weakening final verification
- eliminating unnecessary compatibility layers that keep extra code paths alive

## Bad Performance Work

These are anti-patterns:

- adding caching without proving repeated work is the bottleneck
- making control flow harder to understand for speculative gains
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
