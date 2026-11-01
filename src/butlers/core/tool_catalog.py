"""Immutable value catalog of the final registered FastMCP tool surface."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol

LoadPosture = Literal["eager", "deferred"]

EffectClass = Literal["read", "record", "communicate", "transact", "actuate", "administer"]
OutputTrust = Literal["trusted", "untrusted", "stamped"]
OutputSensitivity = Literal["ordinary", "sensitive"]
Idempotency = Literal["none", "key_required"]
AUTHORIZATION_FIELDS = (
    "owner_domain",
    "effect_class",
    "output_trust",
    "output_sensitivity",
    "resource_params",
    "selection_bound",
    "safety_args",
    "approval_policy",
    "idempotency",
    "owner_authority",
    "recipient_params",
)


class OwnerNotifyBranch(StrEnum):
    """Closed native admission eligibility, never resolved recipient authority."""

    TELEGRAM_SEND_INSIGHT_WITHOUT_PARTY = "telegram_send_insight_without_party"


@dataclass(frozen=True, slots=True)
class RecipientParameters:
    """Named selectors plus parties inherited from a resource or transport.

    Conditions describe the native branch, not an executable policy. A future
    broker must resolve every named party and the branch's inherited parties;
    an unresolved party cannot be replaced by the owner's default.
    """

    argument_names: tuple[str, ...] = ()
    undeterminable: bool = False
    default_owner_condition: str | None = None
    undeterminable_condition: str | None = None
    owner_notify_branch: OwnerNotifyBranch | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "argument_names", tuple(self.argument_names))
        if self.owner_notify_branch is not None:
            # Decode the same closed value after JSON publication. Unknown
            # strings/bools refuse; descriptive condition prose is not parsed.
            object.__setattr__(
                self, "owner_notify_branch", OwnerNotifyBranch(self.owner_notify_branch)
            )

    def as_mapping(self) -> Mapping[str, Any]:
        result = {
            "argument_names": self.argument_names,
            "undeterminable": self.undeterminable,
            "default_owner_condition": self.default_owner_condition,
            "undeterminable_condition": self.undeterminable_condition,
        }
        # Preserve historical publications/digests when no branch was declared.
        if self.owner_notify_branch is not None:
            result["owner_notify_branch"] = self.owner_notify_branch.value
        return result


@dataclass(frozen=True, slots=True)
class StoreWriters:
    """Conservative native store provenance, carried by the same declarations."""

    record_capabilities: tuple[str, ...] = ()
    native_writers: tuple[str, ...] = ()
    external_writers: bool = False
    unknown_writers: bool = False


class ToolMetadata(Protocol):
    """Structural metadata contract supplied by module and core registration."""

    arg_sensitivities: dict[str, bool]
    canonical_name: str | None
    module_name: str | None
    group_name: str | None
    namespace: str | None
    llm_presentable: bool | None
    load_posture: LoadPosture | None
    owner_domain: str | None = None
    effect_class: EffectClass | None = None
    output_trust: OutputTrust | None = None
    output_sensitivity: OutputSensitivity | None = None
    resource_params: tuple[str, ...] | None = None
    selection_bound: Mapping[str, bool] | None = None
    safety_args: tuple[str, ...] | None = None
    approval_policy: Mapping[str, Any] | None = None
    idempotency: Idempotency | None = None
    owner_authority: bool | None = None
    recipient_params: RecipientParameters | None = None
    read_stores: tuple[str, ...] = ()
    write_stores: tuple[str, ...] = ()
    unknown_output_source: bool = True


def _owner_only_tool(name: str) -> bool:
    return name in {
        "approve_action",
        "reject_action",
        "dispatch_approved_action",
        "create_approval_rule",
        "create_rule_from_action",
        "revoke_approval_rule",
        "confirm_promotion_suggestion",
        "dismiss_promotion_suggestion",
    } or name.startswith("custody.")


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
    owner_domain: str | None = None
    effect_class: EffectClass | None = None
    output_trust: OutputTrust | None = None
    output_sensitivity: OutputSensitivity | None = None
    resource_params: tuple[str, ...] | None = None
    selection_bound: Mapping[str, bool] | None = None
    safety_args: tuple[str, ...] | None = None
    approval_policy: Mapping[str, Any] | None = None
    idempotency: Idempotency | None = None
    owner_authority: bool | None = None
    recipient_params: RecipientParameters | None = None
    read_stores: tuple[str, ...] = ()
    write_stores: tuple[str, ...] = ()
    unknown_output_source: bool = True

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
    owner_domain: str | None = None
    effect_class: EffectClass | None = None
    output_trust: OutputTrust | None = None
    output_sensitivity: OutputSensitivity | None = None
    resource_params: tuple[str, ...] | None = None
    selection_bound: Mapping[str, bool] | None = None
    safety_args: tuple[str, ...] | None = None
    approval_policy: Mapping[str, Any] | None = None
    idempotency: Idempotency | None = None
    owner_authority: bool | None = None
    recipient_params: RecipientParameters | None = None
    read_stores: tuple[str, ...] = ()
    write_stores: tuple[str, ...] = ()
    unknown_output_source: bool = True

    def __post_init__(self) -> None:
        _validate_posture(self.load_posture)

    @property
    def authorization_complete(self) -> bool:
        """Definition eligibility only; it never authorizes an invocation."""
        return not _authorization_problems(self)

    @property
    def grantable(self) -> bool:
        """Dormant future-broker eligibility, with transact always disabled."""
        return (
            self.llm_presentable
            and not _owner_only_tool(self.canonical_name)
            and self.authorization_complete
            and self.effect_class != "transact"
        )


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


def _authorization_problems(descriptor: ToolDescriptor) -> list[str]:
    problems = [name for name in AUTHORIZATION_FIELDS if getattr(descriptor, name) is None]
    if not descriptor.classification_complete:
        problems.append("classification_complete")
    if problems:
        return problems
    if not isinstance(descriptor.owner_domain, str) or not descriptor.owner_domain.strip():
        problems.append("owner_domain")
    if not isinstance(descriptor.effect_class, str) or descriptor.effect_class not in {
        "read",
        "record",
        "communicate",
        "transact",
        "actuate",
        "administer",
    }:
        problems.append("effect_class")
    if not isinstance(descriptor.output_trust, str) or descriptor.output_trust not in {
        "trusted",
        "untrusted",
        "stamped",
    }:
        problems.append("output_trust")
    if not isinstance(descriptor.output_sensitivity, str) or descriptor.output_sensitivity not in {
        "ordinary",
        "sensitive",
    }:
        problems.append("output_sensitivity")
    if not isinstance(descriptor.idempotency, str) or descriptor.idempotency not in {
        "none",
        "key_required",
    }:
        problems.append("idempotency")
    if not isinstance(descriptor.owner_authority, bool):
        problems.append("owner_authority")
    if not isinstance(descriptor.approval_policy, Mapping):
        problems.append("approval_policy")
    properties = descriptor.input_schema.get("properties", {})
    selection = descriptor.selection_bound
    resource_params = descriptor.resource_params
    if (
        not isinstance(selection, Mapping)
        or not isinstance(resource_params, tuple)
        or set(selection) != set(resource_params)
        or any(not isinstance(flag, bool) for flag in selection.values())
    ):
        problems.append("selection_bound")
    for name in ("resource_params", "safety_args"):
        value = getattr(descriptor, name)
        if (
            not isinstance(value, tuple)
            or any(not isinstance(arg, str) or arg not in properties for arg in value)
            or len(value) != len(set(value))
        ):
            problems.append(name)
    recipients = descriptor.recipient_params
    if not isinstance(recipients, RecipientParameters):
        problems.append("recipient_params")
    elif (
        not isinstance(recipients.argument_names, tuple)
        or any(not isinstance(arg, str) for arg in recipients.argument_names)
        or not isinstance(recipients.undeterminable, bool)
        or len(recipients.argument_names) != len(set(recipients.argument_names))
        or any(
            value is not None and (not isinstance(value, str) or not value.strip())
            for value in (
                recipients.default_owner_condition,
                recipients.undeterminable_condition,
            )
        )
        or any(arg not in properties for arg in recipients.argument_names)
        or (
            descriptor.effect_class == "communicate"
            and not (
                recipients.argument_names
                or recipients.undeterminable
                or recipients.default_owner_condition
            )
        )
    ):
        problems.append("recipient_params")
    elif (
        {"to", "recipient", "recipients", "chat_id", "chat_jid", "attendees"}.intersection(
            properties
        )
        or descriptor.canonical_name
        in {"ha_call_service", "remind", "delegate_answer", "calendar_respond"}
    ) and not (
        recipients.argument_names or recipients.undeterminable or recipients.default_owner_condition
    ):
        problems.append("recipient_params")
    return problems


def validate_authorization_catalog(
    catalog: ToolCatalog, *, store_writers: Mapping[str, StoreWriters]
) -> None:
    """CI declaration gate over actual final registered definitions.

    This read-only check is not called by handlers, adapters or approval gates.
    Unknown provenance can never establish trusted output.
    """
    for descriptor in catalog.descriptors:
        name = descriptor.canonical_name
        if not descriptor.llm_presentable:
            continue
        if _owner_only_tool(name):
            raise ToolCatalogError(f"owner-decision/private-custody tool is presentable: {name}")
        if descriptor.effect_class == "record":
            for store in descriptor.write_stores:
                writers = store_writers.get(store)
                if writers is None or name not in writers.record_capabilities:
                    raise ToolCatalogError(f"undeclared record writer {name} for store {store}")
        problems = _authorization_problems(descriptor)
        if problems:
            raise ToolCatalogError(
                f"invalid authorization metadata for {name}: {', '.join(problems)}"
            )
        if descriptor.output_trust == "trusted":
            for store in descriptor.read_stores:
                writers = store_writers.get(store)
                if writers is None or (
                    writers.record_capabilities
                    or writers.native_writers
                    or writers.external_writers
                    or writers.unknown_writers
                ):
                    raise ToolCatalogError(
                        f"trusted read {name} covers writable or unknown store {store}"
                    )
            if descriptor.unknown_output_source:
                raise ToolCatalogError(f"trusted read {name} has unknown output provenance")
        if descriptor.output_trust == "stamped":
            raise ToolCatalogError(f"stamped output has no implemented stamp producer: {name}")


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
                **{
                    field_name: (
                        _freeze(getattr(declared, field_name, None))
                        if field_name
                        in {"approval_policy", "resource_params", "selection_bound", "safety_args"}
                        else getattr(declared, field_name, None)
                    )
                    for field_name in AUTHORIZATION_FIELDS
                },
                read_stores=tuple(getattr(declared, "read_stores", ())),
                write_stores=tuple(getattr(declared, "write_stores", ())),
                unknown_output_source=getattr(declared, "unknown_output_source", True),
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
                **{
                    name: (
                        descriptor.recipient_params.as_mapping()
                        if name == "recipient_params"
                        and isinstance(descriptor.recipient_params, RecipientParameters)
                        else getattr(descriptor, name)
                    )
                    for name in AUTHORIZATION_FIELDS
                },
                "read_stores": descriptor.read_stores,
                "write_stores": descriptor.write_stores,
                "unknown_output_source": descriptor.unknown_output_source,
            }
        )
        for descriptor in descriptors
    )
    return ToolCatalog(
        descriptors=tuple(descriptors),
        classification_complete=all(item.classification_complete for item in descriptors),
        generation_digest=_digest(descriptor_values),
    )
