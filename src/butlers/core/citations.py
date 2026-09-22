"""Canonical, server-authoritative normalization for answer citations."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, unquote, urlsplit

MAX_CITATIONS = 20
MAX_LABEL_LENGTH = 200
MAX_TARGET_LENGTH = 2048
MAX_PAYLOAD_LENGTH = 32 * 1024

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_PATH_SEGMENT_SAFE = "-._~:@+"

CitationKind = Literal["internal", "external", "unlinked"]
Citation = dict[str, str | None]


@dataclass(frozen=True, slots=True)
class CitationNormalization:
    """Canonical write values plus content-blind rejected-target evidence."""

    citations: list[Citation] | None
    legacy_sources: list[str] | None
    rejected_by_reason: dict[str, int]

    @property
    def rejected_count(self) -> int:
        return sum(self.rejected_by_reason.values())


@dataclass(frozen=True, slots=True)
class _RouteContract:
    path: tuple[str, ...]
    parameters: dict[str, re.Pattern[str]]
    query: dict[str, tuple[re.Pattern[str] | None, frozenset[str] | None]]
    fragment: re.Pattern[str] | None


def _plain_label(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("sources labels must be non-empty plain text")
    label = value.strip()
    if (
        not label
        or len(label) > MAX_LABEL_LENGTH
        or _CONTROL.search(label)
        or any(unicodedata.category(character) == "Cs" for character in label)
    ):
        raise ValueError("sources labels must be non-empty plain text")
    return label


@lru_cache(maxsize=1)
def _route_contracts() -> tuple[_RouteContract, ...]:
    manifest_path = files("butlers.core").joinpath("citation_routes.json")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("version") != 1 or not isinstance(raw.get("routes"), list):
        raise RuntimeError("citation route manifest is invalid")

    contracts: list[_RouteContract] = []
    seen_paths: set[str] = set()
    for entry in raw["routes"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RuntimeError("citation route manifest entry is invalid")
        path = entry["path"]
        if path in seen_paths or not path.startswith("/"):
            raise RuntimeError("citation route manifest paths must be unique absolute paths")
        seen_paths.add(path)
        segments = () if path == "/" else tuple(path.split("/")[1:])
        raw_parameters = entry.get("parameters", {})
        parameter_names = {segment[1:] for segment in segments if segment.startswith(":")}
        if not isinstance(raw_parameters, dict) or set(raw_parameters) != parameter_names:
            raise RuntimeError("citation route manifest parameters do not match the route")
        parameters = {
            name: re.compile(pattern)
            for name, pattern in raw_parameters.items()
            if isinstance(name, str) and isinstance(pattern, str)
        }
        if set(parameters) != parameter_names:
            raise RuntimeError("citation route manifest parameter pattern is invalid")

        query: dict[str, tuple[re.Pattern[str] | None, frozenset[str] | None]] = {}
        raw_query = entry.get("query", {})
        if not isinstance(raw_query, dict):
            raise RuntimeError("citation route manifest query contract is invalid")
        for key, rule in raw_query.items():
            if not isinstance(key, str) or not isinstance(rule, dict):
                raise RuntimeError("citation route manifest query rule is invalid")
            pattern = rule.get("pattern")
            values = rule.get("values")
            if (pattern is None) == (values is None):
                raise RuntimeError("citation route query rule needs one value constraint")
            query[key] = (
                re.compile(pattern) if isinstance(pattern, str) else None,
                frozenset(values) if isinstance(values, list) else None,
            )

        raw_fragment = entry.get("fragment")
        if raw_fragment is not None and not isinstance(raw_fragment, str):
            raise RuntimeError("citation route fragment contract is invalid")
        contracts.append(
            _RouteContract(
                path=segments,
                parameters=parameters,
                query=query,
                fragment=re.compile(raw_fragment) if raw_fragment is not None else None,
            )
        )
    return tuple(contracts)


def _decode_path_segment(raw_segment: str) -> str | None:
    if _PERCENT_ESCAPE.search(raw_segment) or _ENCODED_SEPARATOR.search(raw_segment):
        return None
    try:
        decoded = unquote(raw_segment, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if decoded in {"", ".", ".."} or "/" in decoded or "\\" in decoded:
        return None
    if quote(decoded, safe=_PATH_SEGMENT_SAFE) != raw_segment:
        return None
    return decoded


def _route_matches(contract: _RouteContract, parsed: Any) -> bool:
    raw_segments = () if parsed.path == "/" else tuple(parsed.path.split("/")[1:])
    if len(raw_segments) != len(contract.path):
        return False
    for expected, raw_segment in zip(contract.path, raw_segments, strict=True):
        decoded = _decode_path_segment(raw_segment)
        if decoded is None:
            return False
        if expected.startswith(":"):
            if contract.parameters[expected[1:]].fullmatch(decoded) is None:
                return False
        elif decoded != expected:
            return False

    if _PERCENT_ESCAPE.search(parsed.query):
        return False
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError):
        return False
    if len({key for key, _ in query_pairs}) != len(query_pairs):
        return False
    if any(key not in contract.query for key, _ in query_pairs):
        return False
    for key, value in query_pairs:
        pattern, values = contract.query[key]
        if pattern is not None and pattern.fullmatch(value) is None:
            return False
        if values is not None and value not in values:
            return False

    if parsed.fragment:
        if contract.fragment is None or _PERCENT_ESCAPE.search(parsed.fragment):
            return False
        try:
            fragment = unquote(parsed.fragment, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return False
        if contract.fragment.fullmatch(fragment) is None:
            return False
    return True


def _internal_target(target: str) -> bool:
    if (
        not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or _CONTROL.search(target)
        or _PERCENT_ESCAPE.search(target)
        or _ENCODED_SEPARATOR.search(target)
    ):
        return False
    try:
        parsed = urlsplit(target)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc or parsed.username or parsed.password:
        return False
    return sum(_route_matches(contract, parsed) for contract in _route_contracts()) == 1


def _external_target(target: str) -> bool:
    if (
        not target.startswith("https://")
        or "\\" in target
        or _CONTROL.search(target)
        or _PERCENT_ESCAPE.search(target)
    ):
        return False
    try:
        parsed = urlsplit(target)
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.netloc
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
    )


def _target_kind(target: str) -> tuple[CitationKind | None, str | None]:
    if _internal_target(target):
        return "internal", None
    if _external_target(target):
        return "external", None
    if target.startswith("/"):
        return None, "route_not_allowlisted"
    return None, "unsafe_external_target"


def normalize_citations(sources: list[Any] | None) -> CitationNormalization:
    """Normalize the existing ``sources`` input into the sole forward citation shape."""
    if sources is None:
        return CitationNormalization(None, None, {})
    if not sources or len(sources) > MAX_CITATIONS:
        raise ValueError("sources must contain between 1 and 20 entries")

    normalized: list[Citation] = []
    rejected_by_reason: dict[str, int] = {}
    for source in sources:
        if isinstance(source, str):
            label = _plain_label(source)
            normalized.append({"label": label, "target": None, "kind": "unlinked"})
            continue
        if not isinstance(source, dict) or set(source) != {"label", "target"}:
            raise ValueError("sources must contain names or {label, target} citation objects")
        label = _plain_label(source.get("label"))
        target = source.get("target")
        if isinstance(target, str) and len(target) > MAX_TARGET_LENGTH:
            raise ValueError("sources target exceeds the size limit")
        if not isinstance(target, str) or not target:
            reason = "target_budget_or_shape_invalid"
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            continue
        kind, reason = _target_kind(target)
        if kind is None:
            assert reason is not None
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            continue
        normalized.append({"label": label, "target": target, "kind": kind})

    if not normalized:
        raise ValueError("sources did not contain a usable citation")

    deduped: list[Citation] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for citation in normalized:
        key = (citation["kind"], citation["target"], citation["label"])
        if key not in seen:
            seen.add(key)
            deduped.append(citation)
    try:
        payload_size = len(
            json.dumps(deduped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except UnicodeEncodeError as exc:
        raise ValueError("sources labels must be valid Unicode plain text") from exc
    if payload_size > MAX_PAYLOAD_LENGTH:
        raise ValueError("sources payload exceeds the size limit")
    return CitationNormalization(
        citations=deduped,
        legacy_sources=[str(citation["label"]) for citation in deduped],
        rejected_by_reason=rejected_by_reason,
    )


__all__ = [
    "CitationNormalization",
    "MAX_CITATIONS",
    "MAX_LABEL_LENGTH",
    "MAX_PAYLOAD_LENGTH",
    "MAX_TARGET_LENGTH",
    "normalize_citations",
]
