"""Run the Codex adapter proof inside the exact production image.

Not a pytest module: the app image has no dev dependencies. The owner's
user-run payload mounts this directory read-only into the rebuilt image and
runs it with the image's own Python, bwrap and Codex binary:

    python exact_image_codex_proof.py <codex binary> <image shim> <output.json>

The container runs under the proof-only seccomp profile, mounted from the repo:
``--security-opt seccomp=deploy/seccomp/session-host-proof.json``, never
``seccomp=unconfined``. That is the session host's pod profile
(``session-host-sandbox.json``, which allows no ``setns``) plus exactly two ``setns``
rules, for user and network namespaces, which only this proof's listener helper needs.
It still needs ``apparmor=unconfined`` and
``systempaths=unconfined``, because Bubblewrap's ``--proc`` cannot mount over masked
paths (``deploy/seccomp/README.md``).

The shim is the image's own ``/usr/local/libexec/butlers/runtime-cli-sandbox-init``,
statically linked by ``Dockerfile.base``; a dynamic shim cannot exec in the domain's
empty root, so the run refuses one rather than substitute a build from outside the image.

It runs the clean launch on both transports and every row of
``codex_proof.MUTATIONS``, and exits 0 only when the clean runs pass every
measured check and each mutation fails the check it names. Everything is
synthetic: no provider, no network, no secrets.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_proof  # noqa: E402


def shim_is_static(path: Path) -> bool:
    """True only for a well-formed x86-64 ELF executable with no interpreter or dynamic section.

    Anything malformed, truncated or of another shape is False and never raises, so the check
    cannot pass a file it could not read.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return False  # ELF64, little endian
    u16 = lambda at: int.from_bytes(data[at : at + 2], "little")  # noqa: E731
    phoff = int.from_bytes(data[32:40], "little")
    entry, count = u16(54), u16(56)
    if u16(16) != 2 or u16(18) != 62 or entry != 56 or count < 1:
        return False  # ET_EXEC, EM_X86_64, 56-byte program headers, at least one
    if phoff + count * entry > len(data):
        return False
    kinds = {
        int.from_bytes(data[phoff + i * entry : phoff + i * entry + 4], "little")
        for i in range(count)
    }
    return not kinds & {2, 3}  # PT_DYNAMIC, PT_INTERP


def _summary(observation: dict) -> dict:
    failures = codex_proof.evaluate(observation)
    return {
        "exit": observation.get("exit"),
        "proof_error": observation.get("proof_error"),
        "failed": {check: reasons for check, reasons in failures.items() if reasons},
        "isolation": observation.get("isolation"),
        "settled": observation.get("settled"),
        "stage_empty": observation.get("stage_empty"),
        "stderr_tail": observation.get("stderr_tail", "")[-600:],
    }


def main(codex: Path, shim: Path, output: Path) -> int:
    if not shim_is_static(shim):
        print(f"RESULT shim={shim} static=false", flush=True)
        return 2
    digest = hashlib.sha256(codex.read_bytes()).hexdigest()
    if digest != codex_proof.PINNED_CODEX_SHA256:
        print(f"RESULT codex_sha256={digest} pinned=false", flush=True)
        return 2
    root = Path(tempfile.mkdtemp(prefix="codex-proof-"))
    images = root / "image"
    images.mkdir()
    shutil.copyfile(codex, images / "codex")
    shutil.copyfile(shim, images / "init")
    (images / "codex").chmod(0o555)
    (images / "init").chmod(0o555)
    rows: dict[str, tuple[dict, str | None]] = {
        "clean-websocket": ({}, None),
        "clean-http": ({"refuse_websocket": True}, None),
        **codex_proof.MUTATIONS,
    }
    results: dict[str, dict] = {}
    ok = True
    for name, (scenario, expected) in rows.items():
        work = root / name
        work.mkdir()
        summary = _summary(codex_proof.run(scenario, images, work))
        if expected is None:
            summary["as_expected"] = (
                not summary["failed"] and summary["settled"] and summary["stage_empty"]
            )
        elif expected in codex_proof.ISOLATION_CHECKS:
            # Must fail on a measured view, not because the launch broke.
            summary["as_expected"] = (
                expected in summary["failed"]
                and summary["exit"] == "completed"
                and (summary["isolation"] or {}).get("measured") is True
            )
        else:
            summary["as_expected"] = expected in summary["failed"]
        ok = ok and summary["as_expected"]
        results[name] = summary
        print(f"row {name}: {'ok' if summary['as_expected'] else 'UNEXPECTED'}", flush=True)
    output.write_text(json.dumps({"codex_sha256": digest, "rows": results}, indent=1))
    shutil.rmtree(root, ignore_errors=True)
    bad = sorted(name for name, row in results.items() if not row["as_expected"])
    print(f"RESULT codex_proof={'pass' if ok else 'fail'} rows={len(rows)} unexpected={bad}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
