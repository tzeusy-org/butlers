"""Task 2.5 smoke for the public MCP server (RFC 0039, P2 "smoke from a test pod in mcp with a
test keyring"). Run by scripts/k8s/public-mcp-smoke.sh inside a labelled probe pod in `mcp`,
with the image's venv interpreter.

Modes (argv[1]):
  keyring  stdin {"seed_hex", "kid"}: print the verifier keyring document for the test key.
  smoke    stdin {"seed_hex", "kid", "bearer", "aud", "sub", "url"}: run the checks.

Prints one verdict line per check (`ok`, `FAIL`) and timing summaries. Never prints the bearer,
the seed, an argument drawn from data or any tool result content: results are judged by shape
and error code only. Exit 0 only when every check passed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519

ISSUER = "mcp-gateway"
TYP = "butlers-mcp-assertion+v1"
CIDS = (
    "https://claude.ai/oauth/mcp-oauth-client-metadata",
    "https://chatgpt.com/connector/oauth/client.json",
)
# A tool either serves or refuses with a setup-state code (ops doc section 4) or not_found.
ACCEPTED_REFUSALS = frozenset(
    {
        "projection_apply_pending",
        "projection_version_mismatch",
        "projection_stale",
        "unavailable",
        "domain_disabled",
        "not_found",
    }
)
# propose_fact writes an inbox row (bu-y66o00.48 owns its live leg); the smoke never calls it.
SKIPPED = frozenset({"propose_fact"})
TIMED_QUERIES = ("dinner", "flight", "doctor", "birthday", "invoice", "meeting", "coffee", "gym")
UNAUTHORIZED = -32001

Transport = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def keyring(seed: bytes, kid: str, now: datetime) -> dict[str, Any]:
    """The runtime_probe_control verifier keyring holding only the test key."""
    from butlers.testing.runtime_probe_control import current_entry, keyring_document

    public = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    return keyring_document(current_entry(public, kid=kid, sign_from=now - timedelta(minutes=5)))


def sign_assertion(
    seed: bytes, kid: str, body: bytes, *, aud: str, sub: str, cid: str, now: float, **over: Any
) -> str:
    """A D7 Worker assertion bound to ``body``, the way the gateway Worker signs one."""
    message = json.loads(body)
    params = message.get("params") or {}
    claims = {
        "v": 1,
        "iss": ISSUER,
        "aud": aud,
        "sub": sub,
        "cid": cid,
        "mth": message.get("method", ""),
        "tool": params.get("name", "") if message.get("method") == "tools/call" else "",
        "path": "",
        "bsha": b64u(hashlib.sha256(body).digest()),
        "jti": b64u(os.urandom(16)),
        "iat": int(now),
        "exp": int(now) + 30,
        **over,
    }
    head = rfc8785.dumps({"alg": "EdDSA", "kid": kid, "typ": TYP})
    signing_input = f"{b64u(head)}.{b64u(rfc8785.dumps(claims))}"
    signature = ed25519.Ed25519PrivateKey.from_private_bytes(seed).sign(signing_input.encode())
    return f"{signing_input}.{b64u(signature)}"


def tool_arguments(today: date) -> dict[str, dict[str, Any]]:
    """Valid, data-free arguments for every tool the smoke calls."""
    start, end = (today - timedelta(days=30)).isoformat(), today.isoformat()
    some_id = str(uuid.uuid4())
    return {
        "search": {"query": "dinner"},
        "fetch": {"id": "fact:00000000-0000-0000-0000-000000000000"},
        "search_filtered": {"query": "dinner"},
        "entity_lookup": {"name": "smoke"},
        "entity_profile": {"entity_id": some_id},
        "graph_neighbors": {"entity_id": some_id},
        "graph_path": {"from_id": some_id, "to_id": str(uuid.uuid4())},
        "timeline": {"start": start, "end": end},
        "time_use": {"start": start, "end": end, "bucket": "week", "group_by": "category"},
        "trips": {},
        "describe": {},
        "finance_summary": {"start": start, "end": end, "group_by": "category"},
        "finance_ledger": {"start": start, "end": end},
        "finance_obligations": {"horizon_days": 30},
        "finance_records": {"kind": "accounts"},
        "health_series": {"metric": "weight", "start": start, "end": end, "bucket": "day"},
        "health_profile": {},
        "health_records": {"kind": "conditions_public"},
    }


class Unreachable(Exception):
    """No HTTP answer at all: refused, reset or timed out (on kube-router, a policy rejection
    also reads as refused)."""


def http_transport(url: str) -> Transport:
    def send(method: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise Unreachable(getattr(reason, "strerror", None) or str(reason)) from None

    return send


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, -(-95 * len(ordered) // 100) - 1))]


class Smoke:
    def __init__(self, cfg: dict[str, Any], transport: Transport, *, clock=time.time, pace=1.05):
        self.seed = bytes.fromhex(cfg["seed_hex"])
        self.kid, self.bearer = cfg["kid"], cfg["bearer"]
        self.aud, self.sub = cfg["aud"], cfg["sub"]
        self.send, self.clock, self.pace = transport, clock, pace
        self.failures = 0
        self.rid = 0

    def verdict(self, passed: bool, label: str) -> None:
        print(f"{'ok  ' if passed else 'FAIL'} {label}", flush=True)
        self.failures += 0 if passed else 1

    def body(self, method: str, params: dict | None = None) -> bytes:
        self.rid += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self.rid, "method": method}
        if params is not None:
            message["params"] = params
        return json.dumps(message).encode()

    def post(
        self, body: bytes, *, assertion: str | None = None, bearer: str | None = "default",
        method: str = "POST",
    ) -> tuple[int, dict[str, Any] | None]:  # fmt: skip
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.bearer if bearer == "default" else bearer
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if assertion is not None:
            headers["X-MCP-Assertion"] = assertion
        status, raw = self.send(method, body, headers)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    def signed(self, body: bytes, cid: str = CIDS[0], **over: Any) -> str:
        claims = {"aud": self.aud, "sub": self.sub, "cid": cid, **over}
        return sign_assertion(self.seed, self.kid, body, now=self.clock(), **claims)

    def call(self, tool: str, arguments: dict, cid: str = CIDS[0]) -> tuple[str, Any]:
        """('ok', structured result) or (error code, None); never prints either."""
        body = self.body("tools/call", {"name": tool, "arguments": arguments})
        status, message = self.post(body, assertion=self.signed(body, cid))
        if status != 200 or not isinstance(message, dict):
            return f"http_{status}", None
        if "error" in message:
            return f"rpc_{message['error'].get('code')}", None
        result = message.get("result") or {}
        if result.get("isError"):
            return str(result.get("structuredContent", {}).get("error", {}).get("code")), None
        return "ok", result.get("structuredContent")

    # -- checks ----------------------------------------------------------------------------

    def transport_and_auth(self) -> None:
        ping = self.body("ping")
        status, _ = self.post(ping, bearer=None)
        self.verdict(status == 401, f"AUTH no hop bearer is 401 (got {status})")
        status, _ = self.post(ping, bearer="not-the-hop-bearer")
        self.verdict(status == 401, f"AUTH a wrong hop bearer is 401 (got {status})")
        status, _ = self.post(b"", method="GET")
        self.verdict(status == 405, f"HTTP GET after the bearer is 405 (got {status})")

        status, message = self.post(self.body("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "public-mcp-smoke", "version": "1"},
        }))  # fmt: skip
        caps = ((message or {}).get("result") or {}).get("capabilities")
        self.verdict(
            status == 200 and isinstance(caps, dict) and set(caps) == {"tools"},
            "MCP initialize without an assertion answers; capabilities are exactly tools",
        )
        status, message = self.post(self.body("tools/list"))
        names = [t.get("name") for t in ((message or {}).get("result") or {}).get("tools", [])]
        from butlers.knowledge_read.describe import TOOLS

        self.verdict(
            tuple(names) == tuple(TOOLS), f"MCP tools/list is the pinned catalogue ({len(names)})"
        )

        body = self.body("tools/call", {"name": "describe", "arguments": {}})
        _, message = self.post(body)
        self.verdict(
            _rpc_code(message) == UNAUTHORIZED, "AUTH tools/call without an assertion is -32001"
        )
        good = self.signed(body)
        status, message = self.post(body, assertion=good)
        self.verdict(
            status == 200 and "result" in (message or {}), "AUTH a signed describe call is admitted"
        )
        _, message = self.post(body, assertion=good)
        self.verdict(_rpc_code(message) == UNAUTHORIZED, "AUTH a replayed assertion is -32001")
        for label, over, cid in (
            ("another audience", {"aud": self.aud + "x"}, CIDS[0]),
            ("an unlisted subject", {"sub": "0"}, CIDS[0]),
            ("an unknown client", {}, "https://client.invalid/metadata"),
            ("a body it does not bind", {"bsha": b64u(hashlib.sha256(b"x").digest())}, CIDS[0]),
        ):
            _, message = self.post(body, assertion=self.signed(body, cid, **over))
            self.verdict(
                _rpc_code(message) == UNAUTHORIZED, f"AUTH an assertion for {label} is -32001"
            )

    def first_call(self) -> None:
        """The first tool call after a start runs the canary probe (D15); time it."""
        t = time.perf_counter()
        code, _ = self.call("entity_lookup", {"name": "smoke"})
        elapsed = (time.perf_counter() - t) * 1000
        print(f"TIMING first tool call (runs the canary probe): {elapsed:.0f}ms -> {code}")
        self.verdict(
            code == "ok" or code in ACCEPTED_REFUSALS,
            f"CANARY probe passed or refused cleanly ({code})",
        )

    def catalogue(self) -> None:
        from butlers.knowledge_read.describe import TOOLS

        arguments = tool_arguments(datetime.now(UTC).date())
        code, result = self.call("describe", {})
        self.verdict(code == "ok", f"TOOL describe answers ({code})")
        found_entity = found_item = None
        for tool in TOOLS:
            if tool in SKIPPED or tool == "describe":
                continue
            args = dict(arguments[tool])
            if tool == "fetch" and found_item:
                args["id"] = found_item
            if tool in ("entity_profile", "graph_neighbors") and found_entity:
                args["entity_id"] = found_entity
            time.sleep(self.pace)
            code, result = self.call(tool, args, CIDS[self.rid % 2])
            served = code == "ok" or code in ACCEPTED_REFUSALS
            self.verdict(served, f"TOOL {tool} {'serves' if code == 'ok' else 'refuses ' + code}")
            if code == "ok" and tool == "search":
                found_item = next((r.get("id") for r in result.get("results", [])), None)
            if code == "ok" and tool == "entity_lookup":
                items = result.get("matches") or []
                found_entity = next(
                    (i.get("entity_id") for i in items if isinstance(i, dict)), None
                )

    def timing(self, rounds: int) -> None:
        for tool, key in (("entity_lookup", "name"), ("search", "query")):
            ms, codes = [], {}
            for i in range(rounds):
                time.sleep(self.pace)
                t = time.perf_counter()
                code, _ = self.call(tool, {key: TIMED_QUERIES[i % len(TIMED_QUERIES)]}, CIDS[i % 2])
                ms.append((time.perf_counter() - t) * 1000)
                codes[code] = codes.get(code, 0) + 1
            print(
                f"TIMING {tool}: n={len(ms)} p50={statistics.median(ms):.0f}ms "
                f"p95={p95(ms):.0f}ms max={max(ms):.0f}ms outcomes={codes}"
            )
            self.verdict(set(codes) <= {"ok"}, f"TIMING {tool} every timed call served")


def _rpc_code(message: dict[str, Any] | None) -> int | None:
    return ((message or {}).get("error") or {}).get("code")


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    cfg = json.loads(sys.stdin.readline())
    if mode == "keyring":
        doc = keyring(bytes.fromhex(cfg["seed_hex"]), cfg["kid"], datetime.now(UTC))
        print(json.dumps(doc))
        return 0
    if mode != "smoke":
        print("usage: public-mcp-smoke.py keyring|smoke < config-json", file=sys.stderr)
        return 2
    return run(Smoke(cfg, http_transport(cfg["url"])), rounds=int(cfg.get("rounds", 30)))


def run(smoke: Smoke, *, rounds: int) -> int:
    try:
        smoke.first_call()
        smoke.transport_and_auth()
        smoke.catalogue()
        smoke.timing(rounds)
    except Unreachable as error:
        smoke.verdict(False, f"REACH the server is unreachable from the probe ({error})")
    print("smoke passed" if smoke.failures == 0 else f"smoke FAILED ({smoke.failures})")
    return 0 if smoke.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
