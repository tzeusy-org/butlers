"""Codex adapter proof for RFC 0038 D3. No tuple becomes eligible here.

Software tests run everywhere. The native ones need the pinned CLI, named by
``BUTLERS_CODEX_PROOF_BINARY``; without it they skip, and a skip is no evidence.
They run the real binary against loopback fakes in an empty network namespace,
with no credentials and no provider. See docs/runtime/session-host.md.
"""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from butlers.session_host.bubblewrap import BubblewrapDomain, ImageFile
from butlers.session_host.codex import (
    AUTH_NAME,
    CATALOG_NAME,
    CONFIG_NAME,
    NATIVE_FILES,
    CodexSettings,
)
from butlers.session_host.contracts import Refused
from butlers.session_host.material import InvocationMaterial
from butlers.session_host.profiles import Profile, ProfileFactory, ReviewedProfile
from tests.session_host import codex_proof, exact_image_codex_proof
from tests.session_host.test_core import request

REPO = Path(__file__).resolve().parents[2]
PROVEN_SETTINGS_SHA256 = "58abe0e9d1963a7d59590485d9aa0d61318421246fe9726e23647af7f1c6f437"
BYPASS_FLAGS = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-skip-permissions",
    "bypassPermissions",
    "--sandbox=false",
)


def image(tmp_path):
    entries = []
    for name in ("init", "codex"):
        source = tmp_path / name
        source.write_bytes(b"synthetic-image-input-" + name.encode())
        source.chmod(0o555)
        entries.append(
            ImageFile(source, "/runtime/" + name, hashlib.sha256(source.read_bytes()).hexdigest())
        )
    return tuple(entries)


def broker_namespace(name="mcp__broker"):
    return {"type": "namespace", "name": name, "tools": [{"type": "function", "name": "goal_ping"}]}


def search_tool(sources):
    """The pinned CLI's client tool_search entry, with its declared sources."""
    lines = "".join(f"- {source}\n" for source in sources)
    return {
        "type": "tool_search",
        "execution": "client",
        "description": "# Tool discovery\n\nSearches over deferred tool metadata with BM25.\n\n"
        f"You have access to tools from the following sources:\n{lines}"
        "Some of the tools may not have been provided to you upfront.",
    }


def search_output(tools, **fields):
    """The pinned CLI's answer to a client-executed ``tool_search_call``."""
    return {
        "type": "tool_search_output",
        "call_id": codex_proof.CALL_IDS["tool_search"],
        "status": "completed",
        "execution": "client",
        "tools": tools,
        **fields,
    }


def clean_observation():
    """What a correctly restricted run looks like; every field is checker input."""
    outputs = []
    for call in codex_proof.HOSTILE_CALLS:
        name = (call.get("namespace", "") + "." + call["name"]).lstrip(".")
        if name == "mcp__broker.goal_ping":
            output = '[{"type":"input_text","text":"pong"}]'
        elif name == "read_mcp_resource":
            output = "resources/read failed: unknown MCP server 'decoy'"
        elif name == "apply_patch":
            output = "unsupported custom tool call: apply_patch"
        elif name == "mcp__decoy.goal_ping":
            output = "unsupported call: mcp__decoygoal_ping"
        elif name == "tool_search":
            outputs.append(search_output([broker_namespace()]))
            continue
        else:
            output = f"unsupported call: {name}"
        outputs.append(
            {
                "type": "function_call_output",
                "call_id": codex_proof.CALL_IDS[name],
                "output": output,
            }
        )
    tools = [
        {"type": "function", "name": "list_mcp_resources"},
        {"type": "function", "name": "list_mcp_resource_templates"},
        {"type": "function", "name": "read_mcp_resource"},
        search_tool(["broker"]),
        broker_namespace(),
    ]
    bearer = f"Bearer {codex_proof.SYNTHETIC_TOKEN}"
    return {
        "exit": "completed",
        "argv": ["/usr/bin/bwrap", *CodexSettings(codex_proof.MODEL).command],
        "bodies": [{"tools": tools, "input": []}, {"tools": tools, "input": outputs}],
        "provider_auth": [f"Bearer {codex_proof.SYNTHETIC_KEY}"],
        "broker": [{"method": "tools/call", "name": "goal_ping", "auth": bearer}],
        "decoy": [],
        "unexpected_paths": [],
        "events": [{"type": "item.completed", "item_type": "mcp_tool_call", "status": "completed"}],
        "isolation": {
            "measured": True,
            "mount_count": 19,
            "unplanned_mounts": [],
            "missing_paths": [],
            "unexpected_paths": [],
            "canary_seen": [],
            "peer_path_visible": False,
            "process_count": 1,
            "processes": ["/runtime/codex"],
            "foreign_processes": [],
            "peer_process_visible": False,
        },
    }


def failed(observation):
    return {check for check, reasons in codex_proof.evaluate(observation).items() if reasons}


def test_checker_passes_only_the_clean_observation():
    assert failed(clean_observation()) == set()


def test_no_evidence_fails_every_check():
    assert failed({}) == set(codex_proof.CHECKED)
    assert failed({"exit": "completed", "bodies": []}) == set(codex_proof.CHECKED)


def test_a_run_with_no_second_request_fails_every_check():
    # Hostile calls were never answered, so nothing was refused or allowed.
    observation = clean_observation()
    observation["bodies"] = observation["bodies"][:1]
    assert failed(observation) == set(codex_proof.CHECKED)


def test_settings_identity_is_the_exact_one_the_native_proof_ran_against():
    # CI has no pinned binary, so this is what holds the proof to these bytes.
    # Changing any setting needs a fresh native run, then this hash.
    identity = CodexSettings(codex_proof.MODEL).identity
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    assert digest == PROVEN_SETTINGS_SHA256


def test_empty_top_level_tools_cannot_hide_the_real_list():
    # The real Responses-lite shape from the pinned CLI on gpt-6-sol's bundled
    # catalog: ``tools`` absent, with a shell-bearing list inside ``input``.
    body = {
        "tools": [],
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [{"type": "function", "name": "exec_command"}],
                    }
                ],
            }
        ],
    }
    assert codex_proof.tool_surface(body) == {"function:exec_command"}
    observation = clean_observation()
    observation["bodies"][0] = body
    assert "builtin_shell_disabled" in failed(observation)


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ({"type": "web_search"}, "provider_search_disabled"),
        ({"type": "web_fetch"}, "provider_fetch_disabled"),
        ({"type": "mcp", "server_url": "https://x.test"}, "provider_remote_mcp_disabled"),
        ({"type": "code_interpreter"}, "provider_code_disabled"),
        ({"type": "custom", "name": "apply_patch"}, "builtin_filesystem_disabled"),
        ({"type": "function", "name": "exec_command"}, "builtin_shell_disabled"),
        ({"type": "function", "name": "web_fetch"}, "builtin_web_disabled"),
        ({"type": "function", "name": "brand_new_tool"}, "broker_only_mcp"),
        ("not-a-tool", "broker_only_mcp"),
        (
            {
                "type": "namespace",
                "name": "mcp__decoy",
                "tools": [{"type": "function", "name": "goal_ping"}],
            },
            "broker_only_mcp",
        ),
    ],
)
def test_any_extra_tool_fails_the_matching_check(tool, expected):
    observation = clean_observation()
    observation["bodies"][1]["tools"] = [*observation["bodies"][1]["tools"], tool]
    assert expected in failed(observation)


# Native deferred discovery (RFC 0027) is the runtime searching the MCP tools it
# already listed, on the client. Provider-hosted search is a different thing.
@pytest.mark.parametrize(
    "tool",
    [
        {"type": "tool_search"},
        {"type": "tool_search", "execution": "server"},
        {"type": "tool_search", "execution": "Client"},
    ],
)
def test_a_tool_search_not_executed_on_the_client_is_provider_search(tool):
    observation = clean_observation()
    for body in observation["bodies"]:
        body["tools"] = [
            tool if entry.get("type") == "tool_search" else entry for entry in body["tools"]
        ]
    assert codex_proof.tool_surface({"tools": [tool]}) == {"hosted:tool_search"}
    assert "provider_search_disabled" in failed(observation)


def test_client_tool_search_is_its_own_surface_entry():
    tool = {"type": "tool_search", "execution": "client"}
    assert codex_proof.tool_surface({"tools": [tool]}) == {"client:tool_search"}


def _with_search_result(observation, output):
    body = observation["bodies"][1]
    body["input"] = [
        output if item.get("type") == "tool_search_output" else item for item in body["input"]
    ]
    return observation


@pytest.mark.parametrize(
    ("output", "expected", "reason"),
    [
        # Discovery surfaced a second server's tools: it reached past the broker.
        (
            search_output([broker_namespace(), broker_namespace("mcp__decoy")]),
            {"broker_only_mcp", "local_mcp_disabled"},
            "tool_search returned",
        ),
        (
            search_output([broker_namespace("mcp__decoy")]),
            {"broker_only_mcp", "local_mcp_disabled"},
            "tool_search returned",
        ),
        # A bare function outside any MCP namespace is not broker discovery.
        (
            search_output([{"type": "function", "name": "exec_command"}]),
            {"broker_only_mcp", "builtin_shell_disabled"},
            "tool_search returned",
        ),
        # The positive companion: discovery that finds nothing proves nothing.
        (search_output([]), {"broker_only_mcp"}, "did not reach the broker corpus"),
        (
            search_output([broker_namespace()], execution="server"),
            {"broker_only_mcp", "provider_search_disabled"},
            "not client-executed",
        ),
        (
            search_output([broker_namespace()], status="failed"),
            {"broker_only_mcp"},
            "did not complete",
        ),
    ],
)
def test_tool_search_must_reach_the_broker_and_only_the_broker(output, expected, reason):
    observation = _with_search_result(clean_observation(), output)
    assert expected <= failed(observation)
    reasons = codex_proof.evaluate(observation)["broker_only_mcp"]
    assert any(reason in item for item in reasons), reasons


@pytest.mark.parametrize(
    ("sources", "also"),
    [
        # A built-in family deferred behind search rather than offered up front.
        (["Multi-agent tools: Spawn and manage sub-agents.", "broker"], set()),
        # A second MCP server in the deferred corpus.
        (["decoy", "broker"], {"local_mcp_disabled"}),
        ([], set()),
        (None, set()),
    ],
)
def test_tool_search_sources_must_be_the_broker_alone(sources, also):
    observation = clean_observation()
    tool = search_tool(sources or [])
    if sources is None:
        tool["description"] = "Tool discovery without a sources list."
    for body in observation["bodies"]:
        body["tools"] = [
            tool if entry.get("type") == "tool_search" else entry for entry in body["tools"]
        ]
    assert {"broker_only_mcp"} | also <= failed(observation)
    reasons = codex_proof.evaluate(observation)["broker_only_mcp"]
    assert any("not the broker alone" in item for item in reasons), reasons


def test_search_sources_reads_the_pinned_cli_description():
    tool = search_tool(["Multi-agent tools: Spawn and manage sub-agents.", "broker"])
    assert codex_proof.search_sources(tool) == [
        "Multi-agent tools: Spawn and manage sub-agents.",
        "broker",
    ]


def test_a_missing_tool_search_answer_fails():
    observation = clean_observation()
    observation["bodies"][1]["input"] = [
        item for item in observation["bodies"][1]["input"] if item["type"] != "tool_search_output"
    ]
    reasons = codex_proof.evaluate(observation)["broker_only_mcp"]
    assert any("did not reach the broker corpus" in item for item in reasons), reasons


def test_dropping_native_discovery_fails_the_surface():
    # The closed preset keeps supports_search_tool (bu-r980fk AC3 needs it for
    # leads), so the proof pins it: a runtime without it has a different surface.
    observation = clean_observation()
    for body in observation["bodies"]:
        body["tools"] = [entry for entry in body["tools"] if entry.get("type") != "tool_search"]
    reasons = codex_proof.evaluate(observation)["broker_only_mcp"]
    assert any("missing=['client:tool_search']" in item for item in reasons), reasons


# Each row names the reason its own rule emits. Several rules can fail the same
# check, so asserting only the check would let a removed rule hide behind another.
@pytest.mark.parametrize(
    ("change", "expected", "reason"),
    [
        ("exit", "broker_only_mcp", "runtime did not complete"),
        ("no-argv", "broker_only_mcp", "no executed argv"),
        ("bypass-argv", "builtin_shell_disabled", "bypass argument executed"),
        ("wrong-provider-auth", "broker_only_mcp", "staged credential"),
        ("decoy", "local_mcp_disabled", "decoy contacted"),
        ("unexpected-path", "broker_only_mcp", "other paths"),
        ("broker-call-missing", "broker_only_mcp", "tools/call never arrived"),
        ("broker-bearer", "broker_only_mcp", "without the staged bearer"),
        ("command-event", "builtin_shell_disabled", "command_execution event"),
        ("file-event", "builtin_filesystem_disabled", "file_change event"),
        ("search-event", "builtin_web_disabled", "web_search event"),
        ("shell-ran", "builtin_shell_disabled", "exec_command output="),
        ("patch-ran", "builtin_filesystem_disabled", "apply_patch output="),
        ("view-ran", "builtin_filesystem_disabled", "view_image output="),
        ("search-ran", "builtin_web_disabled", "web_search output="),
        ("decoy-read", "local_mcp_disabled", "decoy resource read"),
        ("pong-missing", "broker_only_mcp", "did not return to the provider"),
        ("broker-tool-missing", "broker_only_mcp", "missing=['function:mcp__broker.goal_ping']"),
    ],
)
def test_each_observed_violation_fails(change, expected, reason):
    observation = clean_observation()
    outputs = {item["call_id"]: item for item in observation["bodies"][1]["input"]}
    if change == "exit":
        observation["exit"] = "Refused:runtime_failed"
    elif change == "no-argv":
        del observation["argv"]
    elif change == "bypass-argv":
        observation["argv"].append("--dangerously-bypass-approvals-and-sandbox")
    elif change == "wrong-provider-auth":
        observation["provider_auth"] = ["Bearer other"]
    elif change == "decoy":
        observation["decoy"] = [{"method": "initialize"}]
    elif change == "unexpected-path":
        observation["unexpected_paths"] = ["GET /telemetry"]
    elif change == "broker-call-missing":
        observation["broker"] = []
    elif change == "broker-bearer":
        observation["broker"][0]["auth"] = None
    elif change == "command-event":
        observation["events"].append({"item_type": "command_execution"})
    elif change == "file-event":
        observation["events"].append({"item_type": "file_change"})
    elif change == "shell-ran":
        outputs[codex_proof.CALL_IDS["exec_command"]]["output"] = "Process exited with code 0"
    elif change == "patch-ran":
        outputs[codex_proof.CALL_IDS["apply_patch"]]["output"] = "Success."
    elif change == "search-ran":
        outputs[codex_proof.CALL_IDS["web_search"]]["output"] = "results"
    elif change == "search-event":
        observation["events"].append({"item_type": "web_search"})
    elif change == "view-ran":
        outputs[codex_proof.CALL_IDS["view_image"]]["output"] = "image bytes"
    elif change == "decoy-read":
        outputs[codex_proof.CALL_IDS["read_mcp_resource"]]["output"] = "decoy contents"
    elif change == "pong-missing":
        outputs[codex_proof.CALL_IDS["mcp__broker.goal_ping"]]["output"] = "requires approval"
    elif change == "broker-tool-missing":
        # Offered nowhere: neither up front nor through discovery.
        for body in observation["bodies"]:
            body["tools"] = [tool for tool in body["tools"] if tool["type"] != "namespace"]
            for item in codex_proof.search_results(body):
                item["tools"] = []
    reasons = codex_proof.evaluate(observation)[expected]
    assert any(reason in item for item in reasons), reasons


@pytest.mark.parametrize(
    ("isolation", "expected", "reason"),
    [
        (None, {"filesystem_isolation", "process_isolation"}, "not measured"),
        (
            {"measured": False, "error": "OSError()"},
            {"filesystem_isolation", "process_isolation"},
            "not measured (OSError())",
        ),
        ({"unexpected_paths": ["/etc"]}, {"filesystem_isolation"}, "outside its plan"),
        (
            {"unplanned_mounts": ["/tmp/etc (ext4 /etc)"]},
            {"filesystem_isolation"},
            "mounts outside the plan",
        ),
        ({"mount_count": 0}, {"filesystem_isolation"}, "mounts were not read"),
        ({"missing_paths": ["/runtime/codex"]}, {"filesystem_isolation"}, "not this domain's"),
        ({"canary_seen": ["/tmp/peer/x"]}, {"filesystem_isolation"}, "material is readable"),
        ({"peer_path_visible": True}, {"filesystem_isolation"}, "stage directory is visible"),
        ({"foreign_processes": ["-zsh"]}, {"process_isolation"}, "processes outside it"),
        ({"peer_process_visible": True}, {"process_isolation"}, "host process outside"),
        ({"processes": []}, {"process_isolation"}, "process table was empty"),
    ],
)
def test_isolation_findings_fail_the_matching_check(isolation, expected, reason):
    observation = clean_observation()
    if isolation is None:
        del observation["isolation"]
    elif "measured" in isolation:
        observation["isolation"] = isolation
    else:
        observation["isolation"].update(isolation)
    failures = codex_proof.evaluate(observation)
    assert {check for check, reasons in failures.items() if reasons} == expected
    assert all(any(reason in r for r in failures[check]) for check in expected), failures


@pytest.mark.parametrize("key", ["unplanned_mounts", "missing_paths"])
def test_an_isolation_view_without_the_mount_or_path_check_fails(key):
    observation = clean_observation()
    del observation["isolation"][key]
    assert failed(observation) == {"filesystem_isolation"}


_PLAN = [
    "/usr/bin/bwrap",
    "--unshare-pid",
    "--tmpfs",
    "/",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
    "--dir",
    "/invocation",
    "--setenv",
    "PATH",
    "/runtime",
    "--perms",
    "0400",
    "--ro-bind-data",
    "5",
    "/invocation/prompt",
    "--info-fd",
    "7",
    "--",
    "/runtime/init",
    "--tmpfs",
    "/after-the-separator",
]


_HOST_SHM, _HOST_DEV, _HOST_DISK = "0:26", "0:5", "259:2"
# The observer's own mount table: the devices that are the host's.
_HOST = frozenset({_HOST_SHM, _HOST_DEV, _HOST_DISK})
_fresh = iter(range(100, 1000))


def mount(point, root="/", fstype="tmpfs", *, device=None, options="rw,nosuid"):
    return {
        "device": device or f"0:{next(_fresh)}",
        "mountpoint": point,
        "root": root,
        "options": options,
        "fstype": fstype,
        "source": "x",
    }


def planned_view():
    return [
        mount("/"),
        mount("/proc", fstype="proc"),
        mount("/dev"),
        # The planned node binds are the host's own device nodes.
        mount("/dev/null", "/null", "devtmpfs", device=_HOST_DEV),
        # In a container, /dev is itself a tmpfs.
        mount("/dev/tty", "/tty", "tmpfs", device=_HOST_DEV),
        mount("/dev/pts", fstype="devpts"),
        mount("/tmp"),
        mount("/invocation/prompt", "/bindfileuSe1Iw//deleted", options="ro,nosuid"),
    ]


def swap(point, **change):
    def apply(view):
        return [{**m, **change} if m["mountpoint"] == point else m for m in view]

    return apply


def test_the_plan_mounts_are_read_up_to_the_separator():
    assert codex_proof.planned_mounts(_PLAN) == {
        "/": "tmpfs",
        "/proc": "proc",
        "/dev": "dev",
        "/tmp": "tmpfs",
        "/invocation/prompt": "data",
    }


def test_a_plan_option_the_reader_does_not_know_refuses():
    with pytest.raises(ValueError, match="unknown bwrap option"):
        codex_proof.planned_mounts(["bwrap", "--overlay-src", "/x", "--"])


def test_exactly_the_planned_mounts_pass():
    planned = codex_proof.planned_mounts(_PLAN)
    assert codex_proof.unplanned_mounts(planned_view(), planned, _HOST, _HOST_DEV) == []


@pytest.mark.parametrize(
    ("change", "finding"),
    [
        # The review's reproductions: host trees bound under the runtime's scratch.
        (lambda v: v + [mount("/tmp/etc", "/etc", "ext4")], "/tmp/etc (ext4 /etc)"),
        (
            lambda v: v + [mount("/tmp/hostcodex", "/home/orca/.codex", "ext4")],
            "/tmp/hostcodex (ext4 /home/orca/.codex)",
        ),
        # A host tmpfs looks like scratch by filesystem type alone.
        (lambda v: v + [mount("/tmp/shm")], "/tmp/shm (tmpfs /)"),
        # A staged file replaced by a host file: two mounts at one point.
        (
            lambda v: v + [mount("/invocation/prompt", "/home/x/prompt", "ext4")],
            "/invocation/prompt mounted 2 times",
        ),
        # A planned mount whose source is not the plan's.
        (
            lambda v: [
                mount("/tmp", "/srv", "ext4") if m["mountpoint"] == "/tmp" else m for m in v
            ],
            "/tmp (ext4 /srv)",
        ),
        (lambda v: [m for m in v if m["mountpoint"] != "/proc"], "/proc missing"),
        (lambda v: v + [mount("/dev/sda", "/sda", "devtmpfs")], "/dev/sda (devtmpfs /sda)"),
        # Review r4511161750: --bind /dev/shm /tmp in place of --tmpfs /tmp. Same
        # type and root as the planned tmpfs; only the device is the host's.
        (swap("/tmp", device=_HOST_SHM), f"/tmp (tmpfs /) on host device {_HOST_SHM}"),
        # A host tmpfs file named like bwrap's data file, in place of a staged one.
        (
            swap("/invocation/prompt", device=_HOST_SHM),
            f"/invocation/prompt (tmpfs /bindfileuSe1Iw//deleted) on host device {_HOST_SHM}",
        ),
        (swap("/", device=_HOST_SHM), f"/ (tmpfs /) on host device {_HOST_SHM}"),
        (swap("/proc", device="0:24"), "/proc (proc /) on host device 0:24"),
        # --bind-data in place of --ro-bind-data.
        (
            swap("/invocation/prompt", options="rw,nosuid"),
            "/invocation/prompt (tmpfs /bindfileuSe1Iw//deleted rw)",
        ),
        # Review r4511338813: a host tmpfs file named null bound at /dev/null has
        # the node's name and root; only the observer's /dev device may supply it.
        (
            swap("/dev/null", device=_HOST_SHM, fstype="tmpfs"),
            f"/dev/null (tmpfs /null) not from /dev: {_HOST_SHM}",
        ),
        (swap("/dev/tty", device="0:777"), "/dev/tty (tmpfs /tty) not from /dev: 0:777"),
        # A host device at a node name the plan does not make.
        (
            lambda v: v + [mount("/dev/pts/0", "/0", "devpts", device=_HOST_DEV)],
            "/dev/pts/0 (devpts /0)",
        ),
    ],
)
def test_any_mount_outside_the_plan_is_a_finding(change, finding):
    planned = codex_proof.planned_mounts(_PLAN)
    host = _HOST | {"0:24"}
    assert finding in codex_proof.unplanned_mounts(change(planned_view()), planned, host, _HOST_DEV)


def test_without_an_observer_dev_no_node_is_exempt():
    planned = codex_proof.planned_mounts(_PLAN)
    found = codex_proof.unplanned_mounts(planned_view(), planned, _HOST, None)
    assert {f.split(" ", 1)[0] for f in found} == {"/dev/null", "/dev/tty"}


def test_a_planned_writable_data_mount_must_be_writable():
    plan = [p.replace("--ro-bind-data", "--bind-data") for p in _PLAN]
    planned = codex_proof.planned_mounts(plan)
    assert planned["/invocation/prompt"] == "data-rw"
    view = swap("/invocation/prompt", options="rw")(planned_view())
    assert codex_proof.unplanned_mounts(view, planned, _HOST, _HOST_DEV) == []
    assert codex_proof.unplanned_mounts(planned_view(), planned, _HOST, _HOST_DEV) != []


# OAuth mode binds one host file on purpose: the staged auth.json, by descriptor.
_AUTH = "/tmp/home/.codex/auth.json"
_STAGED_ROOT = "/stage/inv-1/auth.json"
_OAUTH_PLAN = [*_PLAN[: _PLAN.index("--")], "--bind-fd", "7", _AUTH, *_PLAN[_PLAN.index("--") :]]


def staged_view(**change):
    view = planned_view() + [mount(_AUTH, _STAGED_ROOT, "ext4", device=_HOST_DISK)]
    return swap(_AUTH, **change)(view) if change else view


def test_the_descriptor_binds_are_planned_mounts():
    assert codex_proof.planned_mounts(_OAUTH_PLAN)[_AUTH] == "fd-rw"
    plan = [p.replace("--bind-fd", "--ro-bind-fd") for p in _OAUTH_PLAN]
    assert codex_proof.planned_mounts(plan)[_AUTH] == "fd-ro"


def test_the_planned_staged_file_bind_passes_only_as_that_file():
    planned = codex_proof.planned_mounts(_OAUTH_PLAN)
    staged = {_AUTH: (_HOST_DISK, _STAGED_ROOT)}
    assert codex_proof.unplanned_mounts(staged_view(), planned, _HOST, _HOST_DEV, staged) == []
    for view, told, finding in [
        # Not told which file was staged: a host bind like any other.
        (staged_view(), None, f"{_AUTH} (ext4 {_STAGED_ROOT} rw)"),
        # Another host file, the right device: wrong root.
        (
            staged_view(root="/home/orca/.codex/auth.json"),
            staged,
            f"{_AUTH} (ext4 /home/orca/.codex/auth.json rw)",
        ),
        # The staged path, but another filesystem.
        (
            staged_view(device=_HOST_SHM, fstype="tmpfs"),
            staged,
            f"{_AUTH} (tmpfs {_STAGED_ROOT} rw)",
        ),
        # Planned writable, mounted read-only.
        (staged_view(options="ro,nosuid"), staged, f"{_AUTH} (ext4 {_STAGED_ROOT} ro)"),
        (planned_view(), staged, f"{_AUTH} missing"),
    ]:
        found = codex_proof.unplanned_mounts(view, planned, _HOST, _HOST_DEV, told)
        assert finding in found, (finding, found)


def test_the_staged_file_is_named_by_the_host_mount_that_holds_it(tmp_path):
    staged = tmp_path / "inv" / "auth.json"
    staged.parent.mkdir()
    staged.write_text("{}")
    path = str(staged.resolve())
    holder = str(tmp_path.resolve())
    host = [
        mount("/", "/", "ext4", device="8:1"),
        mount(holder, "/srv/held", "ext4", device="8:2"),
        mount(holder, "/srv/top", "ext4", device="8:3"),  # on top of the one before
        mount(holder + "x", "/no", "ext4", device="8:4"),  # a prefix by string, not by path
    ]
    assert codex_proof.host_file_mount(staged, host) == ("8:3", "/srv/top/inv/auth.json")
    assert codex_proof.host_file_mount(staged, host[:1]) == ("8:1", path)
    with pytest.raises(ValueError, match="no host mount"):
        codex_proof.host_file_mount(staged, [mount("/elsewhere", device="8:5")])


def test_replace_mount_swaps_the_planned_mount_and_its_perms():
    argv = list(_PLAN)
    codex_proof.replace_mount(argv, "/invocation/prompt", ["--bind-data", "$FD", "/x"])
    assert argv[argv.index("--dir") + 2 :][:4] == ["--setenv", "PATH", "/runtime", "--bind-data"]
    assert "--perms" not in argv and argv[argv.index("--bind-data") + 1] == "5"
    argv = list(_PLAN)
    codex_proof.replace_mount(argv, "/tmp", ["--bind", "/dev/shm", "/tmp"])
    assert codex_proof.planned_mounts(argv)["/tmp"] == "bind"
    with pytest.raises(AssertionError, match="mutation does not apply"):
        codex_proof.replace_mount(list(_PLAN), "/nowhere", [])


def test_report_maps_each_measured_failure_to_fail(tmp_path):
    settings = CodexSettings(codex_proof.MODEL)
    profile = Profile(
        runtime="codex", model=settings.model, image=image(tmp_path), command=settings.command
    )
    failures = {check: [] for check in codex_proof.CHECKED}
    failures["provider_code_disabled"] = ["synthetic failure"]
    checks = json.loads(codex_proof.report(profile.subject, failures, b"x"))["checks"]
    assert checks["provider_code_disabled"] == "fail"
    assert {checks[c] for c in codex_proof.CHECKED if c != "provider_code_disabled"} == {"pass"}


@pytest.mark.parametrize(
    "change", [{"runtime": "opencode"}, {"model": "gpt-other"}, {"runtime": "Codex"}]
)
def test_codex_material_refuses_another_runtime_or_model(tmp_path, change):
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), **{"runtime": "codex", "model": codex_proof.MODEL, **change})
    material = InvocationMaterial.stage(root, req)
    try:
        with pytest.raises(Refused, match="^invalid_codex_material$"):
            material.prepare_codex(CodexSettings(codex_proof.MODEL))
        assert set(material.content_hashes) == {"model-credential", "prompt", "mcp.json"}
    finally:
        material.discard()


def test_session_host_argv_has_no_bypass_flag():
    command = CodexSettings(codex_proof.MODEL).command
    assert not set(command) & set(BYPASS_FLAGS)
    assert "--strict-config" in command


BYPASS_FORMS = (
    *((flag,) for flag in BYPASS_FLAGS),
    ("--permission-mode=bypassPermissions",),
    ("--permission-mode", "bypassPermissions"),
    ("--dangerously-skip-permissions=true",),
    ("--Dangerously-Skip-Permissions",),
    ("--allow-dangerously-skip-permissions",),
    ("-s", "danger-full-access"),
    ("--sandbox", "danger-full-access"),
    ("-c", 'sandbox_mode="danger-full-access"'),
    ("--sandbox", "false"),
    ("--sandbox=False",),
    ("--sandbox", "off"),
    ("--yolo",),
    ("--YOLO=true",),
    ("--approval-mode=yolo",),
    ("--approval-mode", "YOLO"),
)
# Same flags with a restricting value, which a parse-based check must still admit.
SAFE_FORMS = (
    ("--permission-mode", "default"),
    ("--sandbox", "read-only"),
    ("--sandbox=true",),
    ("--approval-mode=default",),
    ("-s", "read-only"),
)


@pytest.mark.parametrize("arguments", BYPASS_FORMS)
@pytest.mark.parametrize("runtime", ["claude", "gemini", "synthetic-only"])
def test_no_runtime_profile_may_carry_a_bypass_argument(tmp_path, runtime, arguments):
    files = image(tmp_path)
    good = ("/runtime/codex", "--safe")
    assert Profile(runtime=runtime, model="m", image=files, command=good).command == good
    with pytest.raises(Refused, match="^invalid_runtime_profile$"):
        Profile(runtime=runtime, model="m", image=files, command=(*good, *arguments))


@pytest.mark.parametrize("arguments", SAFE_FORMS)
def test_restricting_values_of_bypass_capable_flags_are_admitted(tmp_path, arguments):
    command = ("/runtime/codex", *arguments, "--safe")
    profile = Profile(runtime="claude", model="m", image=image(tmp_path), command=command)
    assert profile.command == command


@pytest.mark.parametrize("runtime", ["Codex", "CODEX", "OpenCode"])
def test_case_variant_runtime_cannot_skip_the_closed_preset(tmp_path, runtime):
    files = image(tmp_path)
    with pytest.raises(Refused, match="^invalid_runtime_profile$"):
        Profile(runtime=runtime, model="m", image=files, command=("/runtime/codex", "--safe"))


def test_codex_profile_refuses_any_command_but_the_closed_preset(tmp_path):
    files = image(tmp_path)
    settings = CodexSettings(codex_proof.MODEL)
    Profile(runtime="codex", model=settings.model, image=files, command=settings.command)
    with pytest.raises(Refused, match="^invalid_domain_profile$"):
        Profile(
            runtime="codex",
            model=settings.model,
            image=files,
            command=(*settings.command, "--enable", "shell_tool"),
        )


@pytest.mark.parametrize("model", ["claude-opus", "gpt-6-sol;x", "../gpt", ""])
def test_settings_refuse_other_models(model):
    with pytest.raises(Refused, match="^unsupported_codex_settings$"):
        CodexSettings(model)


@pytest.mark.parametrize("credential", [b"", b"\xff", b" a", b"a\nb", b'{"access_token":"x"}'])
def test_render_refuses_non_api_key_credentials(credential):
    req = replace(request(), runtime="codex", model=codex_proof.MODEL)
    with pytest.raises(Refused, match="^unsupported_codex_credential$"):
        CodexSettings(codex_proof.MODEL).render(req.broker, credential)


def test_domain_plan_mounts_every_native_file_readonly_and_seals_stdin(tmp_path):
    settings = CodexSettings(codex_proof.MODEL)
    domain = BubblewrapDomain(image=image(tmp_path), command=settings.command, codex=settings)
    domain._bwrap = Path("/usr/bin/true")
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    req = replace(request(), runtime="codex", model=settings.model)
    material = InvocationMaterial.stage(root, req)
    try:
        argv, *_ = domain._plan(material)
        for name, destination in NATIVE_FILES:
            at = argv.index(destination)
            assert argv[at - 4 : at - 1] == ["--perms", "0400", "--ro-bind-data"], name
            content = os.pread(int(argv[at - 1]), 65536, 0)
            assert hashlib.sha256(content).hexdigest() == material.content_hashes[name]
        env = {argv[i + 1]: argv[i + 2] for i, item in enumerate(argv) if item == "--setenv"}
        assert env == dict(settings.environment)
        assert "--unshare-net" in argv
        assert domain._stdin is not None
        assert os.pread(domain._stdin, 65536, 0) == req.prompt.encode()
        assert argv[-len(settings.command) :] == list(settings.command)
    finally:
        domain._close_all()
        material.discard()


def test_config_names_only_the_broker_and_keeps_credential_out_of_it():
    req = replace(request(), runtime="codex", model=codex_proof.MODEL)
    settings = CodexSettings(codex_proof.MODEL)
    rendered = settings.render(req.broker, b"synthetic-key")
    config = rendered[CONFIG_NAME].decode()
    assert config.count("[mcp_servers.") == 1
    assert b"synthetic-key" not in rendered[CONFIG_NAME] + rendered[CATALOG_NAME]
    assert json.loads(rendered[AUTH_NAME])["OPENAI_API_KEY"] == "synthetic-key"
    assert req.broker.token not in json.dumps(settings.identity, sort_keys=True)


def test_measured_report_is_refused_while_any_required_check_is_unproven(tmp_path):
    # The end-to-end refusal. Even an all-pass measurement leaves required
    # checks unproven, so the real admission parser cannot accept this report.
    assert set(codex_proof.UNPROVEN) == {"restricted_egress", "catalog_conformance"}
    settings = CodexSettings(codex_proof.MODEL)
    profile = Profile(
        runtime="codex", model=settings.model, image=image(tmp_path), command=settings.command
    )
    failures = {check: [] for check in codex_proof.CHECKED}
    raw = codex_proof.report(profile.subject, failures, b"synthetic")
    with pytest.raises(Refused, match="^runtime_proof_refused$"):
        ReviewedProfile(profile, report=raw, expected_sha256=hashlib.sha256(raw).hexdigest())
    assert set(json.loads(raw)["checks"]) == set(codex_proof.CHECKED) | set(codex_proof.UNPROVEN)


def test_default_factory_still_refuses_every_tuple():
    factory = ProfileFactory()
    for runtime, model in (("codex", codex_proof.MODEL), ("claude", "m"), ("opencode", "m")):
        with pytest.raises(Refused, match="^runtime_ineligible$"):
            factory(runtime, model)


def test_live_codex_adapter_argv_is_untouched():
    # Bypass removal is session-host only until the switch release.
    source = (REPO / "src/butlers/core/runtimes/codex.py").read_text()
    assert '"--dangerously-bypass-approvals-and-sandbox",' in source
    assert "session_host" not in source


# The image's own shim ---------------------------------------------------------


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_exact_image_proof_accepts_only_a_static_shim(tmp_path):
    source = REPO / "scripts/runtime_cli_sandbox_init.c"
    static, dynamic = tmp_path / "static", tmp_path / "dynamic"
    for out, flags in ((static, ["-static"]), (dynamic, [])):
        done = subprocess.run(
            ["cc", "-O2", *flags, str(source), "-o", str(out)], capture_output=True, timeout=60
        )
        assert done.returncode == 0, done.stderr[-300:]
    assert exact_image_codex_proof.shim_is_static(static) is True
    assert exact_image_codex_proof.shim_is_static(dynamic) is False
    script = tmp_path / "script"
    script.write_text("#!/bin/sh\n")
    assert exact_image_codex_proof.shim_is_static(script) is False


def _elf(*, machine=62, kind=2, phentsize=56, phnum=1, phoff=64, size=None, ptype=1):
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[16:18] = kind.to_bytes(2, "little")
    header[18:20] = machine.to_bytes(2, "little")
    header[32:40] = phoff.to_bytes(8, "little")
    header[54:56] = phentsize.to_bytes(2, "little")
    header[56:58] = phnum.to_bytes(2, "little")
    body = bytearray(max(phnum, 1) * 56)
    body[0:4] = ptype.to_bytes(4, "little")
    return bytes(header + body)[:size] if size is not None else bytes(header + body)


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("a well-formed static executable", _elf(), True),
        ("six bytes of header", b"\x7fELF\x02\x01", False),
        ("four bytes", b"\x7fELF", False),
        ("empty", b"", False),
        ("no program headers", _elf(phnum=0), False),
        ("program headers past the end", _elf(phoff=10_000, phnum=3), False),
        ("program headers cut short", _elf(phnum=3, size=64 + 56), False),
        ("wrong machine", _elf(machine=183), False),
        ("a position-independent object", _elf(kind=3), False),
        ("wrong header size", _elf(phentsize=32), False),
        ("an interpreter", _elf(ptype=3), False),
        ("a dynamic section", _elf(ptype=2), False),
    ],
)
def test_shim_is_static_fails_closed_on_anything_malformed(tmp_path, name, data, expected):
    shim = tmp_path / "shim"
    shim.write_bytes(data)
    assert exact_image_codex_proof.shim_is_static(shim) is expected, name


def test_shim_is_static_is_false_for_a_missing_file(tmp_path):
    assert exact_image_codex_proof.shim_is_static(tmp_path / "absent") is False


def test_exact_image_proof_refuses_a_dynamic_shim_before_running(tmp_path, capsys):
    codex, shim = tmp_path / "codex", tmp_path / "init"
    codex.write_bytes(b"x")
    shim.write_bytes(b"#!/bin/sh\n")
    assert exact_image_codex_proof.main(codex, shim, tmp_path / "out.json") == 2
    assert "static=false" in capsys.readouterr().out


# Native proof -------------------------------------------------------------


@pytest.fixture(scope="module")
def proof_root(tmp_path_factory):
    binary = os.environ.get("BUTLERS_CODEX_PROOF_BINARY")
    if not binary:
        pytest.skip("BUTLERS_CODEX_PROOF_BINARY unset: no native Codex proof ran, no evidence")
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    assert digest == codex_proof.PINNED_CODEX_SHA256, "not the pinned CLI build"
    root = tmp_path_factory.mktemp("codex-proof")
    images = root / "image"
    images.mkdir()
    shutil.copyfile(binary, images / "codex")
    run = subprocess.run(
        [
            "cc",
            "-static",
            "-O2",
            str(REPO / "scripts/runtime_cli_sandbox_init.c"),
            "-o",
            str(images / "init"),
        ],
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 0, "shim compilation failed"
    (images / "codex").chmod(0o555)
    (images / "init").chmod(0o555)
    return root


def launch(root: Path, scenario: dict, work: Path) -> dict:
    return codex_proof.run(scenario, root / "image", work)


@pytest.mark.parametrize("transport", ["websocket", "http"])
def test_native_restricted_codex_passes_every_measured_check(proof_root, transport, tmp_path):
    observation = launch(proof_root, {"refuse_websocket": transport == "http"}, tmp_path)
    failures = {k: v for k, v in codex_proof.evaluate(observation).items() if v}
    assert failures == {}, (observation.get("proof_error"), observation.get("stderr_tail"))
    assert set(observation["transports"]) == {transport}
    assert observation["settled"] and observation["stage_empty"]
    settings = CodexSettings(codex_proof.MODEL)
    assert all(body["model"] == settings.model for body in observation["bodies"])


# The table lives in codex_proof so the exact-image run executes the same rows.
@pytest.mark.parametrize("name", sorted(codex_proof.MUTATIONS))
def test_native_each_removed_restriction_fails_the_proof(proof_root, name, tmp_path):
    scenario, expected = codex_proof.MUTATIONS[name]
    observation = launch(proof_root, scenario, tmp_path)
    reasons = codex_proof.evaluate(observation)[expected]
    assert reasons, f"{name} left {expected} passing: the proof would not catch this"
    if expected in codex_proof.ISOLATION_CHECKS:
        # Failing because the launch broke would prove nothing about isolation.
        assert observation["exit"] == "completed", observation.get("stderr_tail")
        assert observation["isolation"]["measured"] is True


def search_answers(observation):
    return [
        answer
        for body in observation["bodies"]
        for answer in codex_proof.search_results(body)
        if answer.get("call_id") == codex_proof.CALL_IDS["tool_search"]
    ]


def test_native_tool_search_runs_on_the_client_over_the_broker_alone(proof_root, tmp_path):
    observation = launch(proof_root, {}, tmp_path)
    answers = search_answers(observation)
    assert answers and all(a["execution"] == "client" for a in answers)
    namespaces = {tool["name"] for a in answers for tool in a["tools"]}
    assert namespaces == {"mcp__broker"}
    # Discovery read the broker's own tools/list; nothing else was asked.
    assert any(call["method"] == "tools/list" for call in observation["broker"])
    assert observation["decoy"] == [] and observation["unexpected_paths"] == []
    surface = set().union(*(codex_proof.tool_surface(b) for b in observation["bodies"]))
    assert "client:tool_search" in surface
    assert not {entry for entry in surface if entry.startswith("hosted:")}


def test_native_tool_search_that_reaches_a_second_server_fails(proof_root, tmp_path):
    # The proof's reason to exist for discovery: give the runtime a second MCP
    # endpoint, and the same search must surface it and fail the proof.
    scenario = {"append_config": '\n[mcp_servers.decoy]\nurl = "$ORIGIN/decoy/mcp"\n'}
    observation = launch(proof_root, scenario, tmp_path)
    reasons = codex_proof.evaluate(observation)["broker_only_mcp"]
    assert any("tool_search returned 'mcp__decoy'" in r for r in reasons), reasons
    assert any(call["method"] == "tools/list" for call in observation["decoy"])


def patch_output(observation):
    outputs = {}
    for body in observation["bodies"]:
        outputs.update(codex_proof.tool_outputs(body))
    return outputs[codex_proof.CALL_IDS["apply_patch"]]


def test_native_bypass_flag_is_what_the_read_only_sandbox_stops(proof_root, tmp_path):
    # Why dropping the flag matters even when no shell is offered. Grant the
    # patch tool, and the same patch is refused without the flag, written with it.
    grant = {"catalog_set": {"apply_patch_tool_type": "freeform"}}
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    without = launch(proof_root, grant, tmp_path / "without")
    assert "rejected" in patch_output(without)
    bypass = {**grant, "add_argv": ["--dangerously-bypass-approvals-and-sandbox"]}
    allowed = launch(proof_root, bypass, tmp_path / "with")
    assert "Success" in patch_output(allowed)


# OAuth mode (bu-kiuh8v.16) ----------------------------------------------------


def _oauth_failures(observation):
    failures = codex_proof.evaluate(
        observation,
        provider_auth=f"Bearer {codex_proof.OAUTH_REFRESHED_ACCESS}",
        provider_paths=codex_proof.OAUTH_PROVIDER_PATHS,
    )
    return {k: v for k, v in failures.items() if v}


@pytest.mark.parametrize("transport", ["websocket", "http"])
def test_native_oauth_codex_refreshes_and_the_host_persists_it(proof_root, transport, tmp_path):
    """The staged token is expired: the runtime refreshes it, writes its one writable
    file, and after settlement the host installs that into the volume."""
    observation = launch(
        proof_root, {"oauth": True, "refuse_websocket": transport == "http"}, tmp_path
    )
    assert _oauth_failures(observation) == {}, (
        observation.get("proof_error"),
        observation.get("stderr_tail"),
    )
    assert set(observation["transports"]) == {transport}
    assert observation["provider_account"] == [codex_proof.OAUTH_ACCOUNT]
    assert observation["refresh"] == [
        {"grant_type": "refresh_token", "refresh_token": "SYNTHETIC-STALE-REFRESH"}
    ]
    assert observation["settled"] and observation["stage_empty"]
    assert observation["lease_released"]
    persisted = json.loads(observation["volume_document"])
    assert persisted["tokens"]["access_token"] == codex_proof.OAUTH_REFRESHED_ACCESS
    assert persisted["tokens"]["refresh_token"] == codex_proof.OAUTH_REFRESHED_REFRESH
    assert persisted["tokens"]["account_id"] == codex_proof.OAUTH_ACCOUNT


def test_native_oauth_refresh_for_another_account_leaves_the_volume(proof_root, tmp_path):
    observation = launch(
        proof_root, {"oauth": True, "refresh_account": "acct-somebody-else"}, tmp_path
    )
    assert observation["settled"] and observation["lease_released"]
    assert observation["refresh"], "the runtime never refreshed, so nothing was tested"
    assert observation["volume_document"] == codex_proof.oauth_document()


def test_native_oauth_read_only_auth_file_loses_the_refresh(proof_root, tmp_path):
    """The control: the writable file is what carries a refresh back to the volume."""
    observation = launch(proof_root, {"oauth": True, "readonly_auth": True}, tmp_path)
    assert observation["refresh"], "the runtime never refreshed, so nothing was tested"
    assert observation["settled"] and observation["lease_released"]
    assert observation["volume_document"] == codex_proof.oauth_document()


def test_native_unreachable_broker_fails_the_launch(proof_root, tmp_path):
    (tmp_path / "required").mkdir()
    (tmp_path / "optional").mkdir()
    down = launch(proof_root, {"broker_down": True}, tmp_path / "required")
    assert down["exit"] != "completed"
    assert not down["bodies"], "a session started without its broker"
    # The control: without the setting the same outage leaves a live session.
    optional = {"broker_down": True, "drop_config_lines": ["required = true"]}
    assert launch(proof_root, optional, tmp_path / "optional")["exit"] == "completed"


@pytest.mark.parametrize("served", [False, True])
def test_stopping_the_fakes_never_waits_on_a_server_that_never_served(served):
    """A launch refused before its receipt never starts the fakes; teardown must still end.

    ``shutdown()`` blocks until ``serve_forever`` returns, so calling it on a server that
    never served deadlocks. That, not the launch, is what kept a refused run from finishing.
    """
    import threading
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), codex_proof._Fakes)
    serving = threading.Thread(target=server.serve_forever, daemon=True) if served else None
    if serving is not None:
        serving.start()
    stopper = threading.Thread(target=codex_proof.stop_fakes, args=(server, serving), daemon=True)
    stopper.start()
    stopper.join(timeout=5)
    assert not stopper.is_alive(), "teardown deadlocked"
    assert server.socket.fileno() == -1
    if serving is not None:
        serving.join(timeout=5)
        assert not serving.is_alive()
