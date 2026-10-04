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
REF = {"storage_ref": "s3://synthetic/synthetic-session/synthetic-source.png"}
MAX_BYTES = 131072


class Rejected(Exception):
    """A typed mock boundary denial; never include a payload in its message."""


class Admission:
    """Independently executable fences for the mock provider and attachment server."""

    def __init__(self, request_cap=2, turn_cap=2):
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
        if self.calls:
            raise Rejected("extra_mcp")
        self.calls += 1

    def output(self, size):
        if size > MAX_BYTES:
            raise Rejected("oversize_output")


def exercise_control(case):
    """Plant each violation alone; success requires the exact image exchange shape."""
    guard = Admission(turn_cap=1 if case == "turn_cap" else 2)
    reason = case
    try:
        if case == "preflight":
            guard.request("first", 1, path="/models")
        elif case == "retry":
            guard.request("first", 1, retry=True)
        elif case == "oversize_input":
            guard.request("first", MAX_BYTES + 1)
        elif case == "partial_replay":
            guard.request("first", 1)
            guard.request("first", 1, partial=True)
        else:
            guard.request("first", 1)
            guard.attachment(
                "shell" if case == "native_tool" else "attachment_view",
                {**REF, "source": "foreign"} if case == "wrong_ref" else REF,
            )
            if case == "extra_mcp":
                guard.attachment("attachment_view", REF)
            guard.request("second", 1)
            if case == "request_cap":
                guard.request("third", 1)
            guard.output(MAX_BYTES + 1 if case == "oversize_output" else 1)
            if case == "persistence":
                raise Rejected("persistence")
            guard.effects = 1
    except Rejected as exc:
        assert str(exc) == case
        disposition = "unsupported"
    else:
        assert case == "positive"
        disposition = "supported"
    return {
        "disposition": disposition,
        "reason": reason,
        "provider_requests": guard.requests,
        "model_turns": guard.turns,
        "attachment_calls": guard.calls,
        "effects": guard.effects,
    }


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
            state.attempts += 1
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
        first = state.guard.requests == 1 or state.case == "extra_mcp"
        arguments = {"storage_ref": "s3://foreign/wrong.png"} if state.case == "wrong_ref" else REF
        if state.case == "native_tool":
            name, namespace = state.native_name, None
            arguments = {
                "command": "touch /tmp/native-effect",
                "cmd": "touch /tmp/native-effect",
                "input": "synthetic",
            }
            first = True
        if not first:
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
                else {"content": state.comparator if state.image_delivered else "NO_IMAGE"}
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
    if interfaces != ["lo"] or pathlib.Path("/home/orca").exists():
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


def _profile(candidate, port):
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
    return args, env, hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _bounded_output(process):
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
    # /proc was mounted AFTER unshare-pid. The supervisor is namespace PID1;
    # every other process is disposable, including descendants that use setsid.
    own = os.getpid()
    for entry in pathlib.Path("/proc").iterdir():
        if entry.name.isdecimal() and int(entry.name) != own:
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


def _supervise(candidate, case="positive", native_name="exec_command"):
    proof = _proof()  # MUST precede even --version, in this same network/mount domain.
    state = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
    state.guard = Admission(
        request_cap=1 if case == "request_cap" else 2, turn_cap=1 if case == "turn_cap" else 2
    )
    state.case = case
    state.native_name = native_name
    state.attempts = 0
    state.attachment_attempts = 0
    state.comparator = secrets.token_hex(8)
    state.image = base64.b64encode(_png(state.comparator)).decode()
    state.image_delivered = False
    state.denials = []
    state.payloads = []
    state.mcp_methods = []
    thread = threading.Thread(target=state.serve_forever, daemon=True)
    thread.start()
    args, env, config_digest = _profile(candidate, state.server_port)
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
    process = subprocess.Popen(
        ["/candidate", *args],
        env=env,
        cwd=str(pathlib.Path(env["HOME"]).parent / "work"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr, timed_out, output_exceeded = _bounded_output(process)
    _fence_descendants()
    state.shutdown()
    if case == "persistence":
        # Positive companion for the scanner: a planted challenge must be found.
        pathlib.Path(env["HOME"], "synthetic-canary").write_bytes(state.comparator.encode())
    persisted = _persisted(state.comparator.encode(), pathlib.Path("/tmp"))
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
    return {
        "candidate": candidate,
        "version": version,
        "config_digest": config_digest,
        "launched": True,
        "isolation": "supported",
        "namespace_proof": proof,
        "eligibility": "unsupported",
        "obligations": obligations,
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
        "reason": "bounded_mock_only" if positive else "positive_chain_unavailable",
        "control": case,
        "provider_attempts": state.attempts,
        "attachment_attempts": state.attachment_attempts,
        "denials": sorted(set(state.denials)),
        "output_exceeded": output_exceeded,
        "native_effect": pathlib.Path("/tmp/native-effect").exists(),
        "mock_controls": [exercise_control(control) for control in CASES],
        "cleanup": "complete",
    }


def run_installed(candidate, case="positive", native_name="exec_command"):
    """Run only exact installed Codex/OpenCode in a fresh proven Bubblewrap domain."""
    if candidate not in {"codex", "opencode"}:
        raise ValueError("unknown candidate")
    binary = shutil.which(candidate)
    fence = shutil.which("bwrap")
    base = {
        "candidate": candidate,
        "launched": False,
        "isolation": "unsupported",
        "eligibility": "unsupported",
        "obligations": dict.fromkeys(OBLIGATIONS, "unsupported"),
        "cleanup": "complete",
    }
    if not binary or not fence:
        return {
            **base,
            "reason": "candidate_unavailable" if not binary else "isolation_unavailable",
        }
    resolved = pathlib.Path(binary).resolve(strict=True)
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
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
    result = subprocess.run(command, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=40)
    if result.returncode:
        return {**base, "reason": "isolation_unavailable"}
    receipt = json.loads(result.stdout)
    receipt["candidate_digest"] = digest
    receipt["harness_digest"] = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    return receipt


if __name__ == "__main__":
    if sys.argv[1] == "--mcp":
        _mcp()
    elif sys.argv[1] == "--supervise":
        print(json.dumps(_supervise(sys.argv[2], sys.argv[3], sys.argv[4])))
    elif sys.argv[1] == "--measure":
        print(
            json.dumps([run_installed(candidate) for candidate in ("codex", "opencode")], indent=2)
        )
