# Discretion Layer Benchmark Results

Maintained by the `discretion_layer` benchmark suite. Each row is updated
idempotently when the benchmark is run for that model.

```
uv run pytest tests/benchmarks/discretion_layer/ -v --override-ini="addopts=" --model <name>
```

| Model | Accuracy | FWD Recall | IGN Prec | p50 | p95 | p99 | Cold Start | req/s | Prompts | Date |
|-------|----------|------------|----------|-----|-----|-----|------------|-------|---------|------|
| gemma3:12b | 86.2% | 98.4% | 74.4% | 523ms | 677ms | 744ms | — | 1.9 | 500 | 2026-03-16 |
