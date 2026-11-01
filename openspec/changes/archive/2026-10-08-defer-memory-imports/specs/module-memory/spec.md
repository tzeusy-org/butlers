## ADDED Requirements

### Requirement: Lazy Canonical Embedding Construction
Memory module and storage/tool imports SHALL defer sentence_transformers, torch and transformers until first actual EmbeddingEngine construction. The engine SHALL preserve the configured/default model,384-dimensional output, normalization and batch behavior, public tools and synchronized per-model cache. Sibling memory modules SHALL use canonical package identities with one body execution per process and the helper construction fake seam SHALL remain usable. Async callers SHALL acquire a first engine without newly blocking their event loop with dependency/model construction.

ID: REQ-module-memory-014
Source: bu-ly3lv5.4 released by bu-7lh5ew; existing memory storage/model/cache/source behavior; performance-discipline
Scope: v1-mandatory

#### Scenario: Unused import leaves the embedding stack absent
- **WHEN** memory storage or tools are imported without engine construction
- **THEN** sentence_transformers, transformers and torch remain absent from sys.modules
- **AND** public memory tools and configured model metadata remain available

#### Scenario: First use constructs the selected model
- **WHEN** a genuine engine request first selects a model
- **THEN** construction pays the deferred dependency/model cost and returns the same model/dimension/normalization behavior
- **AND** later requests for that model reuse its synchronized cached engine

#### Scenario: Construction failure cannot cache success
- **WHEN** engine construction raises
- **THEN** no successful cache entry is recorded for that failed construction
- **AND** a later valid request may construct normally

#### Scenario: Canonical modules retain actual identity
- **WHEN** normal imports and memory helper aliases reference storage, search, embedding or search_vector
- **THEN** each reference selects its corresponding canonical module/class/function object without re-executing a sibling file
- **AND** existing no-LLM transitive checks retain their real banned-edge enforcement

#### Scenario: Concurrent and cancelled first requests keep one identity
- **WHEN** async first-acquisition callers overlap or a waiter is cancelled
- **THEN** construction follows the existing synchronized cache and asynchronous acquisition boundary
- **AND** a still-running worker is not falsely declared cancelled or duplicated merely because its waiter stopped
