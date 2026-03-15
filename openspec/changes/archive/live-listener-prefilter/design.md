# Live Listener Pre-Filter

## Context

The live-listener connector pipeline runs:
`audio → VAD → transcription → filter_gate → discretion (LLM) → envelope → ingest`

The discretion layer is an LLM call per transcribed segment. For a mic in a room where a YouTube video, podcast, or TV show is playing, the VAD detects speech, transcription succeeds, and every snippet hits the discretion LLM — producing an IGNORE verdict 95%+ of the time at significant latency and cost.

The filter_gate only supports `mic_id` rules (whole-mic block/allow) and cannot help with content-level noise. We need a cheap heuristic layer between transcription and discretion that rejects the obvious noise without an LLM call.

## Goals

- Reduce discretion LLM calls by 80%+ during passive media consumption
- Zero false negatives on genuinely directed speech (err on the side of forwarding to discretion)
- No external dependencies — pure Python, no I/O, sub-millisecond per evaluation
- Observable via Prometheus counters per rejection reason

## Non-Goals

- Replacing the discretion layer (pre-filter is coarse; discretion handles nuance)
- Speaker identification (future work, separate capability)
- Wake word detection (explicitly rejected per original design — preserves ambient model)

## Design

### Pipeline Position

```
transcription → filter_gate → [PRE-FILTER] → discretion → envelope → ingest
```

The pre-filter runs after the filter_gate (which is a cheap mic_id check) and before discretion (which is the expensive LLM call). A rejected utterance never reaches discretion, saving both latency and cost.

### Heuristics

Evaluated in order. First rejection wins. If all pass, the utterance proceeds to discretion.

#### 1. Fragment Rejection

Utterances with fewer than `min_words` tokens (default: 3) are auto-rejected unless they match a short-command allowlist.

**Rationale:** Transcription artifacts like "yeah", "uh huh", "okay" are almost never directed at an assistant. But short commands like "stop", "help", "what time is it" are — so we maintain an allowlist of patterns that should pass despite being short.

**Allowlist patterns (configurable, default set):**
- Single-word questions: matches `?` at end
- Known short commands: "stop", "help", "pause", "thanks", "cancel", "yes", "no"

**Why not filter these at VAD level:** VAD correctly identifies them as speech. The issue is that they're not *useful* speech for the butler. This is a semantic distinction that belongs after transcription.

#### 2. Burst Rate Suppression

Track utterance timestamps in a sliding window. If the rate exceeds `burst_max_rate` utterances within `burst_window_s` seconds (default: 15 in 60s), auto-reject all subsequent utterances until the rate drops below the threshold.

**Rationale:** Natural conversation rarely exceeds 5-8 utterances/minute sustained. A podcast or YouTube video easily produces 15-25. This single heuristic catches the "media playing on speakers" scenario without any content analysis.

**State:** Per-mic deque of timestamps, trimmed on each evaluation.

**Hysteresis:** Once burst mode activates, it stays active until the rate drops below `burst_resume_rate` (default: 50% of `burst_max_rate`, i.e. 7-8/min). This prevents rapid toggling at the threshold boundary.

**Edge case — user talks during media:** This is an acceptable false negative. The user can pause the media, or the burst window will naturally clear within 60s of the media stopping. The alternative (trying to distinguish user voice from media voice) requires speaker diarization, which is a non-goal for this change.

#### 3. Near-Duplicate Suppression

Maintain a sliding window of recent transcript texts (last `dedup_window_s` seconds, default: 120s). If the normalized text of a new utterance matches any entry with similarity above `dedup_threshold` (default: 0.85), reject it.

**Normalization:** lowercase, strip punctuation, collapse whitespace.

**Similarity:** Ratio of longest common subsequence length to max string length. This is cheaper than Levenshtein and handles the common case (repeated lyrics, ad slogans, notification sounds that transcribe identically).

**Why not exact match:** Transcription is non-deterministic. The same spoken phrase may transcribe as "subscribe and hit the bell" one time and "subscribe and hit that bell" the next. A similarity threshold catches these near-misses.

**State:** Per-mic deque of `(timestamp, normalized_text)` tuples, trimmed by age on each evaluation.

### Interface

```python
@dataclass
class PreFilterResult:
    allowed: bool
    reason: str  # "passed", "fragment", "burst", "duplicate"

class PreFilterConfig:
    """Loaded from env vars with LIVE_LISTENER_PREFILTER_ prefix."""
    enabled: bool = True
    min_words: int = 3
    fragment_allowlist: list[str]  # loaded from default set + env override
    burst_window_s: float = 60.0
    burst_max_rate: int = 15
    burst_resume_pct: float = 0.5
    dedup_window_s: float = 120.0
    dedup_threshold: float = 0.85

class PreFilter:
    """Per-mic stateful pre-filter. Thread-safe (called from asyncio task)."""

    def __init__(self, mic_name: str, config: PreFilterConfig) -> None: ...

    def evaluate(self, text: str, timestamp: float | None = None) -> PreFilterResult:
        """Evaluate a transcribed utterance. Pure CPU, no I/O, no async."""
        ...
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LIVE_LISTENER_PREFILTER_ENABLED` | `true` | Master toggle |
| `LIVE_LISTENER_PREFILTER_MIN_WORDS` | `3` | Fragment rejection word threshold |
| `LIVE_LISTENER_PREFILTER_BURST_WINDOW_S` | `60` | Burst detection window (seconds) |
| `LIVE_LISTENER_PREFILTER_BURST_MAX_RATE` | `15` | Max utterances per burst window |
| `LIVE_LISTENER_PREFILTER_BURST_RESUME_PCT` | `0.5` | Rate must drop to this fraction of max before exiting burst mode |
| `LIVE_LISTENER_PREFILTER_DEDUP_WINDOW_S` | `120` | Near-duplicate lookback window (seconds) |
| `LIVE_LISTENER_PREFILTER_DEDUP_THRESHOLD` | `0.85` | Similarity threshold for duplicate detection |

### Metrics

New Prometheus counter:

```
connector_live_listener_prefilter_total{mic, reason}
```

`reason` values: `passed`, `fragment`, `burst`, `duplicate`, `disabled`

### Connector Pipeline Integration

In `_process_segment()`, the pre-filter call is inserted between the filter_gate check and the discretion evaluator:

```python
# --- Filter gate (existing) ---
decision = evaluate_voice_filter(filter_evaluator, spec.name)
if not decision.allowed:
    ll_metrics.inc_segments("discarded_silence")
    return

# --- Pre-filter (NEW) ---
pf_result = self._prefilters[mic].evaluate(result.text, timestamp=time.time())
ll_metrics.inc_prefilter(pf_result.reason)
if not pf_result.allowed:
    ll_metrics.inc_segments("prefiltered")
    return

# --- Discretion (existing, now reached less often) ---
disc_result = await evaluator.evaluate(result.text, timestamp=time.time())
```

### Interaction with Discretion Context Window

The pre-filter does NOT add rejected utterances to the discretion evaluator's context window. This is intentional — the discretion LLM should only see utterances that survived pre-filtering, so its context window represents the "real" conversational stream rather than being polluted by media noise.

However, the pre-filter's own dedup window DOES include all utterances (including those it rejects), since it needs the full stream to detect duplicates and measure burst rate.

## Decisions

### 1. Pre-filter is deterministic, not ML-based

A simple rule engine rather than a small classifier model. The heuristics are transparent, debuggable, and have zero cold-start cost. If we find cases that need smarter filtering, we add heuristics — not a second ML model before the first ML model (discretion).

### 2. Fail-open on configuration errors

If `PreFilterConfig` can't parse an env var, it logs a warning and uses the default. A misconfigured pre-filter should never block legitimate speech.

### 3. Per-mic state, not shared

Each mic gets its own `PreFilter` instance with independent burst/dedup state. A TV playing in the kitchen shouldn't suppress the office mic's utterances.

### 4. No persistence

Pre-filter state (burst timestamps, dedup window) is in-memory only. On process restart, the windows are empty. This is fine — the windows are short (60-120s) and will repopulate naturally.

## Tasks

1. **`prefilter.py`** — `PreFilterConfig`, `PreFilterResult`, `PreFilter` class with fragment/burst/dedup heuristics
2. **`config.py`** — Add pre-filter env vars to `LiveListenerConfig`
3. **`metrics.py`** — Add `prefilter_total` counter and `inc_prefilter()` helper
4. **`connector.py`** — Wire pre-filter into `_process_segment()` between filter_gate and discretion; instantiate per-mic in `start()`
5. **Tests** — Unit tests for each heuristic, edge cases (empty text, exact threshold, burst hysteresis, dedup similarity boundary)
