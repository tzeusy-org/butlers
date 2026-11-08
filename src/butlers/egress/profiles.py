"""Reviewed workload egress profiles (RFC 0038 S7) and their chart rendering.

``deploy/egress-profiles.toml`` is the only place egress is declared. This module parses
and validates it, failing closed on anything it does not recognise, and renders it to
``deploy/helm/butlers/files/egress-profiles.json`` for the chart and the shared gateway.

Run ``python -m butlers.egress.profiles`` to re-render and ``--check`` to verify the
committed rendering is current. Neither touches a cluster.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "butlers-egress-profiles/v1"
_REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILES_PATH = _REPO_ROOT / "deploy" / "egress-profiles.toml"
RENDERED_PATH = _REPO_ROOT / "deploy" / "helm" / "butlers" / "files" / "egress-profiles.json"

# A Kubernetes label value, as the chart's app.kubernetes.io/component carries it.
_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
# The last label must be alphabetic, which also rules out every IPv4 literal.
_TLD = re.compile(r"^[a-z]{2,63}$")
# Names that resolve to cluster, LAN or tailnet addresses never go through the gateway.
_PRIVATE_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home.arpa",
    ".svc",
    ".cluster.local",
    ".ts.net",
    ".invalid",
    ".test",
    ".example",
)
_PROTOCOLS = frozenset({"TCP", "UDP"})
# The shared gateway's component. Reaching it is granted only through gateway_port, so
# no profile may name it as a peer (that would borrow another workload's identity) or
# declare it as a workload.
GATEWAY_COMPONENT = "egress-gateway"
_WORKLOAD_KEYS = frozenset(
    {"summary", "held", "gateway_port", "internal", "sites", "external", "dynamic"}
)
_ROUTE_KEYS = frozenset({"gateway_port", "internal", "sites", "external"})


class ProfileError(ValueError):
    """The profile file is invalid; nothing it declares may be rendered."""


@dataclass(frozen=True, slots=True)
class InternalPeer:
    component: str
    port: int
    end_port: int
    protocol: str


@dataclass(frozen=True, slots=True)
class SitePeer:
    site: str
    port: int
    protocol: str
    optional: bool


@dataclass(frozen=True, slots=True)
class ExternalHost:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class DynamicSource:
    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    name: str
    summary: str
    held: str | None
    gateway_port: int | None
    internal: tuple[InternalPeer, ...]
    sites: tuple[SitePeer, ...]
    external: tuple[ExternalHost, ...]
    dynamic: tuple[DynamicSource, ...]


@dataclass(frozen=True, slots=True)
class EgressProfiles:
    workloads: Mapping[str, WorkloadProfile]


def _keys(where: str, table: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ProfileError(f"{where}: unknown keys {unknown}")


def _text(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{where}: must be a non-empty string")
    return value


def _name(where: str, value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ProfileError(f"{where}: {value!r} is not a valid component/site name")
    return value


def _port(where: str, value: Any, *, minimum: int = 1) -> int:
    # bool is an int subclass; `port = true` must not read as port 1.
    if type(value) is not int or not minimum <= value <= 65535:
        raise ProfileError(f"{where}: {value!r} is not a port in {minimum}-65535")
    return value


def _protocol(where: str, value: Any) -> str:
    if value not in _PROTOCOLS:
        raise ProfileError(f"{where}: protocol must be one of {sorted(_PROTOCOLS)}")
    return value


def _entries(where: str, value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ProfileError(f"{where}: must be an array")
    return value


def _table(where: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{where}: must be a table")
    return value


def _internal(where: str, value: Any) -> InternalPeer:
    entry = _table(where, value)
    _keys(where, entry, frozenset({"component", "port", "end_port", "protocol"}))
    port = _port(f"{where}.port", entry.get("port"))
    end_port = _port(f"{where}.end_port", entry.get("end_port", port))
    if end_port < port:
        raise ProfileError(f"{where}: end_port {end_port} is below port {port}")
    return InternalPeer(
        component=_name(f"{where}.component", entry.get("component")),
        port=port,
        end_port=end_port,
        protocol=_protocol(where, entry.get("protocol", "TCP")),
    )


def _site(where: str, value: Any) -> SitePeer:
    entry = _table(where, value)
    _keys(where, entry, frozenset({"site", "port", "protocol", "optional"}))
    optional = entry.get("optional", False)
    if type(optional) is not bool:
        raise ProfileError(f"{where}.optional: must be true or false")
    return SitePeer(
        site=_name(f"{where}.site", entry.get("site")),
        port=_port(f"{where}.port", entry.get("port")),
        protocol=_protocol(where, entry.get("protocol", "TCP")),
        optional=optional,
    )


def _external(where: str, value: Any) -> ExternalHost:
    text = _text(where, value)
    host, sep, port_text = text.rpartition(":")
    # isdigit alone admits Unicode digits; a leading zero would not be the exact port.
    if not sep or not (port_text.isascii() and port_text.isdigit()) or port_text[0] == "0":
        raise ProfileError(f"{where}: {text!r} must be hostname:port")
    labels = host.split(".")
    if (
        host != host.lower()
        or len(host) > 253
        or len(labels) < 2
        or not all(_LABEL.fullmatch(label) for label in labels)
        or not _TLD.fullmatch(labels[-1])
    ):
        raise ProfileError(
            f"{where}: {host!r} must be an exact lowercase public DNS name "
            "(no IP literal, wildcard, trailing dot or single label)"
        )
    if host == "localhost" or any(host.endswith(suffix) for suffix in _PRIVATE_SUFFIXES):
        raise ProfileError(f"{where}: {host!r} names a private, cluster or tailnet destination")
    return ExternalHost(host=host, port=_port(where, int(port_text)))


def _dynamic(where: str, value: Any) -> DynamicSource:
    entry = _table(where, value)
    _keys(where, entry, frozenset({"source", "reason"}))
    return DynamicSource(
        source=_text(f"{where}.source", entry.get("source")),
        reason=_text(f"{where}.reason", entry.get("reason")),
    )


def _unique[T](where: str, items: list[T]) -> tuple[T, ...]:
    seen: set[T] = set()
    for item in items:
        if item in seen:
            raise ProfileError(f"{where}: duplicate entry {item}")
        seen.add(item)
    return tuple(items)


def _parsed[T](
    where: str, table: Mapping[str, Any], key: str, parse: Callable[[str, Any], T]
) -> tuple[T, ...]:
    path = f"{where}.{key}"
    entries = _entries(path, table.get(key, []))
    return _unique(path, [parse(f"{path}[{i}]", entry) for i, entry in enumerate(entries)])


def _workload(name: str, value: Any) -> WorkloadProfile:
    where = f"workloads.{_name(f'workloads.{name}', name)}"
    table = _table(where, value)
    _keys(where, table, _WORKLOAD_KEYS)
    held = table.get("held")
    if held is not None:
        held = _text(f"{where}.held", held)
        declared = sorted(set(table) & _ROUTE_KEYS)
        if declared:
            raise ProfileError(f"{where}: a held workload admits nothing, but declares {declared}")
    internal = _parsed(where, table, "internal", _internal)
    sites = _parsed(where, table, "sites", _site)
    external = _parsed(where, table, "external", _external)
    dynamic = _parsed(where, table, "dynamic", _dynamic)
    gateway_port = table.get("gateway_port")
    if external and gateway_port is None:
        raise ProfileError(f"{where}: external hosts need a gateway_port")
    if gateway_port is not None:
        if not external:
            raise ProfileError(f"{where}: gateway_port without external hosts")
        gateway_port = _port(f"{where}.gateway_port", gateway_port, minimum=1024)
    return WorkloadProfile(
        name=name,
        summary=_text(f"{where}.summary", table.get("summary")),
        held=held,
        gateway_port=gateway_port,
        internal=internal,
        sites=sites,
        external=external,
        dynamic=dynamic,
    )


def parse_profiles(text: str) -> EgressProfiles:
    """Parse and validate profile TOML; any doubt raises ProfileError."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"not valid TOML: {exc}") from exc
    _keys("<root>", document, frozenset({"schema", "workloads"}))
    if document.get("schema") != SCHEMA:
        raise ProfileError(f"schema must be {SCHEMA!r}")
    tables = _table("workloads", document.get("workloads"))
    if not tables:
        raise ProfileError("workloads: at least one profile is required")
    if GATEWAY_COMPONENT in tables:
        raise ProfileError(f"workloads.{GATEWAY_COMPONENT}: the gateway is not a profiled workload")
    workloads = {name: _workload(name, value) for name, value in tables.items()}
    for profile in workloads.values():
        for peer in profile.internal:
            if peer.component == GATEWAY_COMPONENT:
                raise ProfileError(
                    f"workloads.{profile.name}.internal: the gateway is reachable only through "
                    "gateway_port, never as an internal peer"
                )
            if peer.component not in workloads:
                raise ProfileError(
                    f"workloads.{profile.name}.internal: {peer.component!r} "
                    "is not a declared workload"
                )
    ports: dict[int, str] = {}
    for profile in workloads.values():
        if profile.gateway_port is None:
            continue
        if profile.gateway_port in ports:
            raise ProfileError(
                f"gateway_port {profile.gateway_port} is shared by "
                f"{ports[profile.gateway_port]} and {profile.name}: it is the workload identity"
            )
        ports[profile.gateway_port] = profile.name
    return EgressProfiles(workloads=workloads)


def load_profiles(path: Path = PROFILES_PATH) -> EgressProfiles:
    return parse_profiles(path.read_text(encoding="utf-8"))


def render(profiles: EgressProfiles) -> str:
    """Deterministic JSON the chart reads with .Files.Get | fromJson."""
    workloads = {
        name: {
            "summary": p.summary,
            "held": p.held,
            "gateway_port": p.gateway_port,
            "internal": [
                {"component": i.component, "port": i.port, "end_port": i.end_port,
                 "protocol": i.protocol}
                for i in p.internal
            ],
            "sites": [
                {"site": s.site, "port": s.port, "protocol": s.protocol, "optional": s.optional}
                for s in p.sites
            ],
            "external": [{"host": e.host, "port": e.port} for e in p.external],
            "dynamic": [{"source": d.source, "reason": d.reason} for d in p.dynamic],
        }
        for name, p in sorted(profiles.workloads.items())
    }  # fmt: skip
    document = {
        "generated_from": "deploy/egress-profiles.toml (make egress-profiles); do not edit",
        "schema": SCHEMA,
        "workloads": workloads,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the egress profiles.")
    parser.add_argument("--profiles", type=Path, default=PROFILES_PATH)
    parser.add_argument("--output", type=Path, default=RENDERED_PATH)
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed rendering is stale"
    )
    args = parser.parse_args(argv)
    try:
        rendered = render(load_profiles(args.profiles))
    except ProfileError as exc:
        print(f"egress profiles invalid: {exc}", file=sys.stderr)
        return 1
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
        if current != rendered:
            print(f"{args.output} is stale; run `make egress-profiles`", file=sys.stderr)
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
