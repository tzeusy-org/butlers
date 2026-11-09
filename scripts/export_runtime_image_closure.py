"""Export fixed installed-image evidence, without executing a runtime.

Only the existing nightly exact-image builder calls this helper. ``identity``
uses host Git/Docker/provider metadata; ``export`` runs in the already-built
image with no network, read-only root and one empty writable output directory.
Neither command grants session-host eligibility or discovers runtime behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath

SCRIPT = "scripts/export_runtime_image_closure.py"
GENERATOR = "scripts/generate_runtime_cli_sandbox_manifest.py"
V3 = "/usr/local/share/butlers/runtime-cli-sandbox-inputs.json"
VERSIONS = "/opt/cli-versions.txt"
SHIM = "/usr/local/libexec/butlers/runtime-cli-sandbox-init"
CA = "/etc/ssl/certs/ca-certificates.crt"
PACKAGES = ("/usr/lib/node_modules/opencode-ai", "/usr/local/lib/node_modules/opencode-ai")
PINS = {"opencode": "1.17.7", "codex": "0.159.2"}
MAX_FILES = 64
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_JSON = 1024 * 1024
SECONDS = 90
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_LIBRARY = re.compile(
    r"/(?:usr/)?lib(?:64)?/(?:[a-z0-9_-]+/)?[a-zA-Z0-9_+.-]+\.so(?:\.[a-zA-Z0-9_.-]+)*\Z"
)


class Refused(ValueError):
    """Fixed-category refusal; never include command output or file contents."""


def require(condition: object, category: str) -> None:
    if not condition:
        raise Refused(category)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        require(key not in result, "json_duplicate")
        result[key] = value
    return result


def document(raw: bytes) -> dict:
    require(len(raw) <= MAX_JSON, "json_limit")
    value = json.loads(
        raw, object_pairs_hook=_pairs, parse_constant=lambda _: require(False, "json_nonfinite")
    )
    require(type(value) is dict, "json_shape")
    return value


def identity_checked(value: dict) -> dict:
    fields = {
        "version",
        "checkout_sha",
        "checkout_tree",
        "workflow_sha",
        "run_id",
        "run_attempt",
        "job_id",
        "job_name",
        "repository",
        "image_id",
        "base_input_sha",
        "exporter_sha256",
        "generator_sha256",
        "versions",
    }
    require(type(value) is dict and set(value) == fields, "identity_fields")
    require(type(value["version"]) is int and value["version"] == 1, "identity_version")
    for key in ("checkout_sha", "checkout_tree", "workflow_sha"):
        require(type(value[key]) is str and _SHA.fullmatch(value[key]), "identity_sha")
    for key in ("run_id", "run_attempt", "job_id"):
        require(type(value[key]) is int and 0 < value[key] < 10**16, "identity_number")
    for key in ("base_input_sha", "exporter_sha256", "generator_sha256"):
        require(type(value[key]) is str and _HEX.fullmatch(value[key]), "identity_digest")
    require(
        type(value["image_id"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]),
        "identity_image",
    )
    require(value["job_name"] == "exact-image-sandbox", "identity_job")
    require(
        type(value["repository"]) is str
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]),
        "identity_repository",
    )
    require(value["versions"] == PINS, "identity_versions")
    return value


def _command(args: list[str]) -> str:
    # Only Git's fixed hash projection and Docker's fixed two-field projection.
    result = subprocess.run(args, capture_output=True, timeout=10, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 1024, "host_metadata")
    return result.stdout.decode("ascii").strip()


def prepare_identity() -> dict:
    checkout = _command(["git", "rev-parse", "HEAD"])
    tree = _command(["git", "rev-parse", "HEAD^{tree}"])
    require(checkout == os.environ.get("GITHUB_SHA"), "checkout_binding")
    # Exact build fingerprint algorithm; the generator and its v3 contract stay unchanged.
    fingerprint = hashlib.sha256()
    for name in (
        "Dockerfile.base",
        "scripts/runtime_clis/install_system_artifacts.py",
        "deploy/runtime-clis/debian-system-inputs.json",
        "scripts/runtime_cli_sandbox_init.c",
        GENERATOR,
    ):
        fingerprint.update(
            name.encode()
            + b"\0"
            + hashlib.sha256(Path(name).read_bytes()).hexdigest().encode()
            + b"\0"
        )
    image = _command(
        [
            "docker",
            "image",
            "inspect",
            f"butlers-app:{checkout}",
            "--format",
            '{{.Id}}\n{{ index .Config.Labels "butlers.base.input_sha" }}',
        ]
    )
    image_id, label = image.splitlines()
    require(label == fingerprint.hexdigest(), "image_fingerprint")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    repository = os.environ["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "identity_repository")
    require(os.environ.get("GITHUB_JOB") == "exact-image-sandbox", "identity_job")
    jobs = []
    for page in range(1, 4):
        url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(MAX_JSON + 1)
        data = document(raw)
        require(type(data.get("jobs")) is list, "provider_jobs")
        jobs.extend(data["jobs"])
        if len(data["jobs"]) < 100:
            break
    else:
        raise Refused("provider_pagination")
    own = [job for job in jobs if job.get("name") == "exact-image-sandbox"]
    require(
        len(own) == 1
        and own[0].get("head_sha") == checkout
        and own[0].get("status") == "in_progress",
        "provider_job_binding",
    )
    return identity_checked(
        {
            "version": 1,
            "checkout_sha": checkout,
            "checkout_tree": tree,
            "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"],
            "run_id": run_id,
            "run_attempt": attempt,
            "job_id": own[0]["id"],
            "job_name": "exact-image-sandbox",
            "repository": repository,
            "image_id": image_id,
            "base_input_sha": label,
            "exporter_sha256": hashlib.sha256(Path(SCRIPT).read_bytes()).hexdigest(),
            "generator_sha256": hashlib.sha256(Path(GENERATOR).read_bytes()).hexdigest(),
            "versions": PINS,
        }
    )


def safe_path(value: object) -> str:
    require(type(value) is str and 0 < len(value) <= 512, "path_shape")
    path = PurePosixPath(value)
    require(
        str(path) == value
        and path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) <= 20,
        "path_shape",
    )
    return value


def allowed(value: str, package: str) -> bool:
    return value in (
        V3,
        VERSIONS,
        SHIM,
        CA,
        package + "/package.json",
        package + "/bin/opencode.exe",
    ) or bool(_LIBRARY.fullmatch(value))


def metadata(info: os.stat_result) -> dict:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "nlink": info.st_nlink,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def open_regular(root: Path, name: str) -> int:
    """Anchor every component; canonical source paths must contain no symlink."""
    parts = PurePosixPath(safe_path(name)).parts[1:]
    current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            following = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = following
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), "source_type")
        except BaseException:
            os.close(fd)
            raise
        return fd
    finally:
        os.close(current)


def read_small(root: Path, name: str, limit: int) -> bytes:
    fd = open_regular(root, name)
    try:
        before = metadata(os.fstat(fd))
        require(before["size"] <= limit, "file_limit")
        raw = os.read(fd, limit + 1)
        require(len(raw) == before["size"] and metadata(os.fstat(fd)) == before, "source_drift")
        return raw
    finally:
        os.close(fd)


def elf_facts(fd: int, size: int) -> dict | None:
    """Parse file bytes only; no ldd/loader/runtime invocation or ABI inference."""
    if os.pread(fd, 4, 0) != b"\x7fELF":
        return None
    header = os.pread(fd, 64, 0)
    require(len(header) >= 52 and header[4] in (1, 2) and header[5] in (1, 2), "elf_header")
    bits = 32 if header[4] == 1 else 64
    endian = "<" if header[5] == 1 else ">"
    fmt = endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    fields = struct.unpack_from(fmt, header, 16)
    kind, machine, _, _, phoff, _, _, _, entsize, count, *_ = fields
    required = 32 if bits == 32 else 56
    require(
        required <= entsize <= 256 and count <= 256 and phoff + entsize * count <= size,
        "elf_program_headers",
    )
    segments = []
    interpreter = None
    for index in range(count):
        raw = os.pread(fd, required, phoff + index * entsize)
        values = struct.unpack(endian + ("IIIIIIII" if bits == 32 else "IIQQQQQQ"), raw)
        typ = values[0]
        offset, address, length = (
            (values[1], values[2], values[4]) if bits == 32 else (values[2], values[3], values[5])
        )
        require(offset + length <= size, "elf_segment")
        segments.append((typ, offset, address, length))
        if typ == 3:
            require(interpreter is None and 1 < length <= 512, "elf_interpreter")
            rendered = os.pread(fd, length, offset)
            require(rendered.endswith(b"\0") and b"\0" not in rendered[:-1], "elf_interpreter")
            interpreter = safe_path(rendered[:-1].decode("ascii"))
    needed_offsets = []
    string_address = string_size = None
    entry = 8 if bits == 32 else 16
    for typ, offset, _, length in segments:
        if typ != 2:
            continue
        require(length <= 65536 and length % entry == 0, "elf_dynamic")
        for position in range(offset, offset + length, entry):
            tag, value = struct.unpack(
                endian + ("iI" if bits == 32 else "qQ"), os.pread(fd, entry, position)
            )
            if tag == 0:
                break
            if tag == 1:
                needed_offsets.append(value)
            elif tag == 5:
                string_address = value
            elif tag == 10:
                string_size = value
    needed = []
    if needed_offsets:
        require(
            string_address is not None
            and type(string_size) is int
            and 0 < string_size <= 1024 * 1024
            and len(needed_offsets) <= 128,
            "elf_strings",
        )
        tables = [
            offset + string_address - address
            for typ, offset, address, length in segments
            if typ == 1
            and address <= string_address
            and string_address + string_size <= address + length
        ]
        require(len(tables) == 1, "elf_strings")
        for offset in needed_offsets:
            require(offset < string_size, "elf_strings")
            raw = os.pread(fd, min(513, string_size - offset), tables[0] + offset)
            name, separator, _ = raw.partition(b"\0")
            require(separator and re.fullmatch(rb"[A-Za-z0-9_.+-]{1,512}", name), "elf_needed")
            needed.append(name.decode("ascii"))
    return {
        "bits": bits,
        "endian": "little" if endian == "<" else "big",
        "machine": machine,
        "type": kind,
        "interpreter": interpreter,
        "needed": needed,
    }


def aliases(root: Path, destination: str) -> list[dict]:
    """Record every original alias component without selecting byte sources."""
    result = []
    pending = list(PurePosixPath(destination).parts[1:])
    resolved: list[str] = []
    hops = 0
    while pending:
        hops += 1
        require(hops <= 128, "alias_limit")
        component = pending.pop(0)
        if component in ("", "."):
            continue
        if component == "..":
            require(resolved, "alias_escape")
            resolved.pop()
            continue
        current = root.joinpath(*resolved, component)
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(current)
            require(
                0 < len(target) <= 512 and all(32 <= ord(c) < 127 for c in target), "alias_target"
            )
            result.append(
                {
                    "path": "/" + "/".join([*resolved, component]),
                    "target": target,
                    "metadata": metadata(info),
                }
            )
            if target.startswith("/"):
                resolved = []
            pending = target.split("/") + pending
        else:
            resolved.append(component)
    return result


def export_files(root: Path, output: Path, identity: dict) -> dict:
    identity_checked(identity)
    require(
        output.is_dir() and not output.is_symlink() and not any(output.iterdir()),
        "output_not_empty",
    )
    info = output.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "output_owner")
    start = time.monotonic()
    v3_raw = read_small(root, V3, MAX_JSON)
    v3 = document(v3_raw)
    require(type(v3.get("version")) is int and v3["version"] == 3, "v3_version")
    shim = v3["shim"]
    provider = v3["providers"]["opencode-openai"]
    require(
        shim["name"] == "runtime-cli-sandbox-init"
        and shim["executable"] == SHIM
        and provider["binary"] == "opencode",
        "v3_identity",
    )
    bindings = shim["readonly_inputs"] + provider["readonly_inputs"]
    require(type(bindings) is list and len(bindings) <= MAX_FILES, "file_count")
    for item in bindings:
        require(type(item) is dict and set(item) == {"source", "destination"}, "binding_shape")
        safe_path(item["source"])
        safe_path(item["destination"])
    packages = {
        item["source"]
        for item in provider["readonly_inputs"]
        if item["source"] in PACKAGES and item["source"] == item["destination"]
    }
    require(len(packages) == 1, "package_root")
    package = packages.pop()
    payload = package + "/bin/opencode.exe"
    require(provider["executable"] == payload, "selected_payload")
    package_raw = read_small(root, package + "/package.json", MAX_JSON)
    package_info = document(package_raw)
    require(
        package_info.get("name") == "opencode-ai"
        and package_info.get("version") == PINS["opencode"]
        and package_info.get("bin") == {"opencode": "./bin/opencode.exe"},
        "package_identity",
    )
    versions = read_small(root, VERSIONS, 65536).decode("utf-8")
    require(
        re.search(r"(?m)(?:^|[─├└+` ]+)opencode-ai@1\.17\.7$", versions)
        and re.search(r"(?m)(?:^|[─├└+` ]+)@openai/codex@0\.159\.2$", versions),
        "installed_versions",
    )
    selected: dict[str, set[str]] = {
        name: {name} for name in (V3, VERSIONS, package + "/package.json", payload, SHIM, CA)
    }
    omitted = []
    for item in bindings:
        source, destination = item["source"], item["destination"]
        if destination == "/etc/resolv.conf":
            omitted.append({"destination": destination, "reason": "resolver_contents_excluded"})
            continue
        if source == package and destination == package:
            package_stat = (root / package.lstrip("/")).lstat()
            require(stat.S_ISDIR(package_stat.st_mode), "package_type")
            omitted.append(
                {
                    "destination": destination,
                    "reason": "directory_not_exported",
                    "metadata": metadata(package_stat),
                }
            )
            continue
        require(allowed(source, package) and allowed(destination, package), "path_allowlist")
        selected.setdefault(source, set()).add(destination)
    require(len(selected) <= MAX_FILES, "file_count")
    (output / "files").mkdir(mode=0o700)
    records = []
    total = 0
    for index, (source, destinations) in enumerate(sorted(selected.items())):
        require(time.monotonic() - start < SECONDS, "time_limit")
        fd = open_regular(root, source)
        try:
            original = metadata(os.fstat(fd))
            for destination in destinations:
                logical = (root / destination.lstrip("/")).stat()
                require(
                    (logical.st_dev, logical.st_ino) == (original["device"], original["inode"]),
                    "binding_identity",
                )
            require(
                0 <= original["size"] <= MAX_FILE and total + original["size"] <= MAX_TOTAL,
                "byte_limit",
            )
            copied = f"files/{index:04d}.bin"
            target_fd = os.open(
                output / copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            digest = hashlib.sha256()
            written = 0
            try:
                while True:
                    require(time.monotonic() - start < SECONDS, "time_limit")
                    chunk = os.read(fd, min(65536, original["size"] - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    require(
                        written <= original["size"] and total + written <= MAX_TOTAL, "source_drift"
                    )
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(target_fd, view) :]
            finally:
                os.close(target_fd)
            elf = elf_facts(fd, original["size"])
            require(
                written == original["size"] and metadata(os.fstat(fd)) == original, "source_drift"
            )
            check_fd = open_regular(root, source)
            try:
                require(metadata(os.fstat(check_fd)) == original, "source_drift")
            finally:
                os.close(check_fd)
            if source in (SHIM, payload):
                require(elf is not None and original["mode"] & 0o111, "executable_elf")
            if source == V3:
                require(digest.hexdigest() == hashlib.sha256(v3_raw).hexdigest(), "manifest_drift")
            if source == package + "/package.json":
                require(
                    digest.hexdigest() == hashlib.sha256(package_raw).hexdigest(), "package_drift"
                )
            if source == VERSIONS:
                require(
                    digest.hexdigest() == hashlib.sha256(versions.encode()).hexdigest(),
                    "versions_drift",
                )
            findings = []
            if original["nlink"] != 1:
                findings.append("original_hardlinked")
            if original["uid"] != 0 or original["mode"] & 0o022:
                findings.append("original_not_root_immutable")
            records.append(
                {
                    "source": source,
                    "destinations": sorted(destinations),
                    "copy": copied,
                    "sha256": digest.hexdigest(),
                    "original": original,
                    "aliases": [
                        alias
                        for destination in sorted(destinations)
                        for alias in aliases(root, destination)
                    ],
                    "findings": findings,
                    "elf": elf,
                }
            )
            total += written
        finally:
            os.close(fd)
    report = {
        "version": 1,
        "purpose": "installed-files-only-no-runtime-proof",
        "identity": identity,
        "files": records,
        "total_bytes": total,
        "omitted": omitted,
    }
    raw = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(len(raw) <= MAX_JSON, "report_limit")
    descriptor = os.open(
        output / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return report


def verify_export(output: Path, identity: dict) -> None:
    """Host-side exact artifact allowlist and byte reconciliation, after cleanup."""
    identity_checked(identity)
    raw = read_small(output, "/manifest.json", MAX_JSON)
    report = document(raw)
    require(
        set(report) == {"version", "purpose", "identity", "files", "total_bytes", "omitted"},
        "report_fields",
    )
    require(
        report["version"] == 1
        and type(report["version"]) is int
        and report["purpose"] == "installed-files-only-no-runtime-proof"
        and report["identity"] == identity,
        "report_identity",
    )
    records = report["files"]
    require(type(records) is list and 6 <= len(records) <= MAX_FILES, "report_files")
    metadata_keys = {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "mtime_ns",
        "ctime_ns",
    }
    sources = []
    total = 0
    for index, record in enumerate(records):
        require(
            type(record) is dict
            and set(record)
            == {
                "source",
                "destinations",
                "copy",
                "sha256",
                "original",
                "aliases",
                "findings",
                "elf",
            },
            "record_fields",
        )
        source = safe_path(record["source"])
        require(
            any(allowed(source, package) for package in PACKAGES) and source not in sources,
            "record_source",
        )
        sources.append(source)
        require(
            record["copy"] == f"files/{index:04d}.bin"
            and type(record["sha256"]) is str
            and _HEX.fullmatch(record["sha256"]),
            "record_copy",
        )
        require(
            type(record["original"]) is dict
            and set(record["original"]) == metadata_keys
            and all(type(n) is int and n >= 0 for n in record["original"].values()),
            "record_metadata",
        )
        destinations = record["destinations"]
        require(
            type(destinations) is list
            and 1 <= len(destinations) <= MAX_FILES
            and len(set(destinations)) == len(destinations),
            "record_destinations",
        )
        for destination in destinations:
            safe_path(destination)
            require(
                any(allowed(destination, package) for package in PACKAGES), "record_destination"
            )
        require(
            type(record["findings"]) is list
            and set(record["findings"]) <= {"original_hardlinked", "original_not_root_immutable"},
            "record_findings",
        )
        require(
            type(record["aliases"]) is list and len(record["aliases"]) <= MAX_FILES,
            "record_aliases",
        )
        for alias in record["aliases"]:
            require(
                type(alias) is dict and set(alias) == {"path", "target", "metadata"}, "alias_fields"
            )
            safe_path(alias["path"])
            require(
                type(alias["target"]) is str
                and len(alias["target"]) <= 512
                and all(32 <= ord(c) < 127 for c in alias["target"]),
                "alias_target",
            )
            require(
                type(alias["metadata"]) is dict
                and set(alias["metadata"]) == metadata_keys
                and all(type(n) is int and n >= 0 for n in alias["metadata"].values()),
                "alias_metadata",
            )
        elf = record["elf"]
        if elf is not None:
            require(
                type(elf) is dict
                and set(elf) == {"bits", "endian", "machine", "type", "interpreter", "needed"},
                "elf_fields",
            )
            require(
                all(type(elf[key]) is int for key in ("bits", "machine", "type"))
                and elf["bits"] in (32, 64)
                and elf["endian"] in ("little", "big"),
                "elf_types",
            )
            require(elf["interpreter"] is None or type(elf["interpreter"]) is str, "elf_types")
            require(
                type(elf["needed"]) is list
                and len(elf["needed"]) <= 128
                and all(
                    type(name) is str and re.fullmatch(r"[A-Za-z0-9_.+-]{1,512}", name)
                    for name in elf["needed"]
                ),
                "elf_types",
            )
        fd = open_regular(output, "/" + record["copy"])
        try:
            info = os.fstat(fd)
            require(
                info.st_nlink == 1
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and 0 <= info.st_size <= MAX_FILE
                and info.st_size == record["original"]["size"],
                "copy_metadata",
            )
            digest = hashlib.sha256()
            while chunk := os.read(fd, 65536):
                digest.update(chunk)
            require(
                digest.hexdigest() == record["sha256"]
                and elf_facts(fd, info.st_size) == record["elf"],
                "copy_bytes",
            )
            total += info.st_size
            require(total <= MAX_TOTAL, "byte_limit")
        finally:
            os.close(fd)
    require(
        {V3, VERSIONS, SHIM, CA} <= set(sources)
        and sum(source.endswith("/opencode-ai/package.json") for source in sources) == 1
        and sum(source.endswith("/opencode-ai/bin/opencode.exe") for source in sources) == 1,
        "report_population",
    )
    require(type(report["total_bytes"]) is int and report["total_bytes"] == total, "report_total")
    require(
        type(report["omitted"]) is list and len(report["omitted"]) <= MAX_FILES, "report_omitted"
    )
    for omitted in report["omitted"]:
        require(type(omitted) is dict, "omitted_fields")
        if omitted.get("reason") == "resolver_contents_excluded":
            require(
                omitted
                == {"destination": "/etc/resolv.conf", "reason": "resolver_contents_excluded"},
                "omitted_resolver",
            )
        else:
            require(
                set(omitted) == {"destination", "reason", "metadata"}
                and omitted["destination"] in PACKAGES
                and omitted["reason"] == "directory_not_exported",
                "omitted_directory",
            )
            require(
                type(omitted["metadata"]) is dict
                and set(omitted["metadata"]) == metadata_keys
                and all(type(n) is int and n >= 0 for n in omitted["metadata"].values()),
                "omitted_metadata",
            )
    require(
        {path.name for path in output.iterdir()} == {"manifest.json", "files"}
        and not (output / "files").is_symlink()
        and {path.name for path in (output / "files").iterdir()}
        == {f"{index:04d}.bin" for index in range(len(records))},
        "artifact_population",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("identity")
    prepare.add_argument("--output", type=Path, required=True)
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, choices=[Path("/export")], required=True)
    export.add_argument("--identity", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--identity-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "identity":
            value = prepare_identity()
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        elif args.command == "verify":
            value = identity_checked(document(args.identity_file.read_bytes()))
            signal.alarm(SECONDS)
            verify_export(args.output, value)
        else:
            value = identity_checked(document(args.identity.encode()))
            require(os.environ.get("GIT_SHA") == value["checkout_sha"], "image_checkout")
            require(
                hashlib.sha256(Path("/app", SCRIPT).read_bytes()).hexdigest()
                == value["exporter_sha256"]
                and hashlib.sha256(Path("/app", GENERATOR).read_bytes()).hexdigest()
                == value["generator_sha256"],
                "image_source",
            )
            signal.alarm(SECONDS)
            export_files(Path("/"), args.output, value)
        print("installed-image-export: PASS")
        return 0
    except (
        Refused,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        struct.error,
        subprocess.SubprocessError,
    ):
        # No arbitrary file contents, provider responses or child stderr in logs.
        print("installed-image-export: REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
