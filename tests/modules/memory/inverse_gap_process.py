"""Native application generations for the inverse-gap PG compatibility tests.

No database is provisioned, restored, migrated or reset here. The caller supplies
its existing migrated test database over stdin, never argv or printed metadata.
Baseline source comes from local Git history when available. A shallow checkout
can reconstruct it offline from the two full historical modules only when every
other materialized application byte still matches the pinned baseline digest.
Changed support source refuses that reconstruction rather than mixing versions.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = Path(__file__).parent / "fixtures" / "inverse-gap-baseline"
_SOURCE = json.loads((_FIXTURE / "source.json").read_text())
_MODULES = tuple(_SOURCE["module_snapshots"])


def _package_digest(files: dict[str, bytes]) -> str:
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _materialize_source(generation: str, destination: Path) -> dict:
    """Pin a complete package before launching an interpreter or opening PG."""
    if generation not in {"baseline", "current"}:
        raise ValueError("unknown application generation")
    files = {}
    if generation == "baseline":
        archive = subprocess.run(
            ["git", "archive", _SOURCE["baseline_commit"], "src/butlers"],
            cwd=_ROOT,
            capture_output=True,
            timeout=10,
        )
        if archive.returncode == 0:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                for member in tar:
                    if member.isfile():
                        files[member.name] = tar.extractfile(member).read()
            source_form = "pinned-git-archive"
        else:
            files = {name: (_ROOT / name).read_bytes() for name in _SOURCE["source_paths"]}
            for name, snapshot in _SOURCE["module_snapshots"].items():
                files[name] = (_FIXTURE / snapshot["snapshot"]).read_bytes()
            source_form = "verified-offline-reconstruction"
        if _package_digest(files) != _SOURCE["package_sha256"]:
            raise AssertionError(
                "baseline support source unavailable; no mixed-generation fallback"
            )
        for name, snapshot in _SOURCE["module_snapshots"].items():
            assert (
                hashlib.sha256((_FIXTURE / snapshot["snapshot"]).read_bytes()).hexdigest()
                == (snapshot["sha256"])
            )
            assert hashlib.sha256(files[name]).hexdigest() == snapshot["sha256"]
    else:
        names = (
            subprocess.check_output(["git", "ls-files", "-z", "src/butlers"], cwd=_ROOT, timeout=10)
            .decode()
            .split("\0")
        )
        files = {name: (_ROOT / name).read_bytes() for name in names if name}
        source_form = "current-owning-checkout"
    for name, body in files.items():
        path = destination / name
        assert destination in path.resolve().parents
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return {
        "generation": generation,
        "source_form": source_form,
        "baseline_commit": _SOURCE["baseline_commit"] if generation == "baseline" else None,
        "package_sha256": _package_digest(files),
        "source_files": len(files),
        "module_sha256": {name: hashlib.sha256(files[name]).hexdigest() for name in _MODULES},
    }


async def run_source_child(generation: str, operation: str, **payload) -> dict:
    """Use a fresh isolated interpreter and reap it on failure or cancellation."""
    with tempfile.TemporaryDirectory(prefix="inverse-gap-generation-") as directory:
        source = Path(directory)
        identity = _materialize_source(generation, source)
        invocation = str(uuid.uuid4())
        bootstrap = (
            "import pathlib,runpy,sys; "
            "assert 'butlers' not in sys.modules; "
            "sys.path[:]=[sys.argv[1]]+[p for p in sys.path "
            "if not (pathlib.Path(p)/'butlers').is_dir()]; "
            "runpy.run_path(sys.argv[2],run_name='__main__')"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            bootstrap,
            str(source / "src"),
            str(Path(__file__).resolve()),
            cwd=source,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            request = dict(payload, operation=operation, source=identity, invocation=invocation)
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(request).encode()), timeout=30
            )
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
        # Never include child input or unfiltered connection errors in a failure.
        assert stdout, "application child exited without a sanitized result"
        result = json.loads(stdout)
        assert result["invocation"] == invocation
        assert result["pid"] == process.pid and result["pid"] != os.getpid()
        assert result["source"] == identity
        assert process.returncode in {0, 1}, result.get("error_type", "child failure")
        result["exit_code"] = process.returncode
        return result


class _DeterministicEmbeddings:
    """Match the native cache identity and complete 384-vector interface."""

    _model_name = "test-model"
    _dim = 384

    @property
    def model_name(self):
        return self._model_name

    @property
    def dimension(self):
        return self._dim

    def embed(self, _text):
        return [0.1] * self.dimension

    def embed_batch(self, texts):
        return [self.embed(text) for text in texts]


@contextlib.contextmanager
def _deterministic_memory_module():
    """Exercise the real getter before PG; refuse any paid factory throughout."""
    from unittest.mock import patch

    from butlers.modules.memory import MemoryModule, MemoryModuleConfig, tools
    from butlers.modules.memory.embedding import _EMBEDDING_DIM

    module = MemoryModule()
    module._config = MemoryModuleConfig(embedding_model="test-model", groups=["core"])
    engine = _DeterministicEmbeddings()
    module._embedding_engine = engine
    with patch.object(
        tools, "get_embedding_engine", side_effect=AssertionError("unexpected-embedding-factory")
    ) as factory:
        assert module._get_embedding_engine() is engine
        assert engine.model_name == engine._model_name == module._config.embedding_model
        assert engine.dimension == _EMBEDDING_DIM == 384
        vector = engine.embed("fixture text")
        assert len(vector) == engine.dimension and all(type(value) is float for value in vector)
        assert any(vector)  # The migrated cosine-vector fixture rejects zero vectors.
        assert engine.embed_batch(["first", "second"]) == [vector, vector]
        assert engine.embed_batch([]) == []
        contract = {
            "model_name": engine.model_name,
            "dimension": engine.dimension,
            "real_getter_retained_engine": True,
            "factory_requests": factory.call_count,
            "heavy_model_modules_loaded": [],
        }
        assert factory.call_count == 0
        assert not any(
            name.startswith(("sentence_transformers", "transformers", "torch"))
            for name in sys.modules
        )
        try:
            yield module, contract
            assert module._get_embedding_engine() is engine
            assert factory.call_count == 0
        finally:
            contract["factory_requests"] = factory.call_count
            contract["heavy_model_modules_loaded"] = [
                name
                for name in sys.modules
                if name.startswith(("sentence_transformers", "transformers", "torch"))
            ]
        assert not contract["heavy_model_modules_loaded"]


async def _write(pool, request, module):
    """Call the real registered Memory writer with the prevalidated service seam."""
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.core.tool_call_capture import (
        clear_runtime_session_routing_context,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
        set_runtime_session_routing_context,
    )

    mcp = FastMCP("inverse-gap-application-generation")
    await module.register_tools(
        mcp,
        module._config,
        SimpleNamespace(pool=pool, schema="public"),
        "kgtest",
    )
    writer = (await mcp.get_tool("memory_store_fact")).fn
    session_id = str(uuid.uuid4())
    set_runtime_session_routing_context(
        session_id,
        {
            "source_entity_id": request["owner"],
            "request_context": {"source_channel": "telegram"},
        },
    )
    token = set_current_runtime_session_id(session_id)
    try:
        stored = await writer(
            subject="Mei",
            content="Hana",
            predicate=request["forward"],
            entity_id=request["left"],
            object_entity_id=request["right"],
        )
        assert "error" not in stored, "registered native writer refused the fixture edge"
        return stored["id"]
    finally:
        reset_current_runtime_session_id(token)
        clear_runtime_session_routing_context(session_id)


async def _snapshot(pool, request):
    from butlers.modules.memory import knowledge_gaps

    async with pool.acquire() as conn:
        role = dict(await conn.fetchrow("SELECT current_user AS role, current_schema() AS schema"))
        inverse = await conn.fetchrow(
            "SELECT * FROM facts WHERE entity_id=$1 AND object_entity_id=$2"
            " AND predicate=$3 AND validity='active' ORDER BY created_at DESC LIMIT 1",
            uuid.UUID(request["right"]),
            uuid.UUID(request["left"]),
            request["reverse"],
        )
        assert inverse is not None, "real canonical inverse row must exist"
        gap = await conn.fetchrow(
            "SELECT * FROM knowledge_gaps WHERE id=$1", uuid.UUID(request["gap"])
        )
    listed = await knowledge_gaps.list_gaps(
        pool, statuses=("open", "answerable", "delivered"), limit=200
    )
    [view] = [row for row in listed if str(row["id"]) == request["gap"]]
    return {
        "database_role": role,
        "inverse_id": str(inverse["id"]),
        "inverse_value": inverse["content"],
        "inverse_authority": inverse["content_authority"],
        "inverse_authority_entity_id": str(inverse["authority_entity_id"]),
        "gap_status": gap["status"],
        "answered_by_ref": gap["answered_by_ref"],
        "answered_value": gap["answered_value"],
        "coverage_state": view["coverage_state"],
    }


async def _operate(request, module):
    import asyncpg

    from butlers.api.conversations import conversation_reply_create
    from butlers.db import register_jsonb_codec
    from butlers.modules.memory import knowledge_gaps

    pool = await asyncpg.create_pool(
        request["database_url"],
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        timeout=5,
        command_timeout=8,
    )
    try:
        if request["operation"] == "write":
            forward_id = await _write(pool, request, module)
            return dict(await _snapshot(pool, request), forward_id=forward_id)
        before = await _snapshot(pool, request)

        async def post(conversation_id, text):
            return await conversation_reply_create(pool, conversation_id, message=text) is not None

        await knowledge_gaps.deliver_knowledge_gaps(pool, post=post, origin_butler="kgtest")
        after = await _snapshot(pool, request)
        result = {"before": before, "after": after}
        if request.get("future"):
            future = dict(request, **request["future"])
            forward_id = await _write(pool, future, module)
            result["future"] = dict(await _snapshot(pool, future), forward_id=forward_id)
        return result
    finally:
        await pool.close()


def _verify_loaded_sources(source_root, identity):
    from butlers.modules.memory import knowledge_gaps, storage

    for module in (storage, knowledge_gaps):
        path = Path(module.__file__).resolve()
        assert source_root in path.parents
        name = "src/" + path.relative_to(source_root).as_posix()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == identity["module_sha256"][name]
    for name, module in tuple(sys.modules.items()):
        if name.startswith("butlers") and getattr(module, "__file__", None):
            assert source_root in Path(module.__file__).resolve().parents


def _main():
    request = json.loads(sys.stdin.buffer.read())
    result = {"invocation": request["invocation"], "pid": os.getpid(), "source": request["source"]}
    try:
        source_root = Path(sys.argv[1]).resolve()
        _verify_loaded_sources(source_root, request["source"])
        with _deterministic_memory_module() as (module, contract):
            result["embedding_seam"] = contract
            if request["operation"] != "probe":
                result["state"] = asyncio.run(_operate(request, module))
        _verify_loaded_sources(source_root, request["source"])
        # A real pre-fix write commits both rows before this exact invariant fails.
        if request.get("require_answerable"):
            assert result["state"]["gap_status"] == "answerable", "object-gap-closure"
        result["verdict"] = "PASS"
        exit_code = 0
    except AssertionError as exc:
        if str(exc) == "object-gap-closure":
            result.update(verdict="RED", invariant="object-gap-closure")
            exit_code = 1
        else:
            result.update(verdict="ERROR", error_type="AssertionError")
            exit_code = 2
    except Exception as exc:
        result.update(
            verdict="ERROR", error_type=type(exc).__name__, sqlstate=getattr(exc, "sqlstate", None)
        )
        exit_code = 2
    print(json.dumps(result, default=str))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    _main()
