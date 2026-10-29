"""Exercise the actual peer-protected MCP boundary without a database service."""

import asyncio
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from butlers.core.approval_delivery_transport import RecoveryAuthorityError
from butlers.core.confirm_authority import ConfirmAuthorityTopology, ConfirmSource
from butlers.core.confirm_models import ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_service import ConfirmService

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux kernel-peer transport"),
]


def _prompt():
    return ConfirmPrompt(
        question="Was yesterday a work day?",
        options=(ConfirmOption("work", "Work"), ConfirmOption("not_work", "Not work")),
        recipient=Recipient(uuid4(), "42", "-10042", True),
    )


async def test_protected_producer_binds_source_and_retires_held_clients():
    prompt, confirm_id = _prompt(), uuid4()
    prepare = AsyncMock(return_value=(prompt, "trusted-goal-delivery"))
    other_prepare = AsyncMock()
    service = SimpleNamespace(_sinks={}, create=AsyncMock(return_value={"id": confirm_id}))
    app = SimpleNamespace(state=SimpleNamespace())
    forged = {
        "producer": "finance",
        "prepare": "finance.prepare",
        "sink": "other-sink",
        "goal_delivery_ref": "forged-ref",
        "recipient_chat": "attacker-chat",
    }
    # Kernel Unix-socket path limits apply to the complete path. Keep the owned
    # directory short independently of pytest's descriptive test directory.
    with TemporaryDirectory(prefix="cf-") as directory:
        topology = ConfirmAuthorityTopology(
            service,
            sources={"chronicler": ConfirmSource(prepare), "finance": ConfirmSource(other_prepare)},
            socket_directory=Path(directory),
            api_app=app,
        )
        with pytest.raises(RuntimeError, match="confirm_runtime_unavailable"):
            topology.producer("chronicler")
        async with asyncio.timeout(10), topology:
            producer = topology.producer("chronicler")
            assert app.state.confirm_service is service
            assert await producer.request("durable-key", forged) == {
                "status": "accepted",
                "confirm_id": str(confirm_id),
            }
            prepare.assert_awaited_once_with("durable-key", forged)
            other_prepare.assert_not_awaited()
            service.create.assert_awaited_once_with(
                "chronicler", "durable-key", prompt, goal_delivery_ref="trusted-goal-delivery"
            )

            # In-process tool invocation supplies no HTTP peer principal. JSON
            # claiming an issuer cannot replace the kernel-installed authority.
            async with Client(producer._endpoint._mcp) as client:
                with pytest.raises(ToolError, match="confirm_producer_unauthenticated"):
                    await client.call_tool("confirm.request", {"key": "forged", "data": forged})

            # A same-UID process still has a different kernel PID. Even forged
            # identity headers must be refused before HTTP/tool parsing.
            probe = r"""
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(2)
s.connect(sys.argv[1])
try:
    s.sendall(b'POST /mcp HTTP/1.1\r\nHost: local\r\nX-Butlers-Issuer: chronicler\r\nContent-Length: 2\r\n\r\n{}')
    refused = s.recv(4096) == b''
except (ConnectionResetError, BrokenPipeError):
    refused = True
finally:
    s.close()
sys.exit(0 if refused else 1)
"""
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                probe,
                str(producer._endpoint._socket_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            assert process.returncode == 0, stderr.decode()
            prepare.assert_awaited_once()
            service.create.assert_awaited_once()

        assert app.state.confirm_service is None
        assert await asyncio.to_thread(lambda: list(Path(directory).iterdir())) == []
        with pytest.raises(RuntimeError, match="confirm_runtime_unavailable"):
            topology.producer("chronicler")
        with pytest.raises(RecoveryAuthorityError):
            await producer.request("after-close", {})
        with pytest.raises(ValueError, match="unused closed registry"):
            async with topology:
                pytest.fail("a retired topology must not reopen")


async def test_closed_sink_rejects_unregistered_prompt_and_application_options():
    prompt = _prompt()
    prepare = AsyncMock(return_value=(prompt, "source-owned-ref"))
    apply = AsyncMock(return_value={"status": "applied"})
    pool = Mock()
    service = ConfirmService(pool, secret="synthetic-confirm-test", sinks={})
    # A configured sink with a closed vocabulary must reject a producer prompt
    # containing even one option outside it, before opening a DB connection.
    with TemporaryDirectory(prefix="cf-") as directory:
        topology = ConfirmAuthorityTopology(
            service,
            sources={
                "chronicler": ConfirmSource(
                    prepare,
                    sink_name="gap-answer",
                    values=frozenset({"work", "skip"}),
                    apply=apply,
                )
            },
            socket_directory=Path(directory),
        )
        async with asyncio.timeout(10), topology:
            with pytest.raises(ToolError, match="unregistered confirm producer/sink/options"):
                await topology.producer("chronicler").request("gap-key", {"options": ["work"]})
            pool.acquire.assert_not_called()
            registration = service._sinks["gap-answer"]
            assert registration.producer == "chronicler"
            confirm_id = uuid4()
            with pytest.raises(ToolError, match="confirm_option_unregistered"):
                await registration.apply(confirm_id, "source-owned-ref", "not_work")
            apply.assert_not_awaited()
            assert await registration.apply(confirm_id, "source-owned-ref", "work") == {
                "status": "applied"
            }
            apply.assert_awaited_once_with(confirm_id, "source-owned-ref", "work")
        assert service._sinks == {}
        with pytest.raises(RecoveryAuthorityError):
            await registration.apply(confirm_id, "source-owned-ref", "work")
        apply.assert_awaited_once()
