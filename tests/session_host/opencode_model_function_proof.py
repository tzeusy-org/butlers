"""Explicit native D18 inventory probe against a synthetic local OpenAI provider.

Not collected by pytest and never selects a PATH binary or the user's auth. Pass
the reviewed binary and its expected SHA256 explicitly. This is adapter evidence,
not a session-host image, namespace, egress, credential or eligibility proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from butlers.session_host.model_function import OpenCodeModelFunctionSettings, structured_result

MODEL = "openai/gpt-4.1"
ANSWER = '{"verdict":"IGNORE"}'


def probe(binary: Path, expected_sha256: str) -> dict:
    """Run deny/control/deny with independent homes and inspect actual provider requests."""
    if (
        not binary.is_absolute()
        or hashlib.sha256(binary.read_bytes()).hexdigest() != expected_sha256
    ):
        raise ValueError("native_binary_identity_mismatch")
    observations = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(length))
            observations.append(body)
            events = [
                {"type": "response.created", "response": {"id": "resp_synthetic"}},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "message",
                        "id": "msg_synthetic",
                        "role": "assistant",
                        "content": [],
                    },
                },
                {
                    "type": "response.content_part.added",
                    "item_id": "msg_synthetic",
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_synthetic",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": ANSWER,
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_synthetic",
                        "status": "completed",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    },
                },
            ]
            data = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    counts = []
    try:
        for relaxed in (False, True, False):
            with tempfile.TemporaryDirectory(prefix="d18-opencode-") as directory:
                root = Path(directory)
                credential = root / "model-credential"
                credential.write_bytes(b"SYNTHETIC-NOT-A-REAL-API-KEY")
                credential.chmod(0o600)
                settings = OpenCodeModelFunctionSettings(MODEL)
                config = json.loads(settings.render(credential.read_bytes()))
                # Proof-only changes: the credential pointer is under the disposable
                # root and only the fake provider is contacted. No real key is used.
                config["provider"]["openai"]["options"] = {
                    "apiKey": "{file:" + str(credential) + "}",
                    "baseURL": f"http://127.0.0.1:{server.server_port}/v1",
                }
                if relaxed:
                    config.pop("tools")
                    config["permission"] = {"*": "allow"}
                    config["agent"]["model_function"].pop("tools")
                    config["agent"]["model_function"]["permission"] = {"*": "allow"}
                config_path = root / "opencode.jsonc"
                config_path.write_text(json.dumps(config))
                env = {
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(root),
                    "XDG_CONFIG_HOME": str(root / "config"),
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                    "XDG_STATE_HOME": str(root / "state"),
                    "OPENCODE_CONFIG": str(config_path),
                    "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                    "OPENCODE_DISABLE_MODELS_FETCH": "true",
                    "OPENCODE_DISABLE_AUTOUPDATE": "true",
                }
                version = subprocess.run(
                    [str(binary), "--version"],
                    env=env,
                    cwd=root,
                    capture_output=True,
                    timeout=10,
                )
                if version.returncode != 0 or version.stdout.strip() != b"1.17.7":
                    raise ValueError("native_binary_version_mismatch")
                start = len(observations)
                result = subprocess.run(
                    [
                        str(binary),
                        *settings.command[1:],
                        "Reply with a JSON object containing verdict IGNORE.",
                    ],
                    env=env,
                    cwd=root,
                    capture_output=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    raise ValueError("native_model_function_failed")
                if structured_result(result.stdout) != {"verdict": "IGNORE"}:
                    raise ValueError("native_model_function_answer_mismatch")
                calls = observations[start:]
                if len(calls) != 1 or calls[0].get("model") != "gpt-4.1":
                    raise ValueError("native_single_model_call_not_observed")
                if any(type(call.get("tools", [])) is not list for call in calls):
                    raise ValueError("native_tool_inventory_invalid")
                tool_counts = [len(call.get("tools", [])) for call in calls]
                if relaxed:
                    names = {tool.get("name") for tool in calls[0].get("tools", [])}
                    if not {"bash", "read"} <= names:
                        raise ValueError("native_positive_control_failed")
                elif any(tool_counts):
                    raise ValueError("native_tool_denial_failed")
                counts.append(tool_counts)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return {
        "binary_sha256": expected_sha256,
        "sequence": ["deny", "control", "deny"],
        "tool_counts": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.binary, args.sha256), sort_keys=True))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        # Runtime stdout/stderr may contain data; never print either on refusal.
        raise SystemExit("native_model_function_proof_refused") from None


if __name__ == "__main__":
    main()
