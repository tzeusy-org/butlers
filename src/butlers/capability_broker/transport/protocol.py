"""Strict MCP application values, never proof of an owning commit by themselves."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

from butlers.core.capability_json import arguments_digest, canonical_bytes
from butlers.core.goal_stop import CancelIdentity

PROTOCOL = "broker-approvals-v1"
PURPOSE_PROTOCOL = "broker-approvals-v2"
PURPOSE_TOOLS = {
    op: f"broker_approval_authorized_{op.lower()}"
    for op in ("PARK", "SNAPSHOT", "CLAIM", "WITNESS", "EXECUTE")
}
RECEIPT_PROTOCOL = "broker-owning-receipt-v1"
TOOLS = {op: f"broker_approval_{op.lower()}" for op in ("PARK", "LOOKUP", "CANCEL")}
OUTCOMES = {
    "PARK": {"parked", "unknown", "refused"},
    "LOOKUP": {
        "pending",
        "approved",
        "rejected",
        "expired",
        "dispatched",
        "committed",
        "cancelled",
        "fenced_absent",
        "unknown",
        "refused",
    },
    "CANCEL": {"cancelled", "fenced_absent", "unknown", "refused"},
}


class ProtocolError(ValueError):
    """Closed protocol refusal; no raw input or exception payload in messages."""


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ProtocolError("invalid_shape")
    return value


def uuid_text(value):
    if type(value) is not str:
        raise ProtocolError("invalid_uuid")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ProtocolError("invalid_uuid") from None
    if str(parsed) != value:
        raise ProtocolError("noncanonical_uuid")
    return parsed


def identity(value):
    exact(
        value, ("goal_id", "effect_id", "owning_domain", "action_id", "capability", "args_digest")
    )
    try:
        return CancelIdentity(
            uuid_text(value["goal_id"]),
            uuid_text(value["effect_id"]),
            value["owning_domain"],
            uuid_text(value["action_id"]),
            value["capability"],
            value["args_digest"],
        )
    except (ValueError, TypeError):
        raise ProtocolError("invalid_identity") from None


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, val in items:
            if key in result:
                raise ProtocolError("duplicate_key")
            result[key] = val
        return result

    def invalid(_):
        raise ProtocolError("nonfinite_json")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError("invalid_json") from None


def request(value, domain: str):
    if type(value) is dict and value.get("protocol") == PURPOSE_PROTOCOL:
        op = value.get("operation")
        if type(op) is not str or op not in PURPOSE_TOOLS:
            raise ProtocolError("invalid_operation")
        exact(
            value,
            {"protocol", "operation", "request_id", "identity", "handoff"}
            | ({"arguments"} if op == "PARK" else set()),
        )
        target = identity(value["identity"])
        uuid_text(value["request_id"])
        if (
            target.owning_domain != domain
            or type(value["handoff"]) is not str
            or not 1 <= len(value["handoff"]) <= 131072
        ):
            raise ProtocolError("invalid_handoff")
        if op == "PARK" and arguments_digest(value["arguments"]) != target.args_digest:
            raise ProtocolError("argument_binding")
        return value
    if (
        type(value) is not dict
        or type(value.get("operation")) is not str
        or value["operation"] not in TOOLS
    ):
        raise ProtocolError("invalid_operation")
    op = value["operation"]
    keys = {"protocol", "operation", "request_id", "identity"}
    if op == "PARK":
        keys |= {"arguments", "allow_standing_rule"}
    exact(value, keys)
    if value["protocol"] != PROTOCOL or identity(value["identity"]).owning_domain != domain:
        raise ProtocolError("wrong_target")
    uuid_text(value["request_id"])
    if op == "PARK":
        if type(value["arguments"]) is not dict or type(value["allow_standing_rule"]) is not bool:
            raise ProtocolError("invalid_park")
        if arguments_digest(value["arguments"]) != value["identity"]["args_digest"]:
            raise ProtocolError("argument_binding")
    return value


def receipt_digest(value):
    return hashlib.sha256(
        canonical_bytes({k: v for k, v in value.items() if k != "digest"})
    ).hexdigest()


def reply(value, outgoing):
    exact(value, ("protocol", "operation", "request_id", "identity", "outcome", "receipt"))
    if any(value[k] != outgoing[k] for k in ("protocol", "operation", "request_id", "identity")):
        raise ProtocolError("response_binding")
    outcome = value["outcome"]
    if outgoing["protocol"] == PURPOSE_PROTOCOL and outgoing["operation"] != "PARK":
        op = outgoing["operation"]
        if outcome in {"unknown", "refused"}:
            if value["receipt"] is not None:
                raise ProtocolError("unexpected_receipt")
            return value
        r = value["receipt"]
        if op == "SNAPSHOT" and outcome == "approved":
            exact(r, ("version", "call"))
            if type(r["version"]) is not int or r["version"] < 1 or type(r["call"]) is not dict:
                raise ProtocolError("invalid_snapshot")
        elif op in {"CLAIM", "WITNESS"} and outcome == "claimed":
            exact(r, ("intent_id", "snapshot_digest"))
            uuid_text(r["intent_id"])
            if type(r["snapshot_digest"]) is not str or not re.fullmatch(
                r"[0-9a-f]{64}", r["snapshot_digest"]
            ):
                raise ProtocolError("invalid_witness")
        else:
            raise ProtocolError("invalid_outcome")
        return value
    if type(outcome) is not str or outcome not in OUTCOMES[outgoing["operation"]]:
        raise ProtocolError("invalid_outcome")
    r = value["receipt"]
    if outcome in {"unknown", "refused"}:
        if r is not None:
            raise ProtocolError("unexpected_receipt")
        return value
    exact(r, ("protocol", "receipt_id", "identity", "outcome", "approval_ref", "digest"))
    uuid_text(r["receipt_id"])
    identity(r["identity"])
    if (
        r["protocol"] != RECEIPT_PROTOCOL
        or r["identity"] != value["identity"]
        or r["outcome"] != outcome
    ):
        raise ProtocolError("receipt_binding")
    reference = r["approval_ref"]
    if outcome == "fenced_absent":
        if reference is not None:
            raise ProtocolError("invalid_fence_reference")
    elif type(reference) is not str or not 1 <= len(reference) <= 512:
        raise ProtocolError("invalid_approval_reference")
    if (
        type(r["digest"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", r["digest"])
        or receipt_digest(r) != r["digest"]
    ):
        raise ProtocolError("receipt_digest")
    return value


def rpc_request(value, domain):
    """Fixed private MCP profile, before either SDK can coerce/drop fields."""
    if type(value) is not dict or type(value.get("method")) is not str:
        raise ProtocolError("rpc_shape")
    method = value["method"]
    if method not in {"initialize", "tools/list", "tools/call", "notifications/initialized"}:
        raise ProtocolError("private_rpc_method")
    notification = method == "notifications/initialized"
    keys = {"jsonrpc", "method"} | (set() if notification else {"id"})
    if "params" in value:
        keys.add("params")
    exact(value, keys)
    if value["jsonrpc"] != "2.0":
        raise ProtocolError("rpc_shape")
    if not notification and type(value["id"]) not in {str, int}:
        raise ProtocolError("rpc_id")
    params = value.get("params", {})
    if method == "tools/call":
        exact(params, ("name", "arguments"))
        app = request(params["arguments"], domain)
        if params["name"] != tool_name(app):
            raise ProtocolError("tool_binding")
    elif method == "initialize":
        exact(params, ("protocolVersion", "capabilities", "clientInfo"))
        if (
            type(params["protocolVersion"]) is not str
            or type(params["capabilities"]) is not dict
            or type(params["clientInfo"]) is not dict
        ):
            raise ProtocolError("initialize_shape")
    else:
        exact(params, ())
    return value


def rpc_result(raw, outgoing_rpc, domain):
    """Check raw responses before MCP/Pydantic can discard duplicate/unknown fields."""
    v = strict_json(raw)
    exact(v, ("jsonrpc", "id", "result"))
    if (
        v["jsonrpc"] != "2.0"
        or type(v["id"]) is not type(outgoing_rpc["id"])
        or v["id"] != outgoing_rpc["id"]
    ):
        raise ProtocolError("rpc_binding")
    method = outgoing_rpc["method"]
    if method == "tools/call":
        params = exact(outgoing_rpc["params"], ("name", "arguments"))
        req = request(params["arguments"], domain)
        if params["name"] != tool_name(req):
            raise ProtocolError("tool_binding")
        result = exact(v["result"], ("content", "structuredContent", "isError"))
        if result["isError"] is not False:
            raise ProtocolError("mcp_error")
        content = result["content"]
        if type(content) is not list or len(content) != 1:
            raise ProtocolError("ambiguous_content")
        text = exact(content[0], ("type", "text"))
        app = reply(result["structuredContent"], req)
        if text["type"] != "text" or text["text"] != canonical_bytes(app).decode():
            raise ProtocolError("conflicting_content")
    elif method == "initialize":
        exact(v["result"], ("protocolVersion", "capabilities", "serverInfo"))
        if v["result"]["protocolVersion"] != outgoing_rpc["params"]["protocolVersion"]:
            raise ProtocolError("protocol_version")
    elif method == "tools/list":
        result = exact(v["result"], ("tools",))
        if (
            type(result["tools"]) is not list
            or len(result["tools"]) not in {len(TOOLS), len(TOOLS) + len(PURPOSE_TOOLS)}
            or any(type(t) is not dict or type(t.get("name")) is not str for t in result["tools"])
            or {t["name"] for t in result["tools"]}
            not in (set(TOOLS.values()), set(TOOLS.values()) | set(PURPOSE_TOOLS.values()))
        ):
            raise ProtocolError("tool_catalog")
    else:
        raise ProtocolError("unexpected_rpc")
    return v


def tool_name(value):
    tools = PURPOSE_TOOLS if value["protocol"] == PURPOSE_PROTOCOL else TOOLS
    return tools[value["operation"]]
