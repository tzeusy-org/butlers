"""Credential-free installed-CLI feasibility for RFC0036, not a production executor.

Only this module's explicit run_installed entry point executes candidates. Bubblewrap
mounts the exact executable and this stdlib-only supervisor, never the host HOME,
repository, socket, configuration or credentials. The supervisor proves isolation
before version discovery or invocation. The mock admission guard is test equipment;
it is NOT a supported provider interception hook or token/money guarantee.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

CASES = (
    "positive",
    "request_cap",
    "turn_cap",
    "retry",
    "preflight",
    "native_tool",
    "extra_mcp",
    "wrong_ref",
    "oversize_input",
    "oversize_output",
    "partial_replay",
    "persistence",
)
OBLIGATIONS = (
    "egress_isolation",
    "positive_image_chain",
    "request_cap",
    "turn_cap",
    "zero_retry",
    "no_preflight",
    "native_tools_denied",
    "exact_attachment_manifest",
    "input_bound",
    "output_byte_bound",
    "partial_replay_denied",
    "no_persistence",
    "ephemeral_context",
    "hard_output_token_cap",
    "pre_request_budget_reservation",
    "hard_spend_cap",
)
INSTALLED_CASES = tuple((case, "exec_command") for case in CASES if case != "native_tool") + tuple(
    ("native_tool", name) for name in ("shell", "exec_command", "web_search", "apply_patch")
)
REF = {"storage_ref": "s3://synthetic/synthetic-session/synthetic-source.png"}
MAX_BYTES = 131072


class Rejected(Exception):
    """A typed mock boundary denial; never include a payload in its message."""


class Admission:
    """Independently executable fences for the mock provider and attachment server."""

    def __init__(self, request_cap=2, turn_cap=2, allow_repeat_attachment=False):
        self.allow_repeat_attachment = allow_repeat_attachment
        self.request_cap = request_cap
        self.turn_cap = turn_cap
        self.requests = 0
        self.turns = 0
        self.calls = 0
        self.effects = 0
        self.seen = set()
        self.lock = threading.Lock()

    def request(self, key, size, *, path="/responses", retry=False, partial=False):
        if path not in {"/responses", "/chat/completions"}:
            raise Rejected("preflight")
        if retry:
            raise Rejected("retry")
        if partial or key in self.seen:
            raise Rejected("partial_replay")
        if size > MAX_BYTES:
            raise Rejected("oversize_input")
        if self.requests >= self.request_cap:
            raise Rejected("request_cap")
        if self.turns >= self.turn_cap:
            raise Rejected("turn_cap")
        self.seen.add(key)
        self.requests += 1
        self.turns += 1

    def attachment(self, name, arguments):
        if name != "attachment_view":
            raise Rejected("native_tool")
        if arguments != REF:
            raise Rejected("wrong_ref")
        if self.calls and not self.allow_repeat_attachment:
            raise Rejected("extra_mcp")
        self.calls += 1

    def output(self, size):
        if size > MAX_BYTES:
            raise Rejected("oversize_output")


def _png(comparator):
    # Encode random hexadecimal characters as large binary pixel cells. The mock
    # validates delivery, not OCR: this result can never establish actual vision.
    width, height = 256, 32
    raw = bytearray()
    bits = "".join(f"{int(c, 16):04b}" for c in comparator)
    for _ in range(height):
        raw.append(0)
        for x in range(width):
            shade = 0 if bits[x // 4] == "1" else 255
            raw.extend([shade] * 3)

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _mcp():
    """One fixed stdio MCP server, backed by the supervisor's loopback state."""
    port = int(sys.argv[2])
    import urllib.request

    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message.get("method")
        observed = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp_observed", json.dumps({"method": method}).encode()
        )
        with urllib.request.urlopen(observed, timeout=3) as response:
            response.read()
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "attachment", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "attachment_view",
                        "description": "Read the synthetic image.",
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                            "openWorldHint": False,
                        },
                        "inputSchema": {
                            "type": "object",
                            "properties": {"storage_ref": {"type": "string"}},
                            "required": ["storage_ref"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        elif method == "tools/call":
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/attachment",
                json.dumps(message["params"]).encode(),
                {"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    result = json.load(response)
            except Exception:
                result = {"isError": True, "content": [{"type": "text", "text": "denied"}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


class MockProvider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, data, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.denials.append("preflight")
        self._reply(403, b'{"error":"denied"}')

    def do_POST(self):
        state = self.server
        length = int(self.headers.get("Content-Length", "0"))
        if self.path not in {"/attachment", "/mcp_observed"}:
            state.input_sizes.append(length)
            state.attempts += 1
        if length > MAX_BYTES:
            state.denials.append("oversize_input")
            self._reply(413, b'{"error":"denied"}')
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            if self.path == "/mcp_observed":
                state.mcp_methods.append(payload.get("method"))
                self._reply(200, b"{}")
                return
            if self.path == "/attachment":
                state.attachment_attempts += 1
                state.guard.attachment(payload.get("name"), payload.get("arguments"))
                self._reply(
                    200,
                    json.dumps(
                        {
                            "content": (
                                [{"type": "text", "text": "x" * (MAX_BYTES + 1)}]
                                if state.case == "oversize_input"
                                else [
                                    {"type": "image", "mimeType": "image/png", "data": state.image}
                                ]
                            )
                        }
                    ).encode(),
                )
                return
            if state.case == "retry" and state.attempts > 1:
                raise Rejected("retry")
            if state.case == "partial_replay" and state.attempts > 1:
                raise Rejected("partial_replay")
            state.guard.request(
                hashlib.sha256(raw).hexdigest(), length, path=self.path.removeprefix("/v1")
            )
        except (Rejected, ValueError) as exc:
            state.denials.append(str(exc) if isinstance(exc, Rejected) else "invalid_json")
            self._reply(403, b'{"error":"denied"}')
            return
        state.payloads.append(payload)
        if state.case == "retry":
            self._reply(503, b'{"error":"synthetic_unavailable"}')
            return
        if state.case == "preflight":
            self.send_response(307)
            self.send_header("Location", "/preflight")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if state.case == "partial_replay":
            self._reply(
                200,
                b'data: {"type":"response.created","response":{"id":"partial"}}\n\n',
                "text/event-stream",
            )
            return
        tools = payload.get("tools", [])
        names = [t.get("name", t.get("function", {}).get("name", "")) for t in tools]
        name = next((n for n in names if n.endswith("attachment_view")), "attachment_view")
        namespace = None
        for tool in tools:
            for nested in tool.get("tools", []):
                if nested.get("name", "").endswith("attachment_view"):
                    name = nested["name"]
                    namespace = tool.get("name")
        first = state.guard.requests == 1 or state.case in {"extra_mcp", "request_cap", "turn_cap"}
        arguments = {"storage_ref": "s3://foreign/wrong.png"} if state.case == "wrong_ref" else REF
        if state.case == "native_tool":
            name, namespace = state.native_name, None
            for tool in tools:
                for nested in tool.get("tools", []):
                    if nested.get("name") == name:
                        namespace = tool.get("name")
            arguments = {
                "command": "touch /tmp/native-effect",
                "cmd": "touch /tmp/native-effect",
                "input": "*** Begin Patch\n*** Add File: /tmp/native-effect\n+synthetic\n*** End Patch"
                if state.native_name == "apply_patch"
                else "synthetic",
                "search_query": [{"q": "synthetic"}],
            }
            first = state.guard.requests == 1
        if first:
            state.injected_calls += 1
        else:
            # The image must travel through the installed CLI into request two.
            state.image_delivered = state.image in raw.decode()
        if self.path.endswith("/responses"):
            if first:
                item = {
                    "id": "fc_1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
                if namespace:
                    item["namespace"] = namespace
            else:
                item = {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "x" * (MAX_BYTES + 1)
                                if state.case == "oversize_output"
                                else state.comparator
                                if state.image_delivered
                                else "NO_IMAGE"
                            ),
                            "annotations": [],
                        }
                    ],
                }
            response = {
                "id": "resp_" + str(state.guard.requests),
                "object": "response",
                "status": "completed",
                "model": "diagnostic",
                "output": [item],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            }
            events = [
                "data: "
                + json.dumps({"type": "response.created", "response": {**response, "output": []}})
            ]
            events += [
                "data: "
                + json.dumps(
                    {"type": "response.output_item.added", "output_index": 0, "item": item}
                )
            ]
            if first:
                events += [
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.function_call_arguments.delta",
                            "item_id": "fc_1",
                            "output_index": 0,
                            "delta": json.dumps(arguments),
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.function_call_arguments.done",
                            "item_id": "fc_1",
                            "output_index": 0,
                            "arguments": json.dumps(arguments),
                        }
                    ),
                ]
            if not first:
                events += [
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.output_text.delta",
                            "item_id": "msg_1",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": (
                                "x" * (MAX_BYTES + 1)
                                if state.case == "oversize_output"
                                else state.comparator
                                if state.image_delivered
                                else "NO_IMAGE"
                            ),
                        }
                    )
                ]
            events += [
                "data: "
                + json.dumps(
                    {"type": "response.output_item.done", "output_index": 0, "item": item}
                ),
                "data: " + json.dumps({"type": "response.completed", "response": response}),
            ]
            self._reply(200, ("\n\n".join(events) + "\n\n").encode(), "text/event-stream")
        else:
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
                if first
                else {
                    "content": "x" * (MAX_BYTES + 1)
                    if state.case == "oversize_output"
                    else state.comparator
                    if state.image_delivered
                    else "NO_IMAGE"
                }
            )
            chunk = {
                "id": "chat_1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "diagnostic",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": "tool_calls" if first else "stop"}
                ],
            }
            self._reply(
                200,
                ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode(),
                "text/event-stream",
            )


def _proof():
    interfaces = [name for _, name in socket.if_nameindex()]
    if interfaces != ["lo"] or pathlib.Path("/home").exists():
        raise Rejected("isolation_unavailable")
    for family, address in [
        (socket.AF_INET, ("192.0.2.1", 443)),
        (socket.AF_INET6, ("2001:db8::1", 443)),
    ]:
        with socket.socket(family) as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(address) == 0:
                raise Rejected("isolation_unavailable")
    return {"interfaces": interfaces, "external_connect": "unreachable", "host_mount": "absent"}


def _profile(candidate, port, case="positive"):
    root = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))
    home = root / "home"
    home.mkdir()
    (home / "codex").mkdir()
    (root / "work").mkdir()
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "CODEX_HOME": str(home / "codex"),
        "DIAGNOSTIC_FAKE_KEY": "synthetic-not-a-credential",
    }
    mcp = {"command": "/usr/bin/python3", "args": ["/harness.py", "--mcp", str(port)]}
    prompt = "Call attachment_view once with storage_ref s3://synthetic/synthetic-session/synthetic-source.png. Return the image challenge."
    if candidate == "codex":
        config = {
            "approval_policy": "never",
            "model_provider": "mock",
            "model": "diagnostic",
            "history.persistence": "none",
            "model_providers.mock.name": "mock",
            "model_providers.mock.base_url": f"http://127.0.0.1:{port}/v1",
            "model_providers.mock.env_key": "DIAGNOSTIC_FAKE_KEY",
            "model_providers.mock.wire_api": "responses",
            "model_providers.mock.request_max_retries": 0,
            "model_providers.mock.stream_max_retries": 0,
            "features.shell_tool": False,
            "features.unified_exec": False,
            "features.apply_patch_freeform": False,
            "features.apps": False,
            "features.multi_agent": False,
            "features.web_search": False,
            "web_search": "disabled",
            "mcp_servers.attachment.command": mcp["command"],
            "mcp_servers.attachment.args": mcp["args"],
            "mcp_servers.attachment.enabled_tools": ["attachment_view"],
            "mcp_servers.attachment.required": True,
        }
        args = [
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
        ]
        for key, value in config.items():
            # JSON literals are valid TOML for these strings/bools/arrays.
            args += ["-c", key + "=" + json.dumps(value)]
        args += [prompt]
    else:
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "mock/diagnostic",
            "provider": {
                "mock": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "mock",
                    "options": {
                        "baseURL": f"http://127.0.0.1:{port}/v1",
                        "apiKey": "synthetic-not-a-credential",
                    },
                    "models": {
                        "diagnostic": {
                            "name": "diagnostic",
                            "tool_call": True,
                            "attachment": True,
                            "modalities": {"input": ["text", "image"], "output": ["text"]},
                            "limit": {"context": 4096, "output": 128},
                        }
                    },
                }
            },
            "permission": {"*": "deny", "attachment*": "allow", "mcp__attachment*": "allow"},
            "tools": {"*": False, "attachment*": True, "mcp__attachment*": True},
            "mcp": {
                "attachment": {
                    "type": "local",
                    "enabled": True,
                    "command": [mcp["command"], *mcp["args"]],
                }
            },
            "plugin": [],
        }
        path = home / "opencode.json"
        path.write_text(json.dumps(config))
        env["OPENCODE_CONFIG"] = str(path)
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        args = ["run", "--standalone", "--format", "json", "--model", "mock/diagnostic", prompt]
    input_data = None
    if case == "oversize_input":
        input_data = (prompt + "x" * (MAX_BYTES + 1)).encode()
        if candidate == "codex":
            args[-1] = "-"
        else:
            args.pop()  # OpenCode consumes the synthetic stdin prompt.
    return (
        args,
        env,
        hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        input_data,
    )


def _bounded_output(process, input_data=None):
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def drain(stream, buffer):
        while block := stream.read(4096):
            remaining = MAX_BYTES + 1 - len(buffer)
            buffer.extend(block[: max(0, remaining)])
            if len(buffer) > MAX_BYTES:
                exceeded.set()
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break

    threads = [
        threading.Thread(target=drain, args=(stream, buffer), daemon=True)
        for stream, buffer in zip((process.stdout, process.stderr), buffers)
    ]
    for thread in threads:
        thread.start()
    if input_data is not None:

        def feed():
            try:
                process.stdin.write(input_data)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
    _fence_descendants()
    for thread in threads:
        thread.join(timeout=2)
    return bytes(buffers[0]), bytes(buffers[1]), timed_out, exceeded.is_set()


def _fence_descendants():
    # /proc was mounted AFTER unshare-pid. Bubblewrap owns namespace PID1;
    # fence candidate descendants, including children that use setsid.
    own = os.getpid()
    for entry in pathlib.Path("/proc").iterdir():
        if entry.name.isdecimal() and int(entry.name) not in {1, own}:
            try:
                os.kill(int(entry.name), signal.SIGKILL)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            time.sleep(0.01)


def _persisted(comparator, root):
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            previous = b""
            while block := stream.read(65536):
                if comparator in previous + block:
                    return True
                previous = block[-len(comparator) :]
    return False


def _progress(stage, launched):
    print(json.dumps({"stage": stage, "launched": launched}), file=sys.stderr, flush=True)


def _supervise(candidate, case="positive", native_name="exec_command"):
    _progress("proof", False)
    proof = _proof()  # MUST precede even --version, in this same network/mount domain.
    state = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
    state.guard = Admission(
        request_cap=3 if case in {"turn_cap", "extra_mcp"} else 2,
        turn_cap=3 if case == "extra_mcp" else 2,
        # Caps are tested alone; attachment cap is independently exercised elsewhere.
        allow_repeat_attachment=case in {"request_cap", "turn_cap"},
    )
    state.case = case
    state.native_name = native_name
    state.attempts = 0
    state.attachment_attempts = 0
    state.preflight_attempts = 0
    state.input_sizes = []
    state.injected_calls = 0
    state.comparator = secrets.token_hex(8)
    state.image = base64.b64encode(_png(state.comparator)).decode()
    state.image_delivered = False
    state.denials = []
    state.payloads = []
    state.mcp_methods = []
    thread = threading.Thread(target=state.serve_forever, daemon=True)
    thread.start()
    _progress("bootstrap", False)
    args, env, config_digest, input_data = _profile(candidate, state.server_port, case)
    _progress("candidate_launch", None)
    version = (
        subprocess.run(
            ["/candidate", "--version"],
            env=env,
            cwd=str(pathlib.Path(env["HOME"]).parent / "work"),
            capture_output=True,
            timeout=10,
        )
        .stdout.decode()
        .strip()
    )
    _progress("candidate_version", True)
    _progress("candidate_launch", True)
    process = subprocess.Popen(
        ["/candidate", *args],
        env=env,
        cwd=str(pathlib.Path(env["HOME"]).parent / "work"),
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _progress("candidate_execution", True)
    stdout, stderr, timed_out, output_exceeded = _bounded_output(process, input_data)
    _progress("cleanup", True)
    _fence_descendants()
    state.shutdown()
    # Scanner positive companion is independent of candidate persistence evidence.
    planted = pathlib.Path(env["HOME"], "synthetic-canary")
    planted.write_bytes(state.comparator.encode())
    scanner_control = _persisted(state.comparator.encode(), pathlib.Path("/tmp"))
    planted.unlink()
    persisted = any(
        _persisted(pattern, pathlib.Path("/tmp"))
        for pattern in (state.comparator.encode(), state.image.encode(), _png(state.comparator))
    )
    positive = (
        process.returncode == 0
        and not timed_out
        and state.guard.requests == 2
        and state.guard.calls == 1
        and state.image_delivered
        and state.comparator.encode() in stdout
        and not persisted
        and not state.denials
    )
    tool_names = [
        t.get("name", t.get("function", {}).get("name", t.get("type", "")))
        for p in state.payloads
        for t in p.get("tools", [])
    ]
    obligations = dict.fromkeys(OBLIGATIONS, "unsupported")
    obligations.update(
        egress_isolation="supported",
        ephemeral_context="supported",
        positive_image_chain="supported" if positive else "unsupported",
    )
    _progress("receipt", True)
    receipt = {
        "measurement": "unknown" if timed_out else "completed",
        "failure_stage": "candidate_execution" if timed_out else None,
        "candidate": candidate,
        "version": version,
        "config_digest": config_digest,
        "profile_digest": hashlib.sha256(
            json.dumps(
                {
                    "args": args,
                    "env": env,
                    "stdin_digest": hashlib.sha256(input_data).hexdigest()
                    if input_data is not None
                    else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "launched": True,
        "isolation": "supported",
        "namespace_proof": proof,
        "eligibility": "unknown" if timed_out else "unsupported",
        "obligations": obligations,
        "limits": {
            "request_cap": state.guard.request_cap,
            "turn_cap": state.guard.turn_cap,
            "attachment_cap": 2 if state.guard.allow_repeat_attachment else 1,
            "input_bytes": MAX_BYTES,
            "output_bytes_per_stream": MAX_BYTES,
        },
        "capture_digests": {
            "stdout": hashlib.sha256(stdout).hexdigest(),
            "stderr": hashlib.sha256(stderr).hexdigest(),
        },
        "returncode": process.returncode,
        "positive": {
            "disposition": "supported" if positive else "unsupported",
            "provider_requests": state.guard.requests,
            "model_turns": state.guard.turns,
            "attachment_calls": state.guard.calls,
            "image_delivery": state.image_delivered,
            "persisted": persisted,
            "termination": "timeout" if timed_out else "exit",
            "native_tool_count": sum(not n.endswith("attachment_view") for n in tool_names),
        },
        "reason": "bounded_mock_only" if positive else "measured_candidate_limitation",
        "control": case,
        "provider_attempts": state.attempts,
        "attachment_attempts": state.attachment_attempts,
        "denials": sorted(set(state.denials)),
        "output_exceeded": output_exceeded,
        "native_effect": pathlib.Path("/tmp/native-effect").exists(),
        "injected_calls": state.injected_calls,
        "preflight_attempts": state.preflight_attempts,
        "input_exceeded": any(size > MAX_BYTES for size in state.input_sizes),
        "scanner_positive_control": scanner_control,
        "native_variant": native_name if case == "native_tool" else None,
        "artifact_digest": hashlib.sha256(_png(state.comparator)).hexdigest(),
        "cleanup": "complete",
    }
    receipt["control_result"] = _control_result(receipt)
    return receipt


def _control_result(receipt):
    case = receipt["control"]
    denials = set(receipt["denials"])
    expected = case
    exercised = {
        "positive": receipt["positive"]["image_delivery"],
        "request_cap": receipt["provider_attempts"] > 2,
        "turn_cap": receipt["provider_attempts"] > 2,
        "retry": receipt["provider_attempts"] > 0,
        "preflight": "preflight" in denials,
        "native_tool": receipt["injected_calls"] > 0,
        "extra_mcp": receipt["attachment_attempts"] > 1,
        "wrong_ref": receipt["attachment_attempts"] > 0,
        "oversize_input": receipt["input_exceeded"],
        "oversize_output": receipt["output_exceeded"],
        "partial_replay": receipt["provider_attempts"] > 1,
        "persistence": receipt["positive"]["image_delivery"],
    }[case]
    interference = sorted(denials - {expected})
    if case == "positive":
        demonstrated = receipt["positive"]["disposition"] == "supported"
    elif case == "retry":
        demonstrated = exercised and receipt["provider_attempts"] == 1
    elif case == "partial_replay":
        # A truncated response that was never replayed cannot prove replay denial.
        demonstrated = exercised and expected in denials
    elif case == "native_tool":
        # Advertised tools alone and lack of an effect do not establish denial.
        demonstrated = "native_tool" in denials and not receipt["native_effect"]
    elif case == "oversize_output":
        demonstrated = receipt["output_exceeded"]
    elif case == "persistence":
        demonstrated = exercised and not receipt["positive"]["persisted"]
    else:
        demonstrated = exercised and expected in denials
    status = (
        "demonstrated"
        if demonstrated and not interference
        else "violation_observed"
        if exercised
        else "not_exercised"
    )
    return {
        "status": status,
        "exercised": exercised,
        "interference": interference,
        "disposition": "supported" if status == "demonstrated" else "unsupported",
        "scope": "synthetic_transport_only",
        "completion": "complete" if status == "demonstrated" else "incomplete",
        "reason": "independent_observation"
        if status == "demonstrated"
        else "required_chain_not_reached"
        if status == "not_exercised"
        else "candidate_control_not_proven",
    }


def measure_candidate(candidate):
    return [run_installed(candidate, case, native) for case, native in INSTALLED_CASES]


def write_evidence(candidate, receipts):
    """Retain digested sanitized receipts tied to the exact source tree."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD"]).returncode == 0
    envelope = {
        "source_head": head,
        "source_clean": clean,
        "candidate": candidate,
        "measurement": "completed"
        if all(r["measurement"] == "completed" for r in receipts)
        else "incomplete",
        "executor_eligibility": "unsupported",
        "controls_complete": all(
            r.get("control_result", {}).get("completion") == "complete" for r in receipts
        ),
        "receipts": receipts,
    }
    root = pathlib.Path(".tmp/diagnostic-feasibility") / head
    root.mkdir(parents=True, exist_ok=True)
    path = root / (candidate + ".json")
    path.write_text(json.dumps(envelope, sort_keys=True, indent=2) + "\n")
    path.with_suffix(".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "\n")
    return path


def run_installed(candidate, case="positive", native_name="exec_command"):
    """Run only exact installed Codex/OpenCode in a fresh proven Bubblewrap domain."""
    if candidate not in {"codex", "opencode"}:
        raise ValueError("unknown candidate")
    binary = shutil.which(candidate)
    fence = shutil.which("bwrap")
    base = {
        "candidate": candidate,
        "control": case,
        "native_variant": native_name if case == "native_tool" else None,
        "launched": False,
        "measurement": "not_run",
        "isolation": "unmeasured",
        "eligibility": "unmeasured",
        "obligations": dict.fromkeys(OBLIGATIONS, "unmeasured"),
        "cleanup": "complete",
    }
    if not binary or not fence:
        return {
            **base,
            "reason": "candidate_unavailable" if not binary else "isolation_unavailable",
        }
    try:
        resolved = pathlib.Path(binary).resolve(strict=True)
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        harness_digest = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return {
            **base,
            "measurement": "unknown",
            "eligibility": "unknown",
            "failure_stage": "bootstrap",
        }
    command = [
        fence,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        str(resolved),
        "/candidate",
        "--ro-bind",
        str(pathlib.Path(__file__).resolve()),
        "/harness.py",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--chdir",
        "/tmp",
        "--",
        "/usr/bin/python3",
        "/harness.py",
        "--supervise",
        candidate,
        case,
        native_name,
    ]
    # No inherited env values or inherited file descriptors reach the sandbox.
    try:
        result = subprocess.run(
            command, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=40
        )
    except OSError:
        return {
            **base,
            "measurement": "unknown",
            "eligibility": "unknown",
            "failure_stage": "bootstrap",
        }
    except subprocess.TimeoutExpired:
        return {
            **base,
            "measurement": "unknown",
            "eligibility": "unknown",
            "launched": None,
            "failure_stage": "supervisor_timeout",
            "cleanup": "unknown",
        }
    events = []
    for line in result.stderr.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict) and set(event) == {"stage", "launched"}:
                events.append(event)
        except (ValueError, TypeError):
            pass  # Raw sandbox/candidate stderr never leaves this boundary.
    try:
        receipt = json.loads(result.stdout)
        required = {
            "candidate",
            "version",
            "config_digest",
            "launched",
            "isolation",
            "namespace_proof",
            "eligibility",
            "obligations",
            "positive",
            "control_result",
            "cleanup",
        }
        if (
            not isinstance(receipt, dict)
            or not required.issubset(receipt)
            or receipt.get("measurement") not in {"completed", "unknown"}
        ):
            raise ValueError("invalid receipt")
    except (ValueError, TypeError):
        event = events[-1] if events else {"stage": "receipt", "launched": None}
        return {
            **base,
            "measurement": "unknown",
            "eligibility": "unknown",
            "launched": event["launched"],
            "failure_stage": "receipt" if result.stdout else event["stage"],
            "cleanup": "unknown",
        }
    if result.returncode:
        return {
            **base,
            "measurement": "unknown",
            "eligibility": "unknown",
            "launched": receipt.get("launched"),
            "failure_stage": receipt.get("failure_stage", "receipt"),
            "cleanup": "unknown",
        }
    receipt["candidate_digest"] = digest
    receipt["harness_digest"] = harness_digest
    if harness_digest != hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest():
        receipt.update(measurement="unknown", eligibility="unknown", failure_stage="source_changed")
    return receipt


if __name__ == "__main__":
    if sys.argv[1] == "--mcp":
        _mcp()
    elif sys.argv[1] == "--supervise":
        try:
            print(json.dumps(_supervise(sys.argv[2], sys.argv[3], sys.argv[4])))
        except Exception:
            # Progress events identify stage without serializing exception/payload text.
            sys.exit(1)
    elif sys.argv[1] == "--measure":
        complete = True
        for candidate in ("codex", "opencode"):
            receipts = measure_candidate(candidate)
            path = write_evidence(candidate, receipts)
            complete &= all(r["measurement"] == "completed" for r in receipts)
            print(str(path))
        sys.exit(0 if complete else 2)
