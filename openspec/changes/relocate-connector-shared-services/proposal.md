# Relocate connector shared services

## Why
Shared dispatch and registry vocabulary serve core, dashboard and roster consumers. The accepted bu-ep60gq.3 S2 relocation gives each one canonical home.

## What Changes
- Move the complete modern dispatcher into butlers.core.discretion_dispatcher and registry vocabulary into butlers.core.connector_registry_roles; atomically cut over imports without aliases.
- Define the wellness counter in the canonical Health wellness-ingest module with identical collector name/help/labels. Connector-only metrics lose its empty HELP/TYPE family; Health samples remain unchanged.
- Update only the dispatcher home in complete normative requirements, keeping every scenario, identity, authority and behavior.

## Impact
No retries, schema/role, provider, scheduling, dispatch or caller-authority change. Existing tests retain paths and assertions; no new capability. Other bu-ep60gq.3 slices and all six original criteria remain unfinished by this slice alone.
