"""Validate dark declarations against final, approval-wrapped tool registries.

This is an in-process value projection, not an authenticated fleet publication
or a production CatalogProvider. No admission or runtime switch is called here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from butlers.core.goal_admission import Capability, CatalogView, digest
from butlers.core.obligations import (
    DeclarationError,
    ObligationDeclaration,
    ParsedDeclaration,
    parse_declaration,
    project_loaded_schedule,
    qualify_capability,
)
from butlers.core.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    from butlers.config import ScheduleSpec, SubscriptionSpec

logger = logging.getLogger(__name__)


class DeclarationValidationError(ValueError):
    """Catalog or declaration cannot be used after the switch."""


@dataclass(frozen=True)
class ObligationCatalog:
    view: CatalogView
    resource_parameters: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class DeclarationIssue:
    # Index, not the source name or supplied field values, is safe to log.
    index: int
    code: str


@dataclass(frozen=True)
class DeclarationValidation:
    declarations: tuple[tuple[str, ObligationDeclaration], ...]
    issues: tuple[DeclarationIssue, ...]


def build_obligation_catalog(registries: Mapping[str, ToolCatalog]) -> ObligationCatalog:
    """Project exact qualified IDs from actual immutable final registrations.

    Conditional recipient prose is not an executable owner-default predicate.
    This projection conservatively sets owner_default=False. The real publisher
    and broker still own authenticated freshness and per-call owner resolution.
    """
    if not registries:
        raise DeclarationValidationError("catalog_unavailable")
    capabilities = []
    resource_parameters = []
    generations = []
    for domain, registry in sorted(registries.items()):
        if (
            not isinstance(domain, str)
            or not isinstance(registry, ToolCatalog)
            or not registry.classification_complete
            or not registry.generation_digest
        ):
            raise DeclarationValidationError("catalog_unavailable")
        generations.append((domain, registry.generation_digest))
        for descriptor in registry.descriptors:
            if descriptor.owner_domain != domain or (
                descriptor.llm_presentable and not descriptor.authorization_complete
            ):
                raise DeclarationValidationError("catalog_unavailable")
            if not descriptor.grantable:
                continue
            qualified = qualify_capability(domain, descriptor.canonical_name)
            capabilities.append(
                Capability(
                    id=qualified,
                    owner_domain=domain,
                    effect_class=descriptor.effect_class,
                    owner_authority=descriptor.owner_authority,
                    owner_default=False,
                    resource_params=tuple(descriptor.resource_params),
                )
            )
            resource_parameters.append((qualified, tuple(descriptor.resource_params)))
    return ObligationCatalog(
        CatalogView(digest({"version": 1, "registries": generations}), tuple(capabilities)),
        tuple(resource_parameters),
    )


def validate_declarations(
    values: Mapping[str, object],
    catalog: ObligationCatalog | None,
    *,
    after_switch: bool,
) -> DeclarationValidation:
    """Warn dark; refuse after the switch. Invalid entries are never returned.

    The caller must supply the complete due-source inventory and a current
    verified catalog. This function neither discovers sources nor toggles mode.
    """
    if type(after_switch) is not bool:
        raise TypeError("after_switch must be bool")
    issues = []
    valid = []
    eligible = dict(catalog.resource_parameters) if catalog is not None else {}
    for index, (source, raw) in enumerate(values.items()):
        if raw is None:
            issues.append(DeclarationIssue(index, "missing_declaration"))
            continue
        try:
            declaration = parse_declaration(raw)
        except DeclarationError:
            issues.append(DeclarationIssue(index, "invalid_declaration"))
            continue
        if catalog is None:
            issues.append(DeclarationIssue(index, "catalog_unavailable"))
            continue
        bad = False
        for grant in declaration.capabilities:
            if grant.capability not in eligible:
                issues.append(DeclarationIssue(index, "unknown_capability"))
                bad = True
            elif set(dict(grant.resources)) - set(eligible[grant.capability]):
                issues.append(DeclarationIssue(index, "unknown_resource"))
                bad = True
        if not bad:
            valid.append((source, declaration))
    if issues and after_switch:
        raise DeclarationValidationError("obligation_declarations_invalid")
    for issue in issues:
        logger.warning("Obligation declaration index=%d reason=%s", issue.index, issue.code)
    return DeclarationValidation(tuple(valid), tuple(issues))


def diagnose_loaded_schedules(
    schedules: Sequence[ScheduleSpec], *, owner_domain: str, local_catalog: ToolCatalog
) -> tuple[DeclarationIssue, ...]:
    """Run actual startup diagnostics, without claiming a verified fleet view.

    Called only after final approval registration. Local metadata can disprove a
    local grant; it cannot validate remote grants or establish source authority.
    There is no trusted switched control/catalog reader here yet: healthy local
    syntax still reports fleet unavailability and never enables admission.
    """
    return _diagnose_loaded_declarations(
        [
            (index, project_loaded_schedule(spec).declaration)
            for index, spec in enumerate(schedules)
            if spec.dispatch_mode == "prompt"
        ],
        owner_domain=owner_domain,
        local_catalog=local_catalog,
    )


def diagnose_loaded_job_schedules(
    schedules: Sequence[ScheduleSpec],
    *,
    owner_domain: str,
    local_catalog: ToolCatalog,
    job_handlers: Mapping[str, object],
) -> tuple[DeclarationIssue, ...]:
    """Consume the dispatcher's actual handlers after final tool registration.

    Job mode is not a model exemption. Known model-backed handlers undergo the
    SAME parsed declaration/local-catalog checks as prompts. Unknown handlers
    remain unclassified; neither a familiar name nor an absent direct model
    call proves a closed deterministic graph. Explicit safety/context handlers
    stay service-plane jobs, without an admission or model dependency.

    This dark diagnostic changes no dispatch and authenticates no creator,
    source, fleet publication or cause. Even valid local model-job declarations
    retain fleet_catalog_unavailable until the real publisher/handoff exists.
    """
    from butlers.scheduled_jobs import classify_schedule_job

    issues = []
    model_values = []
    for index, spec in enumerate(schedules):
        if spec.dispatch_mode != "job":
            continue
        handler = job_handlers.get(spec.job_name)
        if handler is None:
            code = "unregistered_job"
        else:
            classification = classify_schedule_job(handler)
            if classification == "model_backed":
                model_values.append((index, spec.obligation))
                continue
            if classification == "deterministic":
                continue
            code = "job_semantics_unclassified"
        issue = DeclarationIssue(index, code)
        issues.append(issue)
        logger.warning("Obligation declaration index=%d reason=%s", issue.index, issue.code)
    issues.extend(
        _diagnose_loaded_declarations(
            model_values, owner_domain=owner_domain, local_catalog=local_catalog
        )
    )
    return tuple(sorted(issues, key=lambda issue: issue.index))


def diagnose_loaded_subscriptions(
    subscriptions: Sequence[SubscriptionSpec], *, owner_domain: str, local_catalog: ToolCatalog
) -> tuple[DeclarationIssue, ...]:
    """Inspect actual Git subscriptions after final registration, without IO.

    This does not subscribe to a bus, authenticate an envelope, admit a goal or
    prove a runtime creator. No declaration means no future feed admission;
    today's legacy dispatch remains unchanged while the switch is OFF.
    """
    return _diagnose_loaded_declarations(
        [(index, spec.obligation) for index, spec in enumerate(subscriptions)],
        owner_domain=owner_domain,
        local_catalog=local_catalog,
    )


def _diagnose_loaded_declarations(
    values: Sequence[tuple[int, ParsedDeclaration]],
    *,
    owner_domain: str,
    local_catalog: ToolCatalog,
) -> tuple[DeclarationIssue, ...]:
    try:
        local = build_obligation_catalog({owner_domain: local_catalog})
    except DeclarationValidationError:
        local = None
    resources = dict(local.resource_parameters) if local is not None else {}
    issues = []
    for index, parsed in values:
        if parsed.status != "valid":
            code = f"{parsed.status}_declaration"
        elif local is None:
            code = "catalog_unavailable"
        else:
            code = "fleet_catalog_unavailable"
            for grant in parsed.declaration.capabilities:
                if not grant.capability.startswith(f"{owner_domain}__"):
                    continue
                if grant.capability not in resources:
                    code = "unknown_capability"
                    break
                if set(dict(grant.resources)) - set(resources[grant.capability]):
                    code = "unknown_resource"
                    break
        issue = DeclarationIssue(index, code)
        issues.append(issue)
        logger.warning("Obligation declaration index=%d reason=%s", issue.index, issue.code)
    return tuple(issues)
