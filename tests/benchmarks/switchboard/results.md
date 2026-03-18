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
| ollama/qwen3.5:9b | 65.1% | 47709ms | 80034ms | 96349ms | 82138ms | 0.0 | 100 | 2026-03-18 |
| ollama/gemma3:12b | 0.0% | 1657ms | 1766ms | 1802ms | 1914ms | 0.6 | 100 | 2026-03-17 |
| ollama/qwen3:14b | 0.0% | 1478ms | 1574ms | 1707ms | 1498ms | 0.7 | 100 | 2026-03-18 |
