"""Scoped Noble CI-host userns experiment; never an application/runtime fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path

PACKAGE_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/main/a/apparmor/"
    "apparmor-profiles_4.0.1really4.0.1-0ubuntu0.24.04.9_all.deb"
)
PACKAGE_SHA256 = "90b02aa006eea7702cd4e851343e469e41365dda42145a3cb035de1d6c773b8c"
PROFILE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
PROFILE_MEMBER = "./usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
_SYSCTL = "kernel.apparmor_restrict_unprivileged_userns"
_PROFILE_NAMES = {"bwrap", "unpriv_bwrap"}
_LIMIT = 2 * 1024 * 1024
_PAYLOAD = b"""#include <fcntl.h>
#include <unistd.h>
int main(void) {
  if (getpid() != 1) return 42;
  int fd = open("/invocation/model-credential", O_RDONLY);
  if (fd < 0) return 31;
  close(fd); return 43;
}
"""


class Refusal(Exception):
    """Fixed refusal categories; other command output stays private.

    Fixed synthetic preflight logs separately include a digest and a preview of
    the first 512 stderr bytes, replacing control/non-ASCII bytes with question marks. Invocation
    and provider material never enters that command or its diagnostics.
    """


def failure_category(stderr: bytes) -> str:
    if b"setting up uid map: Permission denied" in stderr:
        return "uid_map_denied"
    if b"Creating new namespace failed: Operation not permitted" in stderr:
        return "namespace_denied"
    return "unknown_preflight_failure"


def preflight_diagnostic(stderr: bytes) -> str:
    # Only the fixed synthetic preflight: never invocation/provider material.
    # Bound bytes before decoding, and replace terminal/control/non-ASCII bytes.
    return "".join(chr(value) if 32 <= value <= 126 else "?" for value in stderr[:512])


def run(arguments: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess:
    # Commands and profile payload are fixed host-owned input; no invocation
    # credential/prompt/token is involved in this preflight.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=error,
            timeout=timeout,
            check=False,
        )
        if output.tell() > _LIMIT or error.tell() > _LIMIT:
            raise Refusal("command_output_limit")
        output.seek(0)
        error.seek(0)
        return subprocess.CompletedProcess(
            arguments, result.returncode, output.read(), error.read()
        )


def checked(arguments: list[str]) -> bytes:
    result = run(arguments)
    if result.returncode:
        raise Refusal("host_command_refused")
    return result.stdout.strip()


def restricted() -> None:
    if checked(["sysctl", "-n", _SYSCTL]) != b"1":
        raise Refusal("global_restriction_not_one")


def loaded() -> set[str]:
    raw = checked(["sudo", "-n", "cat", "/sys/kernel/security/apparmor/profiles"])
    return {line.split(" ", 1)[0] for line in raw.decode("ascii").splitlines()}


def profile_absent() -> None:
    if any("bwrap" in name for name in loaded()):
        raise Refusal("preloaded_profile_conflict")
    # Optional local includes would change the effective pinned policy.
    for name in ("bwrap-userns-restrict", "unpriv_bwrap"):
        path = Path("/etc/apparmor.d/local") / name
        if path.exists() or path.is_symlink():
            raise Refusal("local_profile_override_conflict")


def generic_denied() -> None:
    result = run(["unshare", "--user", "--map-root-user", "/usr/bin/true"])
    if result.returncode != 1 or b"Operation not permitted" not in result.stderr:
        raise Refusal("generic_userns_not_denied")


def verified_profile(package: bytes, archive: bytes) -> bytes:
    if hashlib.sha256(package).hexdigest() != PACKAGE_SHA256:
        raise Refusal("package_digest_mismatch")
    if len(archive) > _LIMIT:
        raise Refusal("package_archive_limit")
    with tarfile.open(fileobj=BytesIO(archive)) as files:
        matches = [item for item in files.getmembers() if item.name == PROFILE_MEMBER]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size != 1936:
            raise Refusal("invalid_profile_member")
        stream = files.extractfile(matches[0])
        if stream is None:
            raise Refusal("invalid_profile_member")
        profile = stream.read(1937)
    if hashlib.sha256(profile).hexdigest() != PROFILE_SHA256:
        raise Refusal("profile_digest_mismatch")
    return profile


def _write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def preflight(root: Path) -> subprocess.CompletedProcess:
    source = root / "preflight.c"
    program = root / "preflight"
    _write(source, _PAYLOAD)
    result = run(
        ["cc", "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(program)],
        timeout=30,
    )
    if result.returncode:
        raise Refusal("preflight_compile_failed")
    program.chmod(0o555)
    return run(
        [
            "/usr/bin/bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-net",
            "--disable-userns",
            "--assert-userns-disabled",
            "--ro-bind",
            str(program),
            "/program",
            "--",
            "/program",
        ]
    )


def repeat_preflight(root: Path) -> subprocess.CompletedProcess:
    # Each control compiles the identical source/flags in its own private root.
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return preflight(root)


def prepare(root: Path) -> None:
    if root.exists():
        raise Refusal("experiment_already_exists")
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    if 'VERSION_ID="24.04"' not in Path("/etc/os-release").read_text():
        raise Refusal("unsupported_runner_release")
    restricted()
    profile_absent()
    generic_denied()
    before = repeat_preflight(root / "before")
    category = failure_category(before.stderr)
    print(
        json.dumps(
            {
                "phase": "before_profile",
                "native_exit": before.returncode,
                "category": category,
                "stderr_sha256": hashlib.sha256(before.stderr).hexdigest(),
                "stderr_preview": preflight_diagnostic(before.stderr),
                "global_restriction": 1,
                "generic_userns": "denied",
            }
        )
    )
    if before.returncode != 1 or category == "unknown_preflight_failure":
        raise Refusal("unattributed_native_preflight")
    with urllib.request.urlopen(PACKAGE_URL, timeout=30) as response:
        package = response.read(_LIMIT + 1)
    if len(package) > _LIMIT or hashlib.sha256(package).hexdigest() != PACKAGE_SHA256:
        raise Refusal("package_digest_mismatch")
    package_path = root / "profile.deb"
    _write(package_path, package)
    archive = checked(["dpkg-deb", "--fsys-tarfile", str(package_path)])
    profile = verified_profile(package, archive)
    path = root / "bwrap-userns-restrict"
    _write(path, profile)
    # No kernel policy has changed yet. Do not replace (-r) an existing profile.
    restricted()
    profile_absent()
    _write(
        root / "load-intent.json", json.dumps({"version": 1, "profile": PROFILE_SHA256}).encode()
    )
    result = run(["sudo", "-n", "apparmor_parser", "-a", str(path)])
    if result.returncode:
        raise Refusal("profile_load_unproven")
    if not _PROFILE_NAMES.issubset(loaded()):
        raise Refusal("profile_load_unobserved")
    _write(root / "loaded.json", b'{"version":1}')
    restricted()
    generic_denied()
    after = repeat_preflight(root / "after")
    print(
        json.dumps(
            {
                "phase": "after_profile",
                "native_exit": after.returncode,
                "category": "payload_reached"
                if after.returncode == 31
                else failure_category(after.stderr),
                "stderr_sha256": hashlib.sha256(after.stderr).hexdigest(),
                "stderr_preview": preflight_diagnostic(after.stderr),
                "global_restriction": 1,
                "generic_userns": "denied",
            }
        )
    )
    if after.returncode != 31:
        raise Refusal("native_preflight_still_refused")
    restricted()
    print(
        json.dumps(
            {
                "status": "scoped_profile_proven",
                "before": category,
                "after_exit": 31,
                "global_restriction": 1,
                "generic_userns": "denied",
                "profile_sha256": PROFILE_SHA256,
            }
        )
    )


def cleanup(root: Path) -> None:
    if not root.exists() or not (root / "load-intent.json").exists():
        print('{"status":"no_profile_load_attempted"}')
        return
    path = root / "bwrap-userns-restrict"
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or hashlib.sha256(path.read_bytes()).hexdigest() != PROFILE_SHA256
    ):
        raise Refusal("cleanup_profile_identity_refused")
    # An unsuccessful add can be partial. We cannot prove ownership of a
    # conflicting profile after that outcome: quarantine the disposable runner,
    # refuse tests/green, and never remove uncertain foreign policy.
    if not (root / "loaded.json").exists():
        raise Refusal("cleanup_load_ownership_unproven")
    if not _PROFILE_NAMES.issubset(loaded()):
        raise Refusal("cleanup_owned_profiles_unobserved")
    checked(["sudo", "-n", "apparmor_parser", "-R", str(path)])
    if _PROFILE_NAMES & loaded():
        raise Refusal("profile_cleanup_unobserved")
    restricted()
    print('{"status":"scoped_profile_removed","global_restriction":1}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "cleanup"))
    options = parser.parse_args()
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("CI") != "true":
            raise Refusal("hosted_runner_required")
        parent = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
        root = parent / "butlers-session-host-userns"
        if options.operation == "prepare":
            prepare(root)
        else:
            cleanup(root)
        return 0
    except Refusal as error:
        print(json.dumps({"status": "refused", "reason": str(error)}))
    except Exception:
        print('{"status":"refused","reason":"host_experiment_failed"}')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
