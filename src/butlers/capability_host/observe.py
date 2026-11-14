"""Audit lines and metrics (RFC 0039 D15, D16).

One JSON line per request after the bearer check, with no content: never a body, argument
value, assertion, bearer, cursor or returned text. Metrics are plain counters and gauges that
:meth:`Metrics.render` exposes in the Prometheus text format; bearer refusals are counted with
no request content, since they never reach the audit line.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

AUDIT_FIELDS = (
    "request_id",
    "jti",
    "sub",
    "cid",
    "tool",
    "args_sha256",
    "outcome",
    "cause",
    "rows",
    "bytes",
    "redactions",
    "latency_ms",
    "ids",
)
PREFIX = "butlers_public_mcp_"
_GAUGES = frozenset({"owner_step_pending", "canary_latched"})


def _stdout(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def audit_line(record: dict[str, Any]) -> str:
    """Exactly the audit fields, in a fixed order; unknown keys never reach the log."""
    return json.dumps(
        {"event": "public_mcp_call", **{k: record.get(k) for k in AUDIT_FIELDS}},
        separators=(",", ":"),
    )


class Metrics:
    def __init__(self, sink: Callable[[str], None] = _stdout):
        self.sink = sink
        self.values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def inc(self, name: str, amount: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.values[key] = self.values.get(key, 0) + amount

    def set(self, name: str, value: float, **labels: str) -> None:
        self.values[(name, tuple(sorted(labels.items())))] = value

    def get(self, name: str, **labels: str) -> float:
        return self.values.get((name, tuple(sorted(labels.items()))), 0)

    def audit(self, record: dict[str, Any]) -> None:
        self.sink(audit_line(record))

    def render(self) -> str:
        lines = []
        for name in sorted({n for n, _ in self.values}):
            kind = "gauge" if name in _GAUGES else "counter"
            full = PREFIX + name + ("" if kind == "gauge" else "_total")
            lines.append(f"# TYPE {full} {kind}")
            for (n, labels), value in sorted(self.values.items()):
                if n == name:
                    text = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
                    lines.append(f"{full}{{{text}}} {value:g}" if text else f"{full} {value:g}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
