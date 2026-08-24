"""Validated capability descriptors for runtime adapters and catalog entries.

Model *eligibility* (enabled, verified, breaker-closed, quota-ok) has never proven
model *fit*: nothing in ``public.model_catalog`` said whether an entry can call
tools, resume a prior provider-native session, or hold the context a dispatch
needs. The resolver therefore happily returned an entry the adapter would reject
at ``invoke()`` time -- the live instance of that is ``ApiAdapter``, which raises
``RuntimeError`` on any non-empty ``mcp_servers`` while its seeded catalog rows
(``api-haiku-cheap``, priority 30) sit at the TOP of the ``cheap`` tier that
butler sessions resolve into.

This module supplies the missing descriptor, in two layers:

1. **Adapter baseline** -- what every model reachable through a runtime adapter
   can or cannot do, declared on the adapter class itself
   (``RuntimeAdapter.declared_capabilities`` plus the pre-existing
   ``supports_resume`` flag). This is a property of the integration, not of the
   model, so it belongs in code beside the integration.
2. **Catalog override** -- per-entry ``public.model_catalog.capabilities`` JSONB
   plus ``max_context_tokens`` / ``max_output_tokens``, for what varies model by
   model within one adapter.

The catalog layer wins per feature; anything neither layer declares stays
:attr:`Support.UNKNOWN`, which callers MUST treat as "not proven", never as
"supported" (see ``butlers.core.dispatch_intent.evaluate_fit``).

Nothing here reads or holds prompt text, credentials, or provider responses --
a descriptor is operator/author-declared configuration only, and the error
strings below name feature keys and Python type names, never a value.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
from collections.abc import Mapping
from types import MappingProxyType

logger = logging.getLogger(__name__)


class ModelFeature(enum.StrEnum):
    """Capability vocabulary a dispatch may require and a descriptor may declare.

    Deliberately small and closed: every member names something the codebase can
    already observe or a caller can already require, so a declaration is checkable
    rather than aspirational. Unknown keys in a descriptor are a validation error,
    not a silently-ignored extension point -- a typo'd feature name must never
    read as "no opinion" when the whole point of the descriptor is fail-closed fit.
    """

    #: The runtime can execute tools during the session (MCP wiring for the CLI
    #: adapters, native tool-use for a direct-API adapter).
    TOOL_USE = "tool_use"
    #: The runtime can be asked for a schema-constrained payload rather than free
    #: text (e.g. ``ApiAdapter.invoke_structured``'s forced tool-use call).
    STRUCTURED_OUTPUT = "structured_output"
    #: The runtime can continue a prior provider-native session instead of
    #: cold-starting (mirrors ``RuntimeAdapter.supports_resume``).
    SESSION_RESUME = "session_resume"


class Support(enum.StrEnum):
    """Tri-state answer to "can this candidate do X".

    ``UNKNOWN`` is a first-class answer, not a stand-in for ``UNSUPPORTED``: the
    two get different treatment at different consequence levels (see
    ``butlers.core.dispatch_intent``), and collapsing them would either brick
    every undeclared entry or silently fail open.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CapabilityDescriptorError(ValueError):
    """A capability descriptor was malformed and cannot be trusted.

    Raised by :func:`parse_capability_descriptor` at write/validation boundaries.
    Resolution-time callers never let this escape -- a catalog row carrying an
    invalid descriptor is *excluded* from candidacy (fail closed) rather than
    wedging every dispatch for that butler.
    """


_EMPTY_FEATURES: Mapping[ModelFeature, bool] = MappingProxyType({})


@dataclasses.dataclass(frozen=True)
class CapabilityDescriptor:
    """What one layer declares about a candidate's capabilities and envelope.

    ``features`` holds only *explicit* declarations. A feature absent from the
    mapping is :attr:`Support.UNKNOWN` for this layer, which is what lets the
    catalog layer override one feature without having to restate the rest.
    """

    features: Mapping[ModelFeature, bool] = _EMPTY_FEATURES
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None

    def support(self, feature: ModelFeature) -> Support:
        """Return this layer's tri-state answer for *feature*."""
        declared = self.features.get(feature)
        if declared is None:
            return Support.UNKNOWN
        return Support.SUPPORTED if declared else Support.UNSUPPORTED

    def layered_over(self, base: CapabilityDescriptor) -> CapabilityDescriptor:
        """Return *self* layered over *base*: self wins per field, base fills gaps.

        Merging is per feature key, not whole-mapping replacement, so a catalog
        row declaring ``{"structured_output": true}`` keeps the adapter's
        ``tool_use`` answer instead of blanking it to UNKNOWN.
        """
        merged: dict[ModelFeature, bool] = dict(base.features)
        merged.update(self.features)
        return CapabilityDescriptor(
            features=MappingProxyType(merged),
            max_context_tokens=(
                self.max_context_tokens
                if self.max_context_tokens is not None
                else base.max_context_tokens
            ),
            max_output_tokens=(
                self.max_output_tokens
                if self.max_output_tokens is not None
                else base.max_output_tokens
            ),
        )

    def describe(self) -> dict[str, object]:
        """Return a JSON-safe projection for a resolution receipt.

        Only declared features appear; an absent key means UNKNOWN, which the
        receipt must render as unknown rather than as a negative answer.
        """
        return {
            "features": {str(feature): value for feature, value in sorted(self.features.items())},
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


EMPTY_CAPABILITIES = CapabilityDescriptor()


def _coerce_positive_int(value: object, *, field: str) -> int | None:
    """Validate an optional positive token-count column."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapabilityDescriptorError(
            f"{field} must be a positive integer or null, got {type(value).__name__}"
        )
    if value <= 0:
        raise CapabilityDescriptorError(f"{field} must be a positive integer or null")
    return value


def parse_capability_descriptor(
    raw_capabilities: object,
    *,
    max_context_tokens: object = None,
    max_output_tokens: object = None,
) -> CapabilityDescriptor:
    """Validate a stored capability envelope into a :class:`CapabilityDescriptor`.

    ``raw_capabilities`` accepts what asyncpg hands back for a JSONB column --
    ``None``, a ``dict``, or the raw JSON ``str`` -- so callers do not have to
    normalize first.

    Raises
    ------
    CapabilityDescriptorError
        When the envelope is not a JSON object, declares a feature key outside
        :class:`ModelFeature`, uses a non-boolean feature value, or carries a
        non-positive token limit. The message names the offending key and the
        Python type only; it never interpolates a stored value.
    """
    if isinstance(raw_capabilities, str):
        try:
            raw_capabilities = json.loads(raw_capabilities)
        except (ValueError, TypeError) as exc:
            raise CapabilityDescriptorError("capabilities is not valid JSON") from exc
    if raw_capabilities is None:
        raw_capabilities = {}
    if not isinstance(raw_capabilities, dict):
        raise CapabilityDescriptorError(
            f"capabilities must be a JSON object, got {type(raw_capabilities).__name__}"
        )

    features: dict[ModelFeature, bool] = {}
    for key, value in raw_capabilities.items():
        if not isinstance(key, str):
            raise CapabilityDescriptorError(
                f"capabilities keys must be strings, got {type(key).__name__}"
            )
        try:
            feature = ModelFeature(key)
        except ValueError as exc:
            known = ", ".join(sorted(str(member) for member in ModelFeature))
            raise CapabilityDescriptorError(
                f"unknown capability key {key!r}; known keys: {known}"
            ) from exc
        if not isinstance(value, bool):
            raise CapabilityDescriptorError(
                f"capability {key!r} must be a boolean, got {type(value).__name__}"
            )
        features[feature] = value

    return CapabilityDescriptor(
        features=MappingProxyType(features),
        max_context_tokens=_coerce_positive_int(max_context_tokens, field="max_context_tokens"),
        max_output_tokens=_coerce_positive_int(max_output_tokens, field="max_output_tokens"),
    )


# Adapter baselines are static for the life of the process (they are class
# attributes on registered adapter classes), so resolve each runtime_type once.
_adapter_baseline_cache: dict[str, CapabilityDescriptor] = {}


def clear_adapter_capability_cache() -> None:
    """Drop the memoized adapter baselines (tests that register fake adapters)."""
    _adapter_baseline_cache.clear()


def adapter_capability_baseline(runtime_type: str) -> CapabilityDescriptor:
    """Return what the adapter registered for *runtime_type* declares it can do.

    An unregistered ``runtime_type`` yields :data:`EMPTY_CAPABILITIES` -- every
    feature UNKNOWN. That is the fail-closed answer, not a fail-open one: a
    dispatch that *requires* a feature will exclude the candidate for lack of
    proof, while one that requires nothing is unaffected.

    A malformed declaration on an adapter class is a code defect, but it must not
    take the fleet down at resolution time: the offending key is dropped with a
    warning and the rest of the declaration still applies. ``test_runtime_adapter_
    capability_declarations`` asserts every in-tree adapter parses cleanly, so the
    defect is caught in CI rather than degraded silently in production.
    """
    cached = _adapter_baseline_cache.get(runtime_type)
    if cached is not None:
        return cached

    try:
        from butlers.core.runtimes import get_adapter

        adapter_cls = get_adapter(runtime_type)
    except (ImportError, ValueError):
        logger.debug(
            "No runtime adapter registered for runtime_type=%r; "
            "treating every capability as unknown",
            runtime_type,
        )
        _adapter_baseline_cache[runtime_type] = EMPTY_CAPABILITIES
        return EMPTY_CAPABILITIES

    baseline = capability_baseline_of_adapter(adapter_cls)
    _adapter_baseline_cache[runtime_type] = baseline
    return baseline


def capability_baseline_of_adapter(adapter_cls: type) -> CapabilityDescriptor:
    """Build the baseline descriptor for one adapter class.

    ``SESSION_RESUME`` is read from the pre-existing ``supports_resume`` class
    flag rather than re-declared, so the descriptor cannot drift from the flag the
    spawner actually branches on. An explicit ``declared_capabilities`` entry for
    ``session_resume`` still wins, for an adapter that needs to say something the
    flag cannot express.
    """
    features: dict[ModelFeature, bool] = {}

    supports_resume = getattr(adapter_cls, "supports_resume", None)
    if isinstance(supports_resume, bool):
        features[ModelFeature.SESSION_RESUME] = supports_resume

    declared = getattr(adapter_cls, "declared_capabilities", None) or {}
    if not isinstance(declared, Mapping):
        logger.warning(
            "Runtime adapter declared_capabilities is not a mapping; ignoring it (adapter=%s)",
            adapter_cls.__name__,
        )
        declared = {}

    for key, value in declared.items():
        if not isinstance(key, str):
            logger.warning(
                "Runtime adapter declared a non-string capability key; ignoring it (adapter=%s)",
                adapter_cls.__name__,
            )
            continue
        try:
            feature = ModelFeature(key)
        except ValueError:
            logger.warning(
                "Runtime adapter declared unknown capability %r; ignoring it (adapter=%s)",
                key,
                adapter_cls.__name__,
            )
            continue
        if not isinstance(value, bool):
            logger.warning(
                "Runtime adapter declared non-boolean capability %r; ignoring it (adapter=%s)",
                key,
                adapter_cls.__name__,
            )
            continue
        features[feature] = value

    return CapabilityDescriptor(features=MappingProxyType(features))


def effective_capabilities(
    runtime_type: str,
    raw_capabilities: object,
    *,
    max_context_tokens: object = None,
    max_output_tokens: object = None,
) -> CapabilityDescriptor:
    """Layer a catalog row's declared envelope over its adapter's baseline.

    Raises
    ------
    CapabilityDescriptorError
        Propagated from :func:`parse_capability_descriptor` when the row's stored
        envelope is malformed. Resolution callers catch this and exclude the row.
    """
    catalog_layer = parse_capability_descriptor(
        raw_capabilities,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
    )
    return catalog_layer.layered_over(adapter_capability_baseline(runtime_type))
