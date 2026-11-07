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
from tests.session_host import codex_proof
from tests.session_host.test_core import request

REPO = Path(__file__).resolve().parents[2]
PROVEN_SETTINGS_SHA256 = "95cef015deba9a584150d8379b6a13edc1c9716df7c49bb880999f336da4dbd3"
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
        {
            "type": "namespace",
            "name": "mcp__broker",
            "tools": [{"type": "function", "name": "goal_ping"}],
        },
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


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("exit", "broker_only_mcp"),
        ("no-argv", "broker_only_mcp"),
        ("bypass-argv", "builtin_shell_disabled"),
        ("wrong-provider-auth", "broker_only_mcp"),
        ("decoy", "local_mcp_disabled"),
        ("unexpected-path", "broker_only_mcp"),
        ("broker-call-missing", "broker_only_mcp"),
        ("broker-bearer", "broker_only_mcp"),
        ("command-event", "builtin_shell_disabled"),
        ("file-event", "builtin_filesystem_disabled"),
        ("shell-ran", "builtin_shell_disabled"),
        ("patch-ran", "builtin_filesystem_disabled"),
        ("search-ran", "builtin_web_disabled"),
        ("pong-missing", "broker_only_mcp"),
    ],
)
def test_each_observed_violation_fails(change, expected):
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
    elif change == "pong-missing":
        outputs[codex_proof.CALL_IDS["mcp__broker.goal_ping"]]["output"] = "requires approval"
    assert expected in failed(observation)


def test_session_host_argv_has_no_bypass_flag():
    command = CodexSettings(codex_proof.MODEL).command
    assert not set(command) & set(BYPASS_FLAGS)
    assert "--strict-config" in command


@pytest.mark.parametrize("flag", BYPASS_FLAGS)
@pytest.mark.parametrize("runtime", ["claude", "gemini", "synthetic-only"])
def test_no_runtime_profile_may_carry_a_bypass_argument(tmp_path, runtime, flag):
    files = image(tmp_path)
    good = ("/runtime/codex", "--safe")
    assert Profile(runtime=runtime, model="m", image=files, command=good).command == good
    with pytest.raises(Refused, match="^invalid_runtime_profile$"):
        Profile(runtime=runtime, model="m", image=files, command=(*good, flag))


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
    # The end-to-end refusal. Even an all-pass measurement leaves four required
    # checks unproven, so the real admission parser cannot accept this report.
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


# Each row removes one restriction from the real launch. The proof earns its
# name here: if a row's check still passes, the restriction is not what enforces
# the behavior, and the proof could not have caught its absence.
MUTATIONS = {
    "bypass-flag": (
        {"add_argv": ["--dangerously-bypass-approvals-and-sandbox"]},
        "builtin_shell_disabled",
    ),
    "shell-features-on": (
        {
            "replace_config": {
                "shell_tool = false": "shell_tool = true",
                "unified_exec = false": "unified_exec = true",
            },
            "catalog_set": {"shell_type": "unified_exec"},
        },
        "builtin_shell_disabled",
    ),
    "catalog-grants-apply-patch": (
        {"catalog_set": {"apply_patch_tool_type": "freeform"}},
        "builtin_filesystem_disabled",
    ),
    "view-image-on": (
        {"replace_config": {"view_image = false": "view_image = true"}},
        "builtin_filesystem_disabled",
    ),
    "web-search-on": (
        {
            "replace_config": {'web_search = "disabled"': 'web_search = "live"'},
            "catalog_set": {"web_search_tool_type": "text_and_image"},
        },
        "provider_search_disabled",
    ),
    "broker-calls-unapproved": (
        {"drop_config_lines": ['default_tools_approval_mode = "approve"']},
        "broker_only_mcp",
    ),
    "second-mcp-server": (
        {"append_config": '\n[mcp_servers.decoy]\nurl = "$ORIGIN/decoy/mcp"\n'},
        "local_mcp_disabled",
    ),
    "no-closed-catalog": (
        {"drop_config_lines": ['model_catalog_json = "/tmp/home/.codex/catalog.json"']},
        "builtin_shell_disabled",
    ),
    "web-search-setting-dropped": (
        {"drop_config_lines": ['web_search = "disabled"']},
        "provider_search_disabled",
    ),
    "multi-agent-on": ({"drop_config_lines": ["multi_agent = false"]}, "broker_only_mcp"),
    "goals-on": ({"drop_config_lines": ["goals = false"]}, "broker_only_mcp"),
    "request-user-input-on": (
        {"drop_config_lines": ["experimental_request_user_input = { enabled = false }"]},
        "broker_only_mcp",
    ),
    "catalog-grants-search-tool": (
        {"catalog_set": {"supports_search_tool": True}},
        "provider_search_disabled",
    ),
    "catalog-grants-code-mode": (
        {"catalog_set": {"tool_mode": "code_mode_only"}},
        "builtin_shell_disabled",
    ),
    "catalog-grants-agents": ({"catalog_set": {"multi_agent_version": "v2"}}, "broker_only_mcp"),
    "catalog-grants-experimental": (
        {"catalog_set": {"experimental_supported_tools": ["clock"]}},
        "broker_only_mcp",
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_native_each_removed_restriction_fails_the_proof(proof_root, name, tmp_path):
    scenario, expected = MUTATIONS[name]
    observation = launch(proof_root, scenario, tmp_path)
    reasons = codex_proof.evaluate(observation)[expected]
    assert reasons, f"{name} left {expected} passing: the proof would not catch this"


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


def test_native_unreachable_broker_fails_the_launch(proof_root, tmp_path):
    (tmp_path / "required").mkdir()
    (tmp_path / "optional").mkdir()
    down = launch(proof_root, {"broker_down": True}, tmp_path / "required")
    assert down["exit"] != "completed"
    assert not down["bodies"], "a session started without its broker"
    # The control: without the setting the same outage leaves a live session.
    optional = {"broker_down": True, "drop_config_lines": ["required = true"]}
    assert launch(proof_root, optional, tmp_path / "optional")["exit"] == "completed"
