"""Explicit default-off entry point; no implicit environment or secret resolver."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path

from butlers.session_host.control import TLSFiles, _unique_object
from butlers.session_host.egress import GatewayConfig, start_gateway
from butlers.session_host.launch import Refused
from butlers.session_host.service import HostConfig, prepare_auth_volume, stage_proofs, start_host


def _configuration(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            content = stream.read(8193)
        if len(content) > 8192:
            raise ValueError
        value = json.loads(content, object_pairs_hook=_unique_object)
        if type(value) is not dict:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Refused("invalid_service_config") from None


def _host(value: dict) -> HostConfig:
    keys = {"listen_host", "binary", "broker_url", "proof_index", "tls"}
    if (
        not keys <= set(value)
        or set(value) - keys - {"model_function_binary", "cli_auth_enabled", "credential_volume"}
        or any(type(value[k]) is not str for k in keys - {"tls"})
        or ("model_function_binary" in value and type(value["model_function_binary"]) is not str)
        or ("cli_auth_enabled" in value and type(value["cli_auth_enabled"]) is not bool)
        or ("credential_volume" in value and type(value["credential_volume"]) is not str)
    ):
        raise Refused("invalid_service_config")
    tls = value["tls"]
    if (
        type(tls) is not dict
        or set(tls) != {"ca", "certificate", "private_key"}
        or any(type(v) is not str for v in tls.values())
    ):
        raise Refused("invalid_service_config")
    return HostConfig(
        enabled=True,
        listen_host=value["listen_host"],
        binary=Path(value["binary"]),
        broker_url=value["broker_url"],
        proof_index=Path(value["proof_index"]),
        tls=TLSFiles(**{k: Path(v) for k, v in tls.items()}),
        model_function_binary=(
            Path(value["model_function_binary"]) if "model_function_binary" in value else None
        ),
        cli_auth_enabled=value.get("cli_auth_enabled", False),
        credential_volume=Path(value["credential_volume"])
        if "credential_volume" in value
        else None,
    )


def _gateway(value: dict) -> GatewayConfig:
    keys = {
        "internal_address",
        "control_address",
        "dns_address",
        "providers",
        "broker_host",
        "host_target",
    }
    if (
        set(value) != keys
        or type(value["providers"]) is not list
        or any(type(v) is not str for v in value["providers"])
        or len(set(value["providers"])) != len(value["providers"])
        or any(type(value[k]) is not str for k in keys - {"providers"})
    ):
        raise Refused("invalid_service_config")
    return GatewayConfig(enabled=True, **(value | {"providers": frozenset(value["providers"])}))


async def run(*, kind: str, enabled: bool, config: Path | None):
    if not enabled:
        return  # No file, environment, platform, credential, listener or worker access.
    if config is None:
        raise Refused("invalid_service_config")
    if kind == "prepare-auth-volume":
        await asyncio.to_thread(prepare_auth_volume, config)
        return
    if kind == "stage-proofs":
        await asyncio.to_thread(stage_proofs, config, Path("/proofs"))
        return
    value = _configuration(config)
    if kind == "host":
        running = await start_host(_host(value))
    else:
        running = await start_gateway(_gateway(value))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        # Repeated TERM only sets the event; it cannot interrupt resource settlement.
        try:
            await running.close()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("host", "gateway", "stage-proofs", "prepare-auth-volume"))
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(run(kind=args.kind, enabled=args.enable, config=args.config))
    except Refused as exc:
        parser.exit(1, f"session host refused: {exc}\n")
    except (OSError, ValueError):
        parser.exit(1, "session host refused: service_start_failed\n")


if __name__ == "__main__":
    main()
