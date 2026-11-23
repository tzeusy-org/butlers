"""The nightly covered workflow refuses partial evidence before it publishes.

Executes the nightly coverage jobs' own shell (duration evidence, prerequisite gates and
the combined report) against owned fixtures (REQ-ci-shard-assurance-006).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _workflow_step(*, job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing {name!r} step")


def _nightly_jobs() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())["jobs"]


def test_covered_groups_publish_sanitized_duration_evidence(tmp_path: Path) -> None:
    jobs = _nightly_jobs()
    shards = jobs["coverage-shards"]
    assert "needs" not in shards
    assert shards["strategy"]["fail-fast"] is False
    rows = shards["strategy"]["matrix"]["include"]
    assert rows == [
        {"lane": lane, "group": index, "timeout": cap}
        for lane, count, cap in (("unit", 5, 28), ("integration", 6, 36))
        for index in range(1, count + 1)
    ]
    assert shards["timeout-minutes"] == "${{ matrix.timeout }}"
    execute = _workflow_step(job=shards, name="Run the covered lane group")
    assert execute["run"] == (
        "uv run python scripts/ci_lanes.py run "
        "--lane ${{ matrix.lane }} --group ${{ matrix.group }} "
        '--output "${RUNNER_TEMP}/ci-artifacts/${{ matrix.lane }}-${{ matrix.group }}"'
    )
    assert execute["env"]["COVERAGE_FILE"].endswith(
        "coverage-${{ matrix.lane }}-${{ matrix.group }}.data"
    )
    duration = _workflow_step(
        job=shards, name="Prepare ${{ matrix.lane }} group ${{ matrix.group }} duration evidence"
    )
    evidence = _workflow_step(job=shards, name="Upload complete covered execution evidence")
    for row in rows:
        directory = tmp_path / "ci-artifacts" / f"{row['lane']}-{row['group']}"
        directory.mkdir(parents=True)
        (directory / "raw-junit.xml").write_text(
            '<testsuites><testsuite><testcase classname="tests.contract" '
            'name="test_receipt[private-value]" time="0.125">'
            "<system-out>private captured output</system-out>"
            "</testcase></testsuite></testsuites>"
        )
        command = duration["run"]
        uploads = evidence["with"]["path"]
        for key, value in row.items():
            command = command.replace("${{ matrix." + key + " }}", str(value))
            uploads = uploads.replace("${{ matrix." + key + " }}", str(value))
        result = subprocess.run(
            ["bash", "-e", "-c", command],
            cwd=REPO_ROOT,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (row, result.stderr.decode())
        sanitized = (directory / "junit.xml").read_text()
        assert 'name="test_receipt[...]"' in sanitized
        assert "private" not in sanitized
        assert "test_receipt[...]" in (directory / "top-durations.txt").read_text()
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/junit.xml" in uploads
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/collected.json" in uploads
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/durations.json" in uploads


def test_coverage_prerequisite_gates_fail_closed() -> None:
    jobs = _nightly_jobs()
    for name, prerequisites in (
        ("coverage-preflight", ["coverage-shards"]),
        ("coverage-report", ["coverage-shards", "coverage-preflight"]),
    ):
        job = jobs[name]
        assert job["needs"] == prerequisites
        assert job["if"] == (
            "${{ always() && (github.event_name != 'workflow_dispatch' "
            "|| !inputs.exact_image_only) }}"
        )
        gate = _workflow_step(job=job, name="Require every coverage prerequisite")
        assert job["steps"][0] == gate
        for failed in (None, *prerequisites, "unknown-job"):
            for outcome in ("failure", "skipped", "cancelled", "unknown", "success"):
                needs = {key: {"result": "success"} for key in prerequisites}
                if failed is not None:
                    needs[failed] = {"result": outcome}
                result = subprocess.run(
                    ["bash", "-e", "-c", gate["run"]],
                    env={**os.environ, "NEEDS_JSON": json.dumps(needs)},
                    capture_output=True,
                    timeout=10,
                )
                expected = failed is None or (failed != "unknown-job" and outcome == "success")
                assert (result.returncode == 0) is expected, (name, failed, outcome)
        missing = {key: {"result": "success"} for key in prerequisites[:-1]}
        result = subprocess.run(
            ["bash", "-e", "-c", gate["run"]],
            env={**os.environ, "NEEDS_JSON": json.dumps(missing)},
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0


@pytest.mark.parametrize(
    "event,exact_only,expected",
    [
        ("schedule", True, True),
        ("workflow_dispatch", False, True),
        ("workflow_dispatch", True, False),
    ],
)
def test_exact_image_dispatch_is_explicit_and_preserves_default_nightly(
    event: str,
    exact_only: bool,
    expected: bool,
) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    jobs = workflow["jobs"]
    assert "if" not in jobs["exact-image-sandbox"]
    assert "needs" not in jobs["exact-image-sandbox"]
    for name, job in jobs.items():
        if name == "exact-image-sandbox":
            continue
        expression = job.get("if")
        selection = "github.event_name != 'workflow_dispatch' || !inputs.exact_image_only"
        required = (
            "${{ always() && (" + selection + ") }}"
            if name in {"coverage-preflight", "coverage-report"}
            else "${{ " + selection + " }}"
        )
        assert expression == required, name
        # Evaluate the exact small expression above, including always() fan-ins;
        # a schedule must keep all jobs even if an unrelated input is truthy.
        evaluated = expression[3:-2].strip().replace("always()", "True")
        evaluated = evaluated.replace("github.event_name", repr(event))
        evaluated = evaluated.replace("!inputs.exact_image_only", str(not exact_only))
        evaluated = evaluated.replace("||", "or").replace("&&", "and")
        assert eval(evaluated, {"__builtins__": {}}, {}) is expected, name

    # PyYAML's YAML 1.1 loader represents the workflow's `on` key as True.
    triggers = workflow[True]
    assert triggers["workflow_dispatch"]["inputs"]["exact_image_only"] == {
        "description": "Run only the complete exact-image conformance job",
        "type": "boolean",
        "default": False,
        "required": False,
    }
    assert triggers["schedule"] == [{"cron": "0 2 * * *"}]


def _historical_controls_coverage_receipt(tmp_path: Path) -> dict:
    """Trace genuine old bodies and current code under the repository config."""
    from coverage import CoverageData

    script = tmp_path / "trace_historical_controls.py"
    script.write_text(
        "import sys, json, hashlib\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from butlers.api.routers import model_settings\n"
        "from butlers.connectors import filtered_event_buffer\n"
        "from butlers.tools.relationship import dates\n"
        "from tests.three_seams_helpers import baseline_function\n"
        "functions = {name: baseline_function(name, vars(module)) for name, module in [\n"
        "    ('create_catalog_entry', model_settings), ('upcoming_dates', dates),\n"
        "    ('record', filtered_event_buffer)]}\n"
        "kwargs = dict(external_message_id='coverage-control', source_channel='email',\n"
        "    sender_identity='777000', subject_or_preview='482913',\n"
        "    filter_reason='validation_error', full_payload={})\n"
        "old = filtered_event_buffer.FilteredEventBuffer(\n"
        "    connector_type='telegram', endpoint_identity='synthetic:coverage')\n"
        "functions['record'](old, **kwargs)\n"
        "current = filtered_event_buffer.FilteredEventBuffer(\n"
        "    connector_type='telegram', endpoint_identity='synthetic:coverage')\n"
        "current.record(**kwargs)\n"
        "assert old._rows[-1][6] == kwargs['subject_or_preview']\n"
        "assert current._rows[-1][6] == '[auth-code withheld: telegram]'\n"
        "print(json.dumps({name: function.__code__.co_filename\n"
        "    for name, function in functions.items()}))\n"
    )
    data_file = tmp_path / "historical-controls.data"
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}", str(script)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = CoverageData(basename=str(data_file))
    data.read()
    measured = set(data.measured_files())
    expected = {str(path.resolve()) for path in (REPO_ROOT / "src/butlers").rglob("*.py")}
    # The current-source positive prevents an absence-only green when no code ran.
    current = str(REPO_ROOT / "src/butlers/connectors/filtered_event_buffer.py")
    assert data.lines(current)
    assert measured == expected, (sorted(measured - expected), sorted(expected - measured))
    functions = json.loads(result.stdout)
    fixture = json.loads((REPO_ROOT / "tests/fixtures/three_seams_baseline.json").read_text())
    for name, filename in functions.items():
        assert fixture[name]["git_sha"] in filename and fixture[name]["path"] in filename
        assert not Path(filename).is_relative_to(REPO_ROOT / "src/butlers")
    return {"source_count": len(measured), "historical_filenames": functions}


def test_ci_coverage_report_rejects_any_bad_input_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the actual report shell with ten independently traced coverage DBs.

    The immutable M1 shell demonstrates that nine valid plus one nonempty corrupt
    database produced a partial green report. This is local reporting evidence,
    not a hosted artifact-upload, badge-network or merge-group timing claim.
    """
    from coverage import CoverageData

    historical = _historical_controls_coverage_receipt(tmp_path)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_ci_coverage as reporter

    checkout = tmp_path / "checkout"
    source = checkout / "src/butlers/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 7\n")
    (source.parent / "__init__.py").write_text("")
    (checkout / "trace.py").write_text("import runpy\nrunpy.run_path('src/butlers/example.py')\n")
    (checkout / ".coveragerc").write_text("[run]\nsource = src/butlers\n")
    tests = checkout / "tests/test_example.py"
    tests.parent.mkdir()
    tests.write_text("def test_example(): pass\n")
    (checkout / "roster").mkdir()
    (checkout / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    for index in range(6):
        (checkout / f"tests/test_seed_{index}.py").write_text(
            "import pytest\ndef test_unit(): pass\n@pytest.mark.integration\ndef test_integration(): pass\n"
        )
    specs = [
        (lane, index)
        for lane, count in (("unit", 5), ("integration", 6))
        for index in range(1, count + 1)
    ]
    scripts = checkout / "scripts"
    scripts.mkdir()
    for filename in ("check_ci_coverage.py", "ci_lanes.py"):
        shutil.copyfile(REPO_ROOT / "scripts" / filename, scripts / filename)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI fixture",
            "-c",
            "user.email=ci-fixture@example.invalid",
            "commit",
            "-qm",
            "coverage control fixture",
        ],
        cwd=checkout,
        check=True,
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    for name, value in {
        "GITHUB_SHA": head,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "merge_group",
    }.items():
        monkeypatch.setenv(name, value)
    inputs = tmp_path / "runner/ci-coverage"
    population: dict[Path, bytes] = {}
    for lane, index in specs:
        data = inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
        data.parent.mkdir(parents=True)
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--data-file={data}", "trace.py"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
        reporter.write_group_metadata(
            coverage_file=data, repo_root=checkout, lane=lane, group=index
        )
        population[data] = data.read_bytes()
        metadata = data.with_suffix(".data.metadata.json")
        population[metadata] = metadata.read_bytes()
    # Use the installed interpreter for each original `uv run` invocation. This
    # avoids installing another environment and preserves real coverage commands.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    shim = binaries / "uv"
    shim.write_text(
        '#!/bin/sh\n[ "$1" = run ] || exit 90\nshift\n'
        'if [ "$1" = python ]; then shift; exec ' + shlex.quote(sys.executable) + ' "$@"; fi\n'
        "exec " + shlex.quote(sys.executable) + ' -m "$@"\n'
    )
    shim.chmod(0o755)
    combined = inputs / "combined.data"
    report = tmp_path / "runner/ci-artifacts/coverage/coverage.json"
    output = tmp_path / "outputs"
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(inputs.parent),
        "COMBINED_COVERAGE": str(combined),
        "COMBINED_REPORT": str(report),
        "GITHUB_OUTPUT": str(output),
        **{
            f"{lane.upper()}_{index}_COVERAGE": str(
                inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
            )
            for lane, index in specs
        },
    }
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    fixed = _workflow_step(
        job=workflow["jobs"]["coverage-report"],
        name="Combine coverage from all independent lane groups",
    )["run"]
    old_path = REPO_ROOT / "tests/fixtures/ci_coverage/m1-combine.sh"
    old = old_path.read_text()
    receipts = []

    def reset() -> None:
        shutil.rmtree(inputs)
        for path, contents in population.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        report.unlink(missing_ok=True)
        output.write_text("")

    def stamp(data: Path) -> None:
        reporter.write_group_metadata(coverage_file=data, repo_root=checkout, lane="unit", group=1)

    def run(label: str, body: str, expected: int) -> None:
        result = subprocess.run(
            ["bash", "-e", "-c", body],
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        published = "percentage=" in output.read_text() and "color=" in output.read_text()
        assert result.returncode == expected, (label, result.stdout, result.stderr)
        assert report.exists() == (expected == 0), (label, result.stderr)
        assert published == (expected == 0), (label, result.stderr)
        receipts.append(
            {
                "case": label,
                "exit": result.returncode,
                "report": report.exists(),
                "badge_outputs": published,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    data = inputs / "unit-1/coverage-unit-1.data"
    metadata = data.with_suffix(".data.metadata.json")
    reset()
    run("fixed-ten-independently-traced-valid", fixed, 0)
    reset()
    data.write_bytes(b"nonempty corrupt coverage control\n")
    stamp(data)  # A valid digest cannot stand in for reading the actual SQLite DB.
    run("baseline-nine-valid-one-nonempty-corrupt", old, 0)
    reset()
    data.write_bytes(b"nonempty corrupt coverage control\n")
    stamp(data)
    run("fixed-nine-valid-one-nonempty-corrupt", fixed, 2)
    for case in (
        "missing",
        "empty",
        "extra",
        "mixed-run",
        "stale-head",
        "wrong-group",
        "wrong-config",
        "digest",
        "incompatible-tracing",
        "source-population",
    ):
        reset()
        if case == "missing":
            data.unlink()
        elif case == "empty":
            data.unlink()
            empty = CoverageData(basename=str(data))
            empty.add_lines({})
            empty.write()
            stamp(data)
        elif case == "extra":
            (inputs / "extra.data").write_bytes(b"extra")
        elif case in {"incompatible-tracing", "source-population"}:
            data.unlink()
            command = [sys.executable, "-m", "coverage", "run", f"--data-file={data}"]
            if case == "incompatible-tracing":
                command.append("--branch")
            else:
                command.append("--omit=src/butlers/__init__.py")
            subprocess.run(
                [*command, "trace.py"], cwd=checkout, check=True, capture_output=True, text=True
            )
            stamp(data)
        else:
            payload = json.loads(metadata.read_text())
            field, value = {
                "mixed-run": ("run_id", "124"),
                "stale-head": ("head", "0" * 40),
                "wrong-group": ("group", True),
                "wrong-config": ("config", {}),
                "digest": ("sha256", "0" * 64),
            }[case]
            payload[field] = value
            metadata.write_text(json.dumps(payload))
        run("fixed-" + case, fixed, 2)
    covered_workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    covered = _workflow_step(
        job=covered_workflow["jobs"]["coverage-report"],
        name="Combine coverage from all independent lane groups",
    )["run"]
    # Each event gets eleven genuine traced inputs stamped by the production
    # writer. No merge-group event is borrowed to certify the scheduled or PR
    # assurance path.
    for event in ("schedule", "workflow_dispatch", "pull_request", "push"):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        environment["GITHUB_EVENT_NAME"] = event
        reset()
        for lane, index in specs:
            current_data = inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    f"--data-file={current_data}",
                    "trace.py",
                ],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            )
            reporter.write_group_metadata(
                coverage_file=current_data, repo_root=checkout, lane=lane, group=index
            )
        run("covered-actual-" + event, covered, 2 if event == "push" else 0)
        if event != "push":
            payload = json.loads(metadata.read_text())
            payload["event"] = "merge_group"
            metadata.write_text(json.dumps(payload))
            report.unlink(missing_ok=True)
            output.write_text("")
            combined.unlink(missing_ok=True)
            run("covered-cross-event-" + event, covered, 2)
    assert all("GITHUB_EVENT_NAME=merge_group" not in body for body in (fixed, covered))
    receipt = tmp_path / "coverage-report-controls.json"
    receipt.write_text(
        json.dumps(
            {
                "baseline_shell_sha256": hashlib.sha256(old.encode()).hexdigest(),
                "fixed_shell_sha256": hashlib.sha256(fixed.encode()).hexdigest(),
                "source_checkout": head,
                "historical_control_source_identity": historical,
                "controls": receipts,
            },
            indent=2,
        )
    )
    print(f"coverage report receipt: {receipt}")
