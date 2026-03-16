# Switchboard Routing Benchmark Results

Maintained by the `switchboard` benchmark suite. Each row is updated
idempotently when the benchmark is run for that model.

```
uv run pytest tests/benchmarks/switchboard/ -v --override-ini="addopts=" --model <name>
```

| Model | Accuracy | p50 | p95 | p99 | Cold Start | req/s | Scenarios | Date |
|-------|----------|-----|-----|-----|------------|-------|-----------|------|
| glm-5 | 0.0% | 1370ms | 1600ms | 1835ms | 1362ms | 0.7 | 322 | 2026-03-16 |
| opencode-go/glm-5 | 80.6% | 25366ms | 49741ms | 90148ms | 26756ms | 0.0 | 100 | 2026-03-16 |
