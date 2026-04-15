#!/usr/bin/env python3
"""Inject a synthetic QA canary and wait for it to reach unfixable status."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

TERMINAL_FAILURE_STATUSES = {
    "failed",
    "timeout",
    "anonymization_failed",
    "pr_open",
    "pr_merged",
}
SUCCESS_STATUS = "unfixable"


@dataclass
class InvokeResult:
    fingerprint: str
    finding_id: str
    patrol_id: str


def _normalize_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/api"):
        value = value[: -len("/api")]
    return value


def _api_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}/api{path}"


def _headers(api_key: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{method} {url} -> connection failed: {exc}") from exc


def _invoke_canary(base_url: str, api_key: str | None, source_butler: str) -> InvokeResult:
    payload = {
        "source_butler": source_butler,
        "event_summary": (
            "Synthetic QA validation canary injected by operator; this is not a real product "
            "bug and should follow the UNFIXABLE protocol."
        ),
        "exception_type": "SyntheticQaValidationError",
        "call_site": "qa.validation.synthetic",
        "severity": 2,
        "trigger_source": "dashboard",
    }
    body = _request_json(
        "POST",
        _api_url(base_url, "/qa/dev/synthetic-findings"),
        headers=_headers(api_key),
        payload=payload,
    )
    data = body["data"]
    return InvokeResult(
        fingerprint=data["fingerprint"],
        finding_id=data["finding_id"],
        patrol_id=data["patrol_id"],
    )


def _fetch_investigations(base_url: str, api_key: str | None, limit: int) -> list[dict[str, Any]]:
    url = _api_url(base_url, f"/qa/investigations?limit={limit}&offset=0")
    body = _request_json("GET", url, headers=_headers(api_key), payload=None)
    return body["data"]


def _find_investigation(investigations: list[dict[str, Any]], fingerprint: str) -> dict[str, Any] | None:
    for item in investigations:
        if item.get("fingerprint") == fingerprint:
            return item
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject a synthetic QA validation canary and wait for it to become unfixable.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BUTLER_QA_INVOKE_BASE_URL", "http://localhost:41200"),
        help="Dashboard root URL or prefixed API root (default: %(default)s).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DASHBOARD_API_KEY"),
        help="Dashboard API key. Falls back to DASHBOARD_API_KEY.",
    )
    parser.add_argument(
        "--source-butler",
        default="general",
        help="Butler name to attribute the synthetic finding to.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Maximum time to wait for the canary to reach unfixable.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=10.0,
        help="Polling interval for /api/qa/investigations.",
    )
    parser.add_argument(
        "--investigation-limit",
        type=int,
        default=100,
        help="How many recent investigations to inspect per poll.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the final result as JSON.",
    )
    return parser


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main() -> int:
    args = build_parser().parse_args()

    invoke = _invoke_canary(args.base_url, args.api_key, args.source_butler)
    start = time.monotonic()

    last_status = "not_seen"
    last_attempt_id = None

    while True:
        investigations = _fetch_investigations(
            args.base_url, args.api_key, args.investigation_limit
        )
        match = _find_investigation(investigations, invoke.fingerprint)
        if match is not None:
            last_status = str(match.get("status"))
            last_attempt_id = match.get("id")
            if last_status == SUCCESS_STATUS:
                _emit(
                    {
                        "result": "success",
                        "fingerprint": invoke.fingerprint,
                        "finding_id": invoke.finding_id,
                        "patrol_id": invoke.patrol_id,
                        "attempt_id": last_attempt_id,
                        "status": last_status,
                        "closed_at": match.get("closed_at"),
                    },
                    args.json,
                )
                return 0
            if last_status in TERMINAL_FAILURE_STATUSES:
                _emit(
                    {
                        "result": "failure",
                        "reason": "unexpected_terminal_status",
                        "fingerprint": invoke.fingerprint,
                        "finding_id": invoke.finding_id,
                        "patrol_id": invoke.patrol_id,
                        "attempt_id": last_attempt_id,
                        "status": last_status,
                        "error_detail": match.get("error_detail"),
                    },
                    args.json,
                )
                return 1

        elapsed = time.monotonic() - start
        if elapsed >= args.timeout_seconds:
            _emit(
                {
                    "result": "failure",
                    "reason": "timeout_waiting_for_unfixable",
                    "fingerprint": invoke.fingerprint,
                    "finding_id": invoke.finding_id,
                    "patrol_id": invoke.patrol_id,
                    "attempt_id": last_attempt_id,
                    "last_status": last_status,
                    "timeout_seconds": args.timeout_seconds,
                },
                args.json,
            )
            return 1

        time.sleep(args.poll_interval_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
