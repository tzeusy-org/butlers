"""Contracts for the image-owned Dashboard CLI sandbox input manifest."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_SCRIPT = Path("scripts/generate_runtime_cli_sandbox_manifest.py")


def _manifest_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_cli_sandbox_manifest", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_declares_only_the_registered_dashboard_runtime_closures(
    monkeypatch,
) -> None:
    """REQ-core-credentials-002: no provider can fall back to a host runtime tree."""
    module = _manifest_module()
    monkeypatch.setattr(module, "_safe_regular_or_directory", lambda path: path)

    def _resolve(binary: str) -> Path:
        return Path(f"/image/bin/{binary}")

    def _closure(
        executable: Path,
        package: Path,
    ) -> tuple[object, ...]:
        return (
            module.RuntimeInputBinding(source=executable, destination=executable),
            module.RuntimeInputBinding(source=package, destination=package),
            module.RuntimeInputBinding(
                source=Path("/image/resolv.conf"),
                destination=Path("/etc/resolv.conf"),
            ),
        )

    document = module.build_manifest(
        npm_root=lambda: Path("/image/node_modules"),
        resolve_command=_resolve,
        runtime_closure=_closure,
    )

    assert document["version"] == 2
    providers = document["providers"]
    assert set(providers) == {"codex", "opencode-openai", "opencode-go"}
    assert providers["codex"] == {
        "binary": "codex",
        "executable": "/image/bin/codex",
        "readonly_inputs": [
            {"destination": "/image/bin/codex", "source": "/image/bin/codex"},
            {
                "destination": "/image/node_modules/@openai/codex",
                "source": "/image/node_modules/@openai/codex",
            },
            {"destination": "/etc/resolv.conf", "source": "/image/resolv.conf"},
        ],
    }
    assert providers["opencode-openai"]["executable"] == "/image/bin/opencode"
    assert providers["opencode-go"]["readonly_inputs"] == [
        {"destination": "/image/bin/opencode", "source": "/image/bin/opencode"},
        {
            "destination": "/image/node_modules/opencode-ai",
            "source": "/image/node_modules/opencode-ai",
        },
        {"destination": "/etc/resolv.conf", "source": "/image/resolv.conf"},
    ]


def test_generator_writes_a_non_group_or_world_writable_manifest(tmp_path: Path) -> None:
    """REQ-core-credentials-002: image runtime input discovery is immutable at rest."""
    module = _manifest_module()
    output = tmp_path / "runtime-cli-sandbox-inputs.json"

    module.write_manifest(output, {"version": 1, "providers": {}})

    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert output.read_text(encoding="utf-8") == '{"providers":{},"version":1}\n'


def test_generator_preserves_logical_elf_loader_paths_for_the_empty_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """An ELF interpreter path must remain mountable at the path it names."""
    module = _manifest_module()
    real_loader = tmp_path / "real-loader"
    real_loader.write_bytes(b"test-only")
    logical_loader = tmp_path / "lib64" / "ld-linux-test.so"
    logical_loader.parent.mkdir()
    logical_loader.symlink_to(real_loader)

    monkeypatch.setattr(module, "_safe_regular_or_directory", lambda path: path)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"\t{logical_loader} (0x00000000)\n",
        ),
    )

    assert module._ldd_dependencies(Path("/image/bin/runtime-cli")) == (
        module.RuntimeInputBinding(source=real_loader, destination=logical_loader),
    )
