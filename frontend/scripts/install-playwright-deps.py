"""Finite privileged dependency group; only cleaned deadline expiry permits recovery.

80/81/82/83 mean cleaned success/failure/deadline/cancellation. Every other
outcome is unproven. Cancellation (including EOF) never authorizes retry.
"""

from __future__ import annotations

import ctypes
import os
import re
import select
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLEANUP_SECONDS = 2
CLEANED_SUCCESS = 80
CLEANED_FAILURE = 81
CLEANED_TIMEOUT = 82
CLEANED_CANCELLED = 83
UNPROVEN = 125
APT_ROOT = Path("/etc/apt")
OS_RELEASE = Path("/usr/lib/os-release")
APT_CONFIG_READER = "/usr/bin/apt-config"
TRUSTED_UID = 0
MAX_FILE_BYTES = 65536
MAX_SOURCE_FILES = 64
MAX_CONFIG_BYTES = 1048576
MIRROR_URI = "mirror+file:/etc/apt/apt-mirrors.txt"
MIRRORS = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1",
    "https://archive.ubuntu.com/ubuntu/\tpriority:2",
    "https://security.ubuntu.com/ubuntu/\tpriority:3",
)


def read_owned_file(path: Path) -> bytes:
    """Fixed trusted inputs only; do not follow links or print their contents."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != TRUSTED_UID
            or metadata.st_mode & 0o022
            or metadata.st_size > MAX_FILE_BYTES
        ):
            raise ValueError("unsupported apt input ownership/type/size")
        content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES or b"\0" in content:
            raise ValueError("unsupported apt input size/content")
        return content


def trusted_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != TRUSTED_UID or metadata.st_mode & 0o022:
            raise ValueError("unsupported apt directory ownership")
    finally:
        os.close(descriptor)


def ubuntu_sources(content: bytes, mirror: Path) -> bytes:
    """Transform only unambiguous, known noble Ubuntu deb822 URI fields."""
    text = content.decode("utf-8")
    replacements = 0
    for stanza in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            if line.startswith("#"):
                continue
            key, separator, value = line.partition(":")
            key = key.lower()
            if (
                not separator
                or key in fields
                or key
                not in {
                    "types",
                    "uris",
                    "suites",
                    "components",
                    "architectures",
                    "signed-by",
                    "enabled",
                }
            ):
                raise ValueError("unsupported Ubuntu deb822 field")
            fields[key] = value.strip()
        if not fields:  # Preserve standalone comment paragraphs verbatim.
            continue
        if (
            fields.get("uris") != MIRROR_URI
            or fields.get("signed-by") != "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
            or not set(fields.get("types", "").split()) <= {"deb", "deb-src"}
            or "deb" not in fields.get("types", "").split()
            or not fields.get("suites")
            or not set(fields["suites"].split())
            <= {"noble", "noble-updates", "noble-backports", "noble-security"}
            or not fields.get("components")
            or not set(fields["components"].split())
            <= {"main", "restricted", "universe", "multiverse"}
            or fields.get("enabled", "yes") != "yes"
        ):
            raise ValueError("unsupported Ubuntu source/trust/layout")
        replacements += 1
    transformed, count = re.subn(
        r"(?im)^(URIs:[ \t]*)" + re.escape(MIRROR_URI) + r"([ \t]*)(?=\n|$)",
        lambda match: match[1] + "mirror+file:" + str(mirror) + match[2],
        text,
    )
    if not replacements or count != replacements:
        raise ValueError("ambiguous Ubuntu URI")
    return transformed.encode()


def prepare_recovery(owned: Path) -> None:
    """No host writes; same candidates, trust, package population and APT state."""
    trusted_directory(APT_ROOT)
    trusted_directory(APT_ROOT / "sources.list.d")
    release = read_owned_file(OS_RELEASE).decode()
    values = dict(line.split("=", 1) for line in release.splitlines() if "=" in line)
    if (
        values.get("ID", "").strip('"') != "ubuntu"
        or values.get("VERSION_ID", "").strip('"') != "24.04"
        or values.get("VERSION_CODENAME", "").strip('"') != "noble"
        or os.environ.get("APT_CONFIG")
    ):
        raise ValueError("unsupported recovery platform/config")
    candidates = read_owned_file(APT_ROOT / "apt-mirrors.txt").decode().splitlines()
    if tuple(candidates) != MIRRORS:
        raise ValueError("unsupported apt mirror candidates/metadata")
    # Only this nonsecret mirror list is sandbox-readable. Copied source files
    # may contain credentials and stay root-private, including their directory.
    owned.chmod(0o711)
    mirror = owned / "mirrors.txt"
    mirror.write_text(
        "\n".join(
            (
                MIRRORS[1].replace("priority:2", "priority:1"),
                MIRRORS[0].replace("priority:1", "priority:2"),
                MIRRORS[2],
            )
        )
        + "\n"
    )
    mirror.chmod(0o644)
    private = owned / "private"
    private.mkdir(mode=0o700)
    parts = private / "sources.list.d"
    parts.mkdir(mode=0o700)
    entries = []
    for entry in (APT_ROOT / "sources.list.d").iterdir():
        if len(entries) >= MAX_SOURCE_FILES:
            raise ValueError("unsupported apt source population size")
        entries.append(entry)
    if not any(p.name == "ubuntu.sources" for p in entries):
        raise ValueError("unsupported apt source population")
    total = 0
    for entry in sorted(entries):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:list|sources)", entry.name):
            raise ValueError("unsupported apt source filename")
        content = read_owned_file(entry)
        total += len(content)
        if total > MAX_CONFIG_BYTES:
            raise ValueError("unsupported apt source population size")
        if entry.name == "ubuntu.sources":
            content = ubuntu_sources(content, mirror)
        elif MIRROR_URI.encode() in content:
            raise ValueError("ambiguous Ubuntu source location")
        target = parts / entry.name
        target.write_bytes(content)
        target.chmod(0o600)
    source = APT_ROOT / "sources.list"
    content = read_owned_file(source) if source.exists() or source.is_symlink() else b""
    if MIRROR_URI.encode() in content:
        raise ValueError("unsupported one-line Ubuntu layout")
    (private / "sources.list").write_bytes(content)
    (private / "sources.list").chmod(0o600)
    (private / "apt.conf").write_text(
        f'Dir::Etc::sourcelist "{private / "sources.list"}";\nDir::Etc::sourceparts "{parts}";\n'
    )
    (private / "apt.conf").chmod(0o600)


def apt_configuration(environment: dict[str, str]) -> list[bytes]:
    # This reader is in the SAME owned process group and attempt deadline as
    # install-deps. Its potentially secret output never reaches diagnostics.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [APT_CONFIG_READER, "dump"],
            env=environment,
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        output.seek(0)
        content = output.read(MAX_CONFIG_BYTES + 1)
    if result.returncode or len(content) > MAX_CONFIG_BYTES:
        raise ValueError("apt configuration reader failed")
    return content.splitlines()


def recovery_child(owned: Path, cli: str) -> None:
    original = dict(os.environ)
    recovered = {**original, "APT_CONFIG": str(owned / "private/apt.conf")}
    before, after = apt_configuration(original), apt_configuration(recovered)
    keys = (b"dir::etc::sourcelist ", b"dir::etc::sourceparts ")
    for line in before + after:
        key = line.split(b" ", 1)[0].lower()
        if (key.startswith(b"binary::") and (b"::dir" in key or b"::rootdir" in key)) or (
            key == b"rootdir" and line.split(b" ", 1)[1] != b'"";'
        ):
            # apt-config and apt-get have different binary overlays. Refuse a
            # layout whose source/state authority cannot be proved by this dump.
            raise ValueError("unsupported binary/root apt path override")

    def ordinary(lines: list[bytes]) -> list[bytes]:
        return [line for line in lines if not line.lower().startswith(keys)]

    if ordinary(before) != ordinary(after):
        raise ValueError("recovery changed ordinary apt configuration")
    for key, value in zip(keys, ("sources.list", "sources.list.d"), strict=True):
        selected = [line for line in after if line.lower().startswith(key)]
        if len(selected) != 1 or selected[0].split(b" ", 1)[1] != (
            f'"{owned / "private" / value}";'.encode()
        ):
            raise ValueError("effective apt source override refused")
    # apt itself reads the preserved deb822 sources during the unchanged CLI
    # install. The real reader above proves later config did not undo isolation.
    os.execvpe(cli, [cli, "install-deps", "chromium"], recovered)


def main() -> int:
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        print("Cannot establish dependency cleanup ownership", file=sys.stderr)
        return UNPROVEN
    term_seconds, kill_seconds = (float(value) / 1000 for value in sys.argv[1:3])
    profile = sys.argv[5]
    cancelled = False

    def cancel(*_args: object) -> None:
        nonlocal cancelled
        cancelled = True

    def interrupted() -> bool:
        nonlocal cancelled
        if select.select([sys.stdin], [], [], 0)[0]:
            os.read(sys.stdin.fileno(), 4096)
            cancelled = True
        return cancelled

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, cancel)
    child = None
    selector = None
    overlay = None
    outcome = UNPROVEN
    cleaned = False
    started = time.monotonic()
    try:
        # Lifetime starts before every setup step and is never restarted.
        if profile not in {"runner-default", "official-ubuntu-recovery"}:
            raise ValueError("unsupported dependency profile")
        if not interrupted() and time.monotonic() - started < term_seconds:
            if profile == "official-ubuntu-recovery":
                overlay = Path(tempfile.mkdtemp(prefix="butlers-apt-"))
                prepare_recovery(overlay)
        if interrupted():
            outcome = CLEANED_CANCELLED
        elif time.monotonic() - started >= term_seconds:
            outcome = CLEANED_TIMEOUT
        else:
            node_directory = os.path.dirname(sys.argv[3])
            command = (
                [sys.argv[4], "install-deps", "chromium"]
                if overlay is None
                else [sys.executable, __file__, "--recovery-child", str(overlay), sys.argv[4]]
            )
            child = subprocess.Popen(
                command,
                start_new_session=True,
                env={**os.environ, "PATH": node_directory + os.pathsep + os.environ["PATH"]},
            )
            selector = selectors.DefaultSelector()
            selector.register(sys.stdin, selectors.EVENT_READ)
            termed = False
            while True:
                # Cancellation wins even when the leader exited concurrently.
                if interrupted():
                    outcome = CLEANED_CANCELLED
                    break
                elapsed = time.monotonic() - started
                if elapsed >= kill_seconds:
                    outcome = CLEANED_TIMEOUT
                    break
                # WNOWAIT reserves PID/PGID until the final group signal.
                status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if status is not None and not termed:
                    outcome = (
                        CLEANED_SUCCESS
                        if status.si_code == os.CLD_EXITED and status.si_status == 0
                        else CLEANED_FAILURE
                    )
                    break
                if not termed and elapsed >= term_seconds:
                    termed = True
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                selector.select(0.02)
    except Exception as error:
        print(f"Playwright dependency supervisor failed: {type(error).__name__}", file=sys.stderr)
    finally:
        if child is not None:
            group = child.pid
            try:
                os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + CLEANUP_SECONDS
            while True:
                child.poll()
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    cleaned = True
                    break
                if pid == 0:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
        else:
            cleaned = True
        if selector is not None:
            selector.close()
        if overlay is not None and cleaned:
            shutil.rmtree(overlay)
    if interrupted():
        outcome = CLEANED_CANCELLED
    reported_profile = (
        profile if profile in {"runner-default", "official-ubuntu-recovery"} else "unsupported"
    )
    print(
        f"Playwright dependencies profile={reported_profile} "
        f"outcome={outcome if cleaned else UNPROVEN} elapsed={time.monotonic() - started:.2f}s",
        file=sys.stderr,
    )
    if not cleaned or outcome == UNPROVEN:
        print(
            "Playwright dependency cleanup could not be confirmed; refusing retry", file=sys.stderr
        )
        return UNPROVEN
    return outcome


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--recovery-child":
        try:
            recovery_child(Path(sys.argv[2]), sys.argv[3])
        except Exception:
            print("Playwright private apt configuration refused", file=sys.stderr)
            raise SystemExit(1) from None
    else:
        raise SystemExit(main())
