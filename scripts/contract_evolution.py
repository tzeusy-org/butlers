"""Fresh retained-owner execution under exact protected-public contract authority.

This is intentional contract evolution, never condensation equivalence. The
ordinary branch/mutation survivor protocol remains the authority for other loss.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import secrets
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import check_condensation_ledger as ledger

AUTHORIZATIONS = "tests/contract-evolution-authorizations/"
REQUESTS = "tests/contract-evolution-requests/"
AUTH_SCHEMA = "contract-evolution-authorization/v1"
REQUEST_SCHEMA = "contract-evolution-request/v1"
RETIREMENT_SCHEMA = "governing-retirement-authorization/v1"
_REQUIREMENT_ID = re.compile(r"REQ-(?:[a-z0-9]+-)+[0-9]+")
# Actual execution bodies must also be independently authorized source inputs.
MACHINERY = {
    "scripts/contract_evolution.py",
    "scripts/check_condensation_ledger.py",
    "scripts/contract_lifecycle.py",
    "scripts/condense_evidence.py",
    "scripts/pre_push.py",
    "scripts/condensation_lineage.py",
}


def requirement_blocks(text: str) -> dict[str, tuple[int, int]]:
    """Locate literal complete normative blocks; fences cannot create authority."""
    headings, identifiers = [], []
    offset, fence = 0, None
    for line in text.splitlines(keepends=True):
        delimiter = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is not None:
            if (
                delimiter
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
        elif delimiter:
            fence = delimiter[1][0], len(delimiter[1])
        else:
            if re.match(r"^#{1,3} ", line):
                headings.append((offset, line.startswith("### Requirement:")))
            identity = re.fullmatch(r"ID: (REQ-(?:[a-z0-9]+-)+[0-9]+)[ \t]*", line.rstrip("\r\n"))
            if identity:
                identifiers.append((offset, identity[1]))
        offset += len(line)
    result = {}
    for index, (start, requirement) in enumerate(headings):
        if not requirement:
            continue
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        ids = [identity for position, identity in identifiers if start <= position < end]
        if not ids:
            continue
        if len(ids) != 1 or ids[0] in result:
            raise ledger.EvidenceError("ambiguous-contract-block")
        result[ids[0]] = start, end
    return result


def requirement_digest(text: str, identity: str) -> str:
    try:
        start, end = requirement_blocks(text)[identity]
    except KeyError:
        raise ledger.EvidenceError("missing-contract-block") from None
    return hashlib.sha256(text[start:end].encode()).hexdigest()


def record_at(root: Path, ref: str, name: str) -> dict | None:
    ledger.public_path(name)
    row = ledger.git(root, "ls-tree", ref, "--", name)
    if not row:
        return None
    metadata, path = row.split("\t")
    mode, kind, _ = metadata.split()
    if path != name or kind != "blob" or mode not in {"100644", "100755"}:
        raise ledger.EvidenceError("indirect-contract-input")
    return {
        "mode": mode,
        "sha256": hashlib.sha256(ledger.committed_body(root, ref, name)).hexdigest(),
    }


def current_record(root: Path, name: str) -> dict | None:
    path = root / ledger.public_path(name)
    if not path.exists() and not path.is_symlink():
        return None
    row = ledger.input_record(root, name)
    if row["mode"] == "120000":
        raise ledger.EvidenceError("indirect-contract-input")
    return {key: row[key] for key in ("mode", "sha256")}


def validate_request(value: dict) -> None:
    ledger.fields(value, {"schema", "authorization", "owners", "timeout_s"})
    if (
        value["schema"] != REQUEST_SCHEMA
        or not isinstance(value["authorization"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", value["authorization"])
    ):
        raise ledger.EvidenceError("contract-request-identity")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or any(not isinstance(node, str) or "[" in node or "::" not in node for node in owners)
        or len(set(owners)) != len(owners)
        or type(value["timeout_s"]) not in {int, float}
        or not 0 < value["timeout_s"] <= 300
    ):
        raise ledger.EvidenceError("contract-request-population-or-bound")


def authorize(root: Path, base: str, trusted: str, request: dict) -> tuple[dict, str]:
    """Only committed protected-public bytes authorize the exact source delta."""
    name = AUTHORIZATIONS + request["authorization"] + ".json"
    authority_record = record_at(root, trusted, name)
    if authority_record is None:
        raise ledger.EvidenceError("missing-protected-contract-authority")
    if current_record(root, name) != authority_record:
        raise ledger.EvidenceError("changed-protected-contract-authority")
    authority = ledger._strict_json_body(ledger.committed_body(root, trusted, name).decode())
    ledger.fields(authority, {"schema", "id", "bead", "decision", "owners", "contracts", "inputs"})
    if authority["schema"] != AUTH_SCHEMA or authority["id"] != request["authorization"]:
        raise ledger.EvidenceError("contract-authority-identity")
    ledger.fields(authority["decision"], {"owner", "date", "reference"})
    if not all(
        isinstance(v, str) and v for v in [authority["bead"], *authority["decision"].values()]
    ):
        raise ledger.EvidenceError("missing-contract-review-record")
    rows = authority["owners"]
    ledger.identities(rows, "before")
    if {row["before"] for row in rows} != set(request["owners"]):
        raise ledger.EvidenceError("incomplete-authorized-owner-population")
    cites = set()
    for row in rows:
        ledger.fields(row, {"before", "after", "contracts", "reason"})
        if (
            row["before"] != row["after"]
            or not isinstance(row["reason"], str)
            or not row["reason"]
            or not isinstance(row["contracts"], list)
            or not row["contracts"]
            or len(set(row["contracts"])) != len(row["contracts"])
        ):
            raise ledger.EvidenceError("contract-evolution-is-retained-owner-only")
        cites.update(row["contracts"])
    contracts = authority["contracts"]
    seen, by_path = set(), {}
    for row in contracts:
        ledger.fields(row, {"path", "id", "before_sha256", "after_sha256"})
        pair = row["path"], row["id"]
        if pair in seen or row["id"] == "REQ-testing-053":
            raise ledger.EvidenceError("ambiguous-or-self-authorizing-contract")
        seen.add(pair)
        before = ledger.committed_body(root, base, row["path"]).decode()
        after = (root / ledger.public_path(row["path"])).read_text()
        if (
            requirement_digest(before, row["id"]) != row["before_sha256"]
            or requirement_digest(after, row["id"]) != row["after_sha256"]
            or row["before_sha256"] == row["after_sha256"]
        ):
            raise ledger.EvidenceError("stale-or-unchanged-authorized-contract")
        by_path.setdefault(row["path"], set()).add(row["id"])
    if not cites or not cites <= {row["id"] for row in contracts}:
        raise ledger.EvidenceError("unmapped-changed-contract")
    inputs = authority["inputs"]
    names = ledger.identities(inputs, "path")
    for row in inputs:
        ledger.fields(row, {"path", "before", "after"})
        if (
            row["before"] != record_at(root, base, row["path"])
            or row["after"] != current_record(root, row["path"])
            or row["before"] is None
            and row["after"] is None
        ):
            raise ledger.EvidenceError("stale-authorized-source-or-context")
    required = {"pyproject.toml", "uv.lock"}
    for node in request["owners"]:
        path = node.split("::", 1)[0]
        required.add(path)
        for parent in PurePosixPath(path).parents:
            context = (parent / "conftest.py").as_posix()
            if (
                record_at(root, base, context) is not None
                or current_record(root, context) is not None
            ):
                required.add(context)
        for ref in (base, None):
            text = (
                ledger.committed_body(root, ref, path).decode()
                if ref
                else (root / path).read_text()
            )
            if node not in ledger.test_shapes(path, text):
                raise ledger.EvidenceError("missing-retained-contract-owner")
    if not required <= names:
        raise ledger.EvidenceError("incomplete-authorized-owner-context")
    records = {row["path"]: row for row in inputs}
    if any(records[p]["before"] != records[p]["after"] for p in ("pyproject.toml", "uv.lock")):
        raise ledger.EvidenceError("changed-environment-needs-independent-installed-tools")
    # Freshness alone cannot approve executing machinery or a new request.
    tracked = set(ledger.tracked_paths(root))
    actual = {r["path"]: r for r in executor_records()}
    for path in MACHINERY:
        if path not in tracked or ledger.input_record(root, path) != actual[path]:
            raise ledger.EvidenceError("unapproved-contract-executor")
    # New public history is trusted; every candidate delta needs exact approval.
    changed = ledger.git(root, "diff", "--name-only", "-z", "--no-renames", trusted, "--")
    for path in filter(None, changed.split("\0")):
        ledger.public_path(path)
        if path.startswith(AUTHORIZATIONS):
            if current_record(root, path) != record_at(root, trusted, path):
                raise ledger.EvidenceError("changed-protected-contract-authority")
            continue
        if path in names:
            continue
        if path in by_path:
            old = ledger.committed_body(root, trusted, path).decode()
            new = (root / path).read_text()

            def masked(text):
                blocks = requirement_blocks(text)
                spans = [(blocks[identity], identity) for identity in by_path[path]]
                for (start, end), identity in sorted(spans, reverse=True):
                    text = text[:start] + "CONTRACT:" + identity + "\n" + text[end:]
                return text

            if masked(old) == masked(new):
                continue
        raise ledger.EvidenceError("unapproved-contract-runtime-input")
    return authority, name


def _read_run(directory, run, side, selection, records, controls, tools, nonce):
    """Consume fresh raw phase and SQLite carriers, never a claimed PASS count."""
    scope = sorted({node.split("::", 1)[0] for node in selection})
    expected_flags = [
        "-n",
        "0",
        "-p",
        "no:cov",
        "-p",
        "no:terminal",
        "-o",
        "addopts=",
        "--import-mode=importlib",
        "-m",
        "not nightly and not bench and not perf",
        "--ignore=tests/benchmarks",
    ]
    if run["id"] != side:
        raise ledger.EvidenceError("changed-contract-run-identity")
    temporary_root = directory / (side + ".runtime-tmp")
    runtime_environment = dict.fromkeys(("TMPDIR", "PYTEST_DEBUG_TEMPROOT"), str(temporary_root))
    if temporary_root.is_symlink() or not temporary_root.is_dir():
        raise ledger.EvidenceError("changed-contract-runtime-directory")
    execution_request = ledger.strict_json(directory / (side + ".request.json"))
    if execution_request != {
        "root": str(directory / side),
        "selection": selection,
        "scope": scope,
        "nonce": nonce,
        "output": str(directory / (side + ".json")),
        "id": side,
        "control_inputs": [],
        "runtime_environment": runtime_environment,
    }:
        raise ledger.EvidenceError("changed-contract-execution-request")
    saved = ledger.strict_json(directory / (side + ".json"))
    if saved != run or (
        run["complete"] is not True
        or run["cleanup"] is not True
        or type(run["exit"]) is not int
        or run["exit"] != 0
        or run["deselected"] != 0
        or type(run["selected"]) is not int
        or run["selection"] != selection
        or run["tools"] != tools
        or run["core"] != "ctrace"
        or run["tracer"] != "CTracer"
        or type(run["deselected"]) is not int
        or run["scope"] != scope
        or run["scope_inputs"] != [r for p in scope for r in records if r["path"] == p]
        or run["command_flags"] != expected_flags
        or run["measurement_root"] != str(directory / side)
        or run["runtime_environment"]
        != {**runtime_environment, "tempfile_root": str(temporary_root)}
    ):
        raise ledger.EvidenceError("incomplete-contract-execution")
    cases = run["cases"]
    ledger.identities(cases, "key")
    if run["selected"] != len(cases) or {r["node"] for r in cases} != set(selection):
        raise ledger.EvidenceError("incomplete-contract-case-population")
    for case in cases:
        ledger.fields(case, {"key", "node", "phases"})
        if not ledger.HEX.fullmatch(case["key"]) or case["phases"] != dict.fromkeys(
            ("setup", "call", "teardown"), "passed"
        ):
            raise ledger.EvidenceError("nonpass-contract-case")
    boundary = run["request_boundary"]
    ledger.fields(boundary, {"inputs", "runtime_read", "descendant_execution"})
    if (
        boundary["inputs"] != []
        or boundary["runtime_read"] is not False
        or type(boundary["descendant_execution"]) is not bool
    ):
        raise ledger.EvidenceError("contract-control-runtime-boundary")
    ledger.require_request_observation(run["request_access"], boundary, set(), records)
    import contract_lifecycle

    contract_lifecycle.validate(
        directory / (side + ".json"),
        [
            sys.executable,
            str(Path(__file__).resolve().with_name("condense_evidence.py")),
            "_worker",
            str(directory / (side + ".request.json")),
        ],
        nonce,
    )
    arcs = ledger.coverage_arcs(directory / (run["id"] + ".coverage"), run)
    if arcs != run["arcs"] or any(not arcs[row["key"]] for row in cases):
        raise ledger.EvidenceError("unproved-contract-case-execution")
    return {(row["key"], row["node"]) for row in cases}


def executor_records():
    root = Path(__file__).resolve().parents[1]
    return ledger.source_records(
        root,
        sorted(
            MACHINERY
            | {
                "scripts/condense_evidence.py",
                "scripts/pre_push.py",
            }
        ),
    )


def execute(root, base, authority, request, directory, controls, *, trusted, nonce=None):
    """Reuse the existing observed executor in exact complete owned snapshots."""
    import condense_evidence as producer
    import contract_lifecycle

    directory.mkdir()
    deadline = time.monotonic() + request["timeout_s"]
    inputs = ledger.source_records(root, ledger.tracked_paths(root))
    approved, _ = authorize(root, base, trusted, request)
    if ledger.canonical(approved) != ledger.canonical(authority):
        raise ledger.EvidenceError("changed-protected-contract-authority")
    tools = ledger.tool_record()
    binding = {
        "base": ledger.git(root, "rev-parse", base),
        "head": ledger.git(root, "rev-parse", "HEAD"),
        "tree": ledger.git(root, "rev-parse", "HEAD^{tree}"),
        "inputs": inputs,
        "tools": tools,
        "executor": executor_records(),
        "nonce": nonce if nonce is not None else secrets.token_hex(32),
    }
    producer.publish(directory / "receipt.json", {"status": "UNKNOWN", "binding": binding})
    if ledger.canonical(authorize(root, base, trusted, request)[0]) != ledger.canonical(authority):
        raise ledger.EvidenceError("changed-protected-contract-authority")
    cancelled = threading.Event()
    workers_live = False
    interrupted_execution = False

    def interrupted(_signum, _frame):
        nonlocal interrupted_execution
        interrupted_execution = True
        cancelled.set()
        if not workers_live:
            raise InterruptedError

    previous = {
        sig: signal.signal(sig, interrupted)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)
    }
    try:
        copies = {side: directory / side for side in ("before", "after")}
        # Retain per-side pytest/tool temporaries through independent consumption.
        # Shared pytest retention may otherwise reap the earlier side's executables.
        temporary_roots = {side: directory / (side + ".runtime-tmp") for side in copies}
        for path in temporary_roots.values():
            path.mkdir()
        for path in copies.values():
            path.mkdir()
        if ledger.source_records(root, ledger.tracked_paths(root)) != inputs:
            raise ledger.EvidenceError("contract-source-changed-after-authorization")
        producer.snapshot_base(root, base, copies["before"])
        producer.snapshot_current(root, copies["after"], inputs)
        before_inputs = ledger.source_records(copies["before"], ledger.tracked_paths(root, base))
        binding["before_inputs"] = before_inputs
        producer.publish(directory / "receipt.json", {"status": "UNKNOWN", "binding": binding})
        selection = request["owners"]
        scope = sorted({node.split("::", 1)[0] for node in selection})
        runs = {}
        if deadline - time.monotonic() <= 15:
            raise ledger.EvidenceError("contract-proof-setup-timeout")
        workers_live = True
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                try:
                    futures = {
                        pool.submit(
                            producer.suite,
                            copies[side],
                            selection,
                            scope,
                            binding["nonce"],
                            directory / (side + ".json"),
                            side,
                            max(0.001, deadline - time.monotonic() - 15),
                            set(),
                            cancelled,
                            contract_lifecycle.run,
                            temporary_roots[side],
                        ): side
                        for side in copies
                    }
                    for future in as_completed(futures):
                        runs[futures[future]] = future.result()
                finally:
                    cancelled.set()
        finally:
            workers_live = False
        if interrupted_execution:
            raise InterruptedError
        if any(run["complete"] is not True for run in runs.values()):
            raise ledger.EvidenceError("incomplete-contract-workers")
        populations = []
        for side, records in (("before", before_inputs), ("after", inputs)):
            if ledger.source_records(copies[side], [r["path"] for r in records]) != records:
                raise ledger.EvidenceError("contract-source-changed-during-execution")
            populations.append(
                _read_run(
                    directory,
                    runs[side],
                    side,
                    selection,
                    records,
                    controls,
                    tools,
                    binding["nonce"],
                )
            )
        if populations[0] != populations[1]:
            raise ledger.EvidenceError("contract-case-population-change")
        if (
            time.monotonic() > deadline
            or ledger.tool_record() != tools
            or (
                ledger.git(root, "rev-parse", "HEAD") != binding["head"]
                or ledger.git(root, "rev-parse", "HEAD^{tree}") != binding["tree"]
                or ledger.source_records(root, ledger.tracked_paths(root)) != inputs
            )
        ):
            raise ledger.EvidenceError("stale-contract-proof-binding")
        producer.publish(
            directory / "receipt.json",
            {
                "status": "PASS",
                "binding": binding,
                "authorization": ledger.canonical(authority),
                "cases": len(populations[0]),
                "seconds": request["timeout_s"] - (deadline - time.monotonic()),
                "runs": runs,
                "controls": sorted(controls),
            },
        )
    finally:
        for side in ("before", "after"):
            shutil.rmtree(directory / side, ignore_errors=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextmanager
def bounded(seconds):
    """Bound setup, execution and independent consumption; restore caller state."""

    def expired(_signum, _frame):
        raise ledger.EvidenceError("contract-proof-timeout")

    previous = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if old_timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, old_timer[0] - (time.monotonic() - started)),
                old_timer[1],
            )


def _already_landed(
    root: Path, trusted: str, path: str, request: dict, request_record: dict
) -> bool:
    """True only when trusted public bytes already carry this request and its authorization.

    Every comparison is against bytes committed at *trusted*, never a candidate claim: the
    request file, the authorization, each retained owner file, and each authorized contract
    block must be identical between the trusted parent and the candidate. Anything else (a
    candidate-only request, an edited authorization, an owner or contract the candidate
    touched) returns False and falls through to the fresh proof.
    """
    try:
        if record_at(root, trusted, path) != {k: request_record[k] for k in ("mode", "sha256")}:
            return False
        name = AUTHORIZATIONS + request["authorization"] + ".json"
        authority_record = record_at(root, trusted, name)
        if authority_record is None or current_record(root, name) != authority_record:
            return False
        authority = ledger._strict_json_body(ledger.committed_body(root, trusted, name).decode())
        if (
            authority.get("schema") != AUTH_SCHEMA
            or authority.get("id") != request["authorization"]
        ):
            return False
        if {row["before"] for row in authority["owners"]} != set(request["owners"]):
            return False
        for owner in request["owners"]:
            owner_path = owner.split("::", 1)[0]
            trusted_owner = record_at(root, trusted, owner_path)
            if trusted_owner is None or current_record(root, owner_path) != trusted_owner:
                return False
        for row in authority["contracts"]:
            target = root / ledger.public_path(row["path"])
            if target.is_symlink() or not target.is_file():
                return False
            if requirement_digest(target.read_text(), row["id"]) != row["after_sha256"]:
                return False
            trusted_text = ledger.committed_body(root, trusted, row["path"]).decode()
            if requirement_digest(trusted_text, row["id"]) != row["after_sha256"]:
                return False
    except (ledger.EvidenceError, KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        return False
    return True


def fresh_accounts(
    root: Path, base: str, trusted: str, losses: dict, directory: Path, *, reports=None
) -> set[str]:
    paths = ledger.tracked_paths(root)
    requests = [p for p in paths if p.startswith(REQUESTS) and p.endswith(".json")]
    admitted = set()
    for index, path in enumerate(requests):
        if ledger.input_record(root, path)["mode"] == "120000":
            raise ledger.EvidenceError("indirect-contract-request")
        request_record = ledger.input_record(root, path)
        request = ledger.strict_json(root / path)
        validate_request(request)
        if not set(request["owners"]) & set(losses):
            continue
        if trusted is not None and _already_landed(root, trusted, path, request, request_record):
            # The loss is public history: the trusted parent already admitted this exact
            # request and nothing it covers has changed since. Do not re-prove it against a
            # stale event base, and do not hold this PR's unrelated delta to the request.
            if admitted & set(request["owners"]):
                raise ledger.EvidenceError("duplicate-contract-owner-account")
            admitted.update(request["owners"])
            continue
        started = time.monotonic()
        with bounded(request["timeout_s"]):
            if trusted is None:
                trusted = ledger.git(root, "rev-parse", "origin/main")
            if directory is None:
                import tempfile

                home = root / ".tmp/condensation-ci"
                home.mkdir(parents=True, exist_ok=True)
                directory = Path(tempfile.mkdtemp(prefix="run-", dir=home))
            authority, authorization_path = authorize(root, base, trusted, request)
            controls = set(requests) | MACHINERY | {authorization_path}
            controls |= {p for p in paths if p.startswith(AUTHORIZATIONS)}
            from pre_push import Refusal, run_checked

            run = directory / f"contract-{index}"
            nonce = secrets.token_hex(32)
            try:
                run_checked(
                    "contract-evolution-proof",
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--repo-root",
                        str(root),
                        "--base",
                        base,
                        "--trusted",
                        trusted,
                        "--request",
                        path,
                        "--output",
                        str(run),
                        "--nonce",
                        nonce,
                    ],
                    root,
                    timeout=max(0.001, request["timeout_s"] - 10),
                )
            except Refusal:
                raise ledger.EvidenceError("fresh-contract-evolution-proof-refused") from None
            if ledger.input_record(root, path) != request_record:
                raise ledger.EvidenceError("contract-request-changed-during-execution")
            consume(root, base, authority, request, run, controls, nonce)
            if reports is not None:
                receipt = ledger.strict_json(run / "receipt.json")
                binding = receipt["binding"]
                reports.append(
                    {
                        "authorization": authority["id"],
                        "authorization_sha256": record_at(root, trusted, authorization_path)[
                            "sha256"
                        ],
                        "base": binding["base"],
                        "public_authority": ledger.git(root, "rev-parse", trusted),
                        "head": binding["head"],
                        "tree": binding["tree"],
                        "matched_owner_count": len(set(request["owners"]) & set(losses)),
                        "executed_owner_count": len(request["owners"]),
                        "case_count": receipt["cases"],
                        "elapsed_s": time.monotonic() - started,
                        "complete": True,
                    }
                )
        if admitted & set(request["owners"]):
            raise ledger.EvidenceError("duplicate-contract-owner-account")
        admitted.update(request["owners"])
    return admitted


def _requirement_span(text: str, key: str) -> tuple[int, int] | None:
    """Locate one literal requirement block by `ID:` or by its exact heading text."""
    if _REQUIREMENT_ID.fullmatch(key):
        return requirement_blocks(text).get(key)
    headings, offset, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        delimiter = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is not None:
            if (
                delimiter
                and delimiter[1][0] == fence[0]
                and len(delimiter[1]) >= fence[1]
                and not delimiter[2].strip()
            ):
                fence = None
        elif delimiter:
            fence = delimiter[1][0], len(delimiter[1])
        elif re.match(r"^#{1,3} ", line):
            headings.append((offset, line.rstrip("\r\n")))
        offset += len(line)
    wanted = "### Requirement: " + key
    spans = [
        (start, headings[index + 1][0] if index + 1 < len(headings) else len(text))
        for index, (start, line) in enumerate(headings)
        if line == wanted
    ]
    if len(spans) > 1:
        raise ledger.EvidenceError("ambiguous-contract-block")
    return spans[0] if spans else None


def requirement_digest_or_none(text: str | None, key: str) -> str | None:
    span = None if text is None else _requirement_span(text, key)
    return None if span is None else hashlib.sha256(text[span[0] : span[1]].encode()).hexdigest()


def owner_body_sha256(text: str, qualname: str) -> str | None:
    """Hash one test owner's own source (decorators included), not its file."""
    tree = ast.parse(text)
    body, node = tree.body, None
    for part in qualname.split("::"):
        node = next(
            (
                n
                for n in body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            return None
        body = node.body
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    lines = text.splitlines(keepends=True)[first - 1 : node.end_lineno]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def _retirement_manifest(body: bytes, name: str) -> dict | None:
    """Parse one manifest; None unless it claims the retirement schema."""
    try:
        value = ledger._strict_json_body(body.decode())
    except (ledger.EvidenceError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != RETIREMENT_SCHEMA:
        return None
    required = {"schema", "id", "bead", "authority", "retired", "requirements"}
    if not required <= set(value) <= required | {"replacements"}:
        raise ledger.EvidenceError("retirement-schema-fields")
    if (
        value["id"] != name[len(AUTHORIZATIONS) : -len(".json")]
        or not all(isinstance(value[k], str) and value[k] for k in ("id", "bead", "authority"))
        or not isinstance(value["retired"], list)
        or not value["retired"]
        or not isinstance(value["requirements"], list)
        or not value["requirements"]
        or not isinstance(value.get("replacements", []), list)
    ):
        raise ledger.EvidenceError("retirement-authority-identity")
    for row in value["retired"]:
        ledger.fields(row, {"owner", "base_sha256", "reason"})
        if (
            not all(isinstance(v, str) and v for v in row.values())
            or "::" not in row["owner"]
            or not ledger.HEX.fullmatch(row["base_sha256"])
        ):
            raise ledger.EvidenceError("retirement-owner-record")
    if len({row["owner"] for row in value["retired"]}) != len(value["retired"]):
        raise ledger.EvidenceError("retirement-owner-record")
    for row in value["requirements"]:
        if not isinstance(row, dict) or not {"path", "requirement", "before_sha256"} <= set(row):
            raise ledger.EvidenceError("retirement-requirement-record")
        if set(row) - {"path", "requirement", "before_sha256", "after_sha256"} or not all(
            isinstance(v, str) and v for v in row.values()
        ):
            raise ledger.EvidenceError("retirement-requirement-record")
        ledger.public_path(row["path"])
        if not all(ledger.HEX.fullmatch(row[k]) for k in row if k.endswith("_sha256")):
            raise ledger.EvidenceError("retirement-requirement-record")
    if not all(isinstance(owner, str) and "::" in owner for owner in value.get("replacements", [])):
        raise ledger.EvidenceError("retirement-owner-record")
    return value


def _retired_in_public_history(root: Path, trusted: str, owners: dict, current: dict) -> bool:
    """True when every manifest owner is absent from both the trusted parent and the candidate."""
    for owner in owners:
        if owner in current:
            return False
        path = owner.split("::", 1)[0]
        try:
            body = ledger.committed_body(root, trusted, path).decode()
        except (ledger.EvidenceError, UnicodeError):
            continue  # The whole file is gone from the trusted parent.
        try:
            if owner in ledger.test_shapes(path, body):
                return False
        except (SyntaxError, ValueError):
            return False
    return True


def retirement_accounts(
    root: Path, base: str, trusted: str | None, losses: dict, current: dict, *, reports=None
) -> set[str]:
    """Admit whole-owner retirement only under manifest bytes from the trusted ref."""
    if trusted is None:
        try:
            trusted = ledger.git(root, "rev-parse", "origin/main")
        except ledger.EvidenceError:
            return set()  # No protected public source exists, so nothing can be admitted.
    listed = ledger.git(root, "ls-tree", "-r", "--name-only", "-z", trusted, "--", AUTHORIZATIONS)
    trusted_names = {n for n in listed.split("\0") if n.endswith(".json")}
    admitted: set[str] = set()
    for name in sorted(trusted_names):
        manifest = _retirement_manifest(ledger.committed_body(root, trusted, name), name)
        if manifest is None:
            continue
        owners = {row["owner"]: row for row in manifest["retired"]}
        if not set(owners) & set(losses):
            continue  # Historical authority with no current loss is inert.
        authority_record = record_at(root, trusted, name)
        if current_record(root, name) != authority_record:
            raise ledger.EvidenceError("changed-protected-retirement-authority")
        if admitted & set(owners):
            raise ledger.EvidenceError("duplicate-retirement-owner-account")
        # Owners already absent from the trusted parent were retired by public history that
        # this manifest gated. A base older than the manifest's own cannot re-litigate them.
        landed = _retired_in_public_history(root, trusted, owners, current)
        for owner, row in owners.items():
            if losses.get(owner) != "removed-or-moved":
                raise ledger.EvidenceError("retired-owner-not-wholly-removed")
            if landed:
                continue
            path, qualname = owner.split("::", 1)
            try:
                old = ledger.committed_body(root, base, path).decode()
                digest = owner_body_sha256(old, qualname)
            except (ledger.EvidenceError, SyntaxError, ValueError):
                digest = None
            if digest != row["base_sha256"]:
                raise ledger.EvidenceError("stale-retired-owner-hash")
        for row in [] if landed else manifest["requirements"]:
            before = requirement_digest_or_none(
                (
                    ledger.committed_body(root, base, row["path"]).decode()
                    if record_at(root, base, row["path"])
                    else None
                ),
                row["requirement"],
            )
            if before != row["before_sha256"]:
                raise ledger.EvidenceError("stale-retirement-requirement")
            target = root / ledger.public_path(row["path"])
            after = requirement_digest_or_none(
                target.read_text() if target.is_file() and not target.is_symlink() else None,
                row["requirement"],
            )
            if after == row["before_sha256"]:
                raise ledger.EvidenceError("unchanged-retirement-requirement")
            if "after_sha256" in row and after != row["after_sha256"]:
                raise ledger.EvidenceError("mismatched-retirement-requirement")
        if any(owner not in current for owner in manifest.get("replacements", [])):
            raise ledger.EvidenceError("missing-retirement-replacement")
        admitted |= set(owners)
        if reports is not None:
            reports.append(
                {
                    "manifest": manifest["id"],
                    "manifest_sha256": authority_record["sha256"],
                    "admitted_owner_count": len(owners),
                    "base": ledger.git(root, "rev-parse", base),
                    "public_authority": ledger.git(root, "rev-parse", trusted),
                }
            )
    # A candidate-supplied retirement manifest is never authority.
    for path in ledger.tracked_paths(root):
        if not path.startswith(AUTHORIZATIONS) or not path.endswith(".json"):
            continue
        if path in trusted_names or not (root / path).is_file():
            continue
        try:
            claimed = _retirement_manifest((root / path).read_bytes(), path)
        except ledger.EvidenceError:
            claimed = {"retired": []}
        if claimed and {row["owner"] for row in claimed["retired"]} & set(losses):
            raise ledger.EvidenceError("candidate-only-retirement-authority")
    return admitted


def consume(root, base, authority, request, directory, controls, nonce):
    receipt = ledger.strict_json(directory / "receipt.json")
    binding = receipt["binding"]
    if (
        receipt["status"] != "PASS"
        or receipt["authorization"] != ledger.canonical(authority)
        or receipt["controls"] != sorted(controls)
        or binding["base"] != ledger.git(root, "rev-parse", base)
        or binding["head"] != ledger.git(root, "rev-parse", "HEAD")
        or binding["tree"] != ledger.git(root, "rev-parse", "HEAD^{tree}")
        or binding["before_inputs"] != ledger.committed_records(root, base)
        or binding["tools"] != ledger.tool_record()
        or binding["executor"] != executor_records()
        or binding["inputs"] != ledger.source_records(root, ledger.tracked_paths(root))
        or binding["nonce"] != nonce
        or not ledger.HEX.fullmatch(binding["nonce"])
        or type(receipt["seconds"]) not in {int, float}
        or not 0 <= receipt["seconds"] <= request["timeout_s"]
    ):
        raise ledger.EvidenceError("stale-contract-proof-binding")
    populations = []
    for side, records in (("before", binding["before_inputs"]), ("after", binding["inputs"])):
        populations.append(
            _read_run(
                directory,
                receipt["runs"][side],
                side,
                request["owners"],
                records,
                controls,
                binding["tools"],
                nonce,
            )
        )
    if (
        populations[0] != populations[1]
        or type(receipt["cases"]) is not int
        or receipt["cases"] != len(populations[0])
    ):
        raise ledger.EvidenceError("contract-case-population-change")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--trusted", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    if not ledger.HEX.fullmatch(args.nonce):
        raise ledger.EvidenceError("invalid-fresh-contract-nonce")
    request_path = ledger.public_path(args.request)
    request_record = ledger.input_record(root, str(request_path))
    request = ledger.strict_json(root / request_path)
    validate_request(request)

    def expired(_signum, _frame):
        raise ledger.EvidenceError("contract-proof-timeout")

    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, request["timeout_s"] - 25))
    try:
        authority, name = authorize(root, args.base, args.trusted, request)
        paths = ledger.tracked_paths(root)
        controls = (
            MACHINERY | {name} | {p for p in paths if p.startswith((REQUESTS, AUTHORIZATIONS))}
        )
        execute(
            root,
            args.base,
            authority,
            request,
            args.output,
            controls,
            trusted=args.trusted,
            nonce=args.nonce,
        )
        if ledger.input_record(root, str(request_path)) != request_record:
            raise ledger.EvidenceError("contract-request-changed-during-execution")
        consume(root, args.base, authority, request, args.output, controls, args.nonce)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    try:
        main()
    except ledger.EvidenceError as exc:
        print(json.dumps({"status": "REFUSED", "category": str(exc)}))
        sys.exit(1)
    except (KeyError, TypeError, ValueError, OSError, InterruptedError):
        print(json.dumps({"status": "REFUSED", "category": "invalid-structural-evidence"}))
        sys.exit(1)
