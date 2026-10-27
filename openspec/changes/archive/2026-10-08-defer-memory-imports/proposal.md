# Defer Memory and roster imports until demand

## Why
Memory sibling file loaders duplicate canonical module objects, and root pytest setup discovers all roster bodies before an actual consumer asks for one. Both paths pay embedding dependency import costs during unrelated work and conceal import identity and failure boundaries.

## What Changes
- Import the embedding backend at first engine construction, preserving configured models, 384 dimensions, encoding, public tools, fake construction, and synchronized per-model caching.
- Use canonical storage/search/embedding/search_vector modules. Acquire engines off the event loop from every registered async tool.
- Resolve supported roster module/job/API namespaces from their owning package, with explicit complete registry discovery and governed optional API skips. Root conftest retains the source guard and all fixtures without discovery/preloads.
- Keep deterministic API channel/attribution reads separate from mutation and semantic dependencies; preserve the actual Finder guard and router service seams.
- Enforce fresh-process absent-stack and a calibrated numeric conftest cap, with original/eager/slow causal reds and restored positives. No CI wall-clock gain is claimed.

## Impact
Owning Memory, registry, jobs, API discovery, root conftest, deterministic Relationship API helper boundaries, tests, and developer docs. No model, tool, dependency lock, schema, role, provider, CI corpus, or foreign change adoption.
