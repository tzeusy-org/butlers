"""Reduce a recorded ``claude -p --output-format stream-json`` transcript to serving metadata.

Usage: ``python sanitize.py < raw.jsonl > fixture.jsonl``

Keeps only the ``system/init`` and ``result`` events, and only the fields the served-identity
parser reads. Everything else (hook, assistant, user and rate-limit events, session ids, uuids,
cwd, tool and MCP inventories, prompt and response text, permission-denial bodies) is dropped.
The response text is replaced with a fixed placeholder; permission denials keep their count only.
Every retained value is copied unchanged from the recording.
"""

from __future__ import annotations

import json
import sys

SANITIZER_VERSION = "1"

_INIT_FIELDS = ("type", "subtype", "model", "claude_code_version")
_RESULT_FIELDS = ("type", "subtype", "is_error", "num_turns", "total_cost_usd", "modelUsage")
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_MODEL_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "costUSD",
)


def sanitize_event(event: dict) -> dict | None:
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return {key: event[key] for key in _INIT_FIELDS if key in event}
    if kind != "result":
        return None
    kept = {key: event[key] for key in _RESULT_FIELDS if key in event}
    if isinstance(kept.get("modelUsage"), dict):
        kept["modelUsage"] = {
            model: {key: value[key] for key in _MODEL_USAGE_FIELDS if key in value}
            for model, value in kept["modelUsage"].items()
        }
    usage = event.get("usage")
    if isinstance(usage, dict):
        kept["usage"] = {key: usage[key] for key in _USAGE_FIELDS if key in usage}
    if isinstance(event.get("permission_denials"), list):
        kept["permission_denials"] = [{} for _ in event["permission_denials"]]
    kept["result"] = "<recorded response removed>"
    return kept


def main() -> None:
    for line in sys.stdin:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (kept := sanitize_event(event)) is not None:
            sys.stdout.write(json.dumps(kept, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
