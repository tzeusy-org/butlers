"""Immutable value catalog of the final registered FastMCP tool surface."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol

LoadPosture = Literal["eager", "deferred"]


class ToolMetadata(Protocol):
    """Structural metadata contract supplied by module and core registration."""

    arg_sensitivities: dict[str, bool]
    canonical_name: str | None
    module_name: str | None
    group_name: str | None
    namespace: str | None
    llm_presentable: bool | None
    load_posture: LoadPosture | None


class ToolCatalogError(ValueError):
    """The registered surface and its checked-in metadata disagree."""


def _validate_posture(value: str) -> None:
    if value not in {"eager", "deferred"}:
        raise ValueError(f"invalid tool load posture: {value!r}")


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    """Checked-in presentation declaration for one canonical handler."""

    canonical_name: str
    module_name: str
    group_name: str
    namespace: str
    llm_presentable: bool
    load_posture: LoadPosture

    def __post_init__(self) -> None:
        _validate_posture(self.load_posture)
        for field_name in ("canonical_name", "module_name", "group_name", "namespace"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")


FrozenValue = (
    str | int | float | bool | None | tuple["FrozenValue", ...] | Mapping[str, "FrozenValue"]
)


def _freeze(value: Any) -> FrozenValue:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported catalog definition value: {type(value).__name__}")


def _plain(value: FrozenValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: FrozenValue) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: FrozenValue) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Callable-free immutable description of one final FastMCP definition."""

    canonical_name: str
    module_name: str
    group_name: str
    namespace: str
    llm_presentable: bool
    load_posture: LoadPosture
    classification_complete: bool
    input_schema: Mapping[str, FrozenValue]
    description: str
    input_schema_digest: str
    description_digest: str
    arg_sensitivities: Mapping[str, bool]

    def __post_init__(self) -> None:
        _validate_posture(self.load_posture)


@dataclass(frozen=True, slots=True)
class ToolCatalog(Mapping[str, ToolDescriptor]):
    """One daemon generation's stable registered definition snapshot."""

    descriptors: tuple[ToolDescriptor, ...]
    classification_complete: bool
    generation_digest: str
    _by_name: Mapping[str, ToolDescriptor] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = [descriptor.canonical_name for descriptor in self.descriptors]
        if len(names) != len(set(names)):
            raise ToolCatalogError("duplicate canonical name in tool catalog")
        object.__setattr__(
            self,
            "_by_name",
            MappingProxyType(
                {descriptor.canonical_name: descriptor for descriptor in self.descriptors}
            ),
        )

    def __getitem__(self, key: str) -> ToolDescriptor:
        return self._by_name[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self.descriptors)


def validate_presentation_inventory(
    actual_owners: Mapping[str, str],
    declarations: tuple[ToolPresentation, ...],
) -> None:
    """Require an exact, unique checked-in declaration for an executable union."""
    declared: dict[str, ToolPresentation] = {}
    for declaration in declarations:
        if declaration.canonical_name in declared:
            raise ToolCatalogError(
                f"duplicate presentation declaration: {declaration.canonical_name}"
            )
        declared[declaration.canonical_name] = declaration

    missing = sorted(set(actual_owners) - set(declared))
    stale = sorted(set(declared) - set(actual_owners))
    owner_drift = sorted(
        name
        for name in set(actual_owners) & set(declared)
        if actual_owners[name] != declared[name].module_name
    )
    problems: list[str] = []
    if missing:
        problems.append(f"missing={missing}")
    if stale:
        problems.append(f"stale={stale}")
    if owner_drift:
        problems.append(f"owner mismatch={owner_drift}")
    if problems:
        raise ToolCatalogError("invalid presentation inventory: " + "; ".join(problems))


def validate_tool_metadata(name: str, owner: str, metadata: ToolMetadata | None) -> bool:
    """Validate one optional exposure declaration and report whether it is complete."""
    if metadata is None:
        return False
    values = (
        metadata.canonical_name,
        metadata.module_name,
        metadata.group_name,
        metadata.namespace,
        metadata.llm_presentable,
        metadata.load_posture,
    )
    if all(value is None for value in values):
        return False
    if any(value is None for value in values):
        raise ToolCatalogError(f"incomplete tool presentation metadata: {name}")
    if metadata.canonical_name != name:
        raise ToolCatalogError(
            f"tool metadata canonical name mismatch: {name!r} != {metadata.canonical_name!r}"
        )
    if metadata.module_name != owner:
        raise ToolCatalogError(
            f"tool metadata owner mismatch: {name!r} registered to {owner!r}, "
            f"declared by {metadata.module_name!r}"
        )
    if not isinstance(metadata.group_name, str) or not metadata.group_name:
        raise ToolCatalogError(f"invalid tool metadata group: {name}")
    if not isinstance(metadata.namespace, str) or not metadata.namespace:
        raise ToolCatalogError(f"invalid tool metadata namespace: {name}")
    if not isinstance(metadata.llm_presentable, bool):
        raise ToolCatalogError(f"invalid tool metadata llm_presentable: {name}")
    try:
        _validate_posture(metadata.load_posture)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ToolCatalogError(f"invalid tool metadata load posture: {name}") from exc
    return True


async def build_tool_catalog(
    mcp: Any,
    *,
    tool_owners: Mapping[str, str],
    tool_metadata: Mapping[str, ToolMetadata] | None = None,
) -> ToolCatalog:
    """Snapshot final definitions through FastMCP's public list interface."""
    metadata = tool_metadata or {}
    descriptors: list[ToolDescriptor] = []
    listed_tools = mcp.list_tools()
    if inspect.isawaitable(listed_tools):
        listed_tools = await listed_tools
    listed_names = [tool.name for tool in listed_tools]
    if len(listed_names) != len(set(listed_names)):
        raise ToolCatalogError("FastMCP listed duplicate canonical tool names")
    for tool in sorted(listed_tools, key=lambda item: item.name):
        name = tool.name
        declared = metadata.get(name)
        owner = tool_owners.get(name)
        if owner is None:
            raise ToolCatalogError(f"registered tool has no owner: {name}")
        classified = validate_tool_metadata(name, owner, declared)
        schema = _freeze(tool.parameters or {})
        if not isinstance(schema, Mapping):
            raise ToolCatalogError(f"tool input schema is not an object: {name}")
        description = tool.description or ""
        frozen_description = _freeze(description)
        descriptors.append(
            ToolDescriptor(
                canonical_name=name,
                module_name=owner,
                group_name=declared.group_name if classified else "legacy",
                namespace=declared.namespace if classified else f"{owner}.legacy",
                llm_presentable=declared.llm_presentable if classified else True,
                load_posture=declared.load_posture if classified else "eager",
                classification_complete=classified,
                input_schema=schema,
                description=description,
                input_schema_digest=_digest(schema),
                description_digest=_digest(frozen_description),
                arg_sensitivities=MappingProxyType(
                    dict(declared.arg_sensitivities) if declared is not None else {}
                ),
            )
        )

    descriptor_values: FrozenValue = tuple(
        _freeze(
            {
                "canonical_name": descriptor.canonical_name,
                "module_name": descriptor.module_name,
                "group_name": descriptor.group_name,
                "namespace": descriptor.namespace,
                "llm_presentable": descriptor.llm_presentable,
                "load_posture": descriptor.load_posture,
                "classification_complete": descriptor.classification_complete,
                "input_schema_digest": descriptor.input_schema_digest,
                "description_digest": descriptor.description_digest,
                "arg_sensitivities": descriptor.arg_sensitivities,
            }
        )
        for descriptor in descriptors
    )
    return ToolCatalog(
        descriptors=tuple(descriptors),
        classification_complete=all(item.classification_complete for item in descriptors),
        generation_digest=_digest(descriptor_values),
    )
