## MODIFIED Requirements

### Requirement: Module Registry with Auto-Discovery
The `ModuleRegistry` SHALL discover all concrete `Module` subclasses by walking the `butlers.modules` package tree via `pkgutil.walk_packages()`. Modules SHALL be registered by class, then instantiated when a butler's configuration is loaded. Calling default_registry() SHALL explicitly perform complete discovery. Installing on-demand roster import resolution or importing unrelated test infrastructure SHALL NOT perform full discovery. Healthy repeated discovery SHALL preserve concrete classes, sorted names, dependency behavior and the existing declared-only daemon startup boundary. A failed requested module import or different-class duplicate-name collision SHALL be visible rather than yielding an apparently complete registry; a failed newly-created module SHALL NOT remain as a successful cached discovery. The same re-exported class MAY be registered idempotently.

ID: REQ-core-modules-004
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Built-in modules discovered
- **WHEN** `default_registry()` is called
- **THEN** all concrete `Module` subclasses in `butlers.modules.*` are registered
- **AND** `available_modules` returns a sorted list of their names

#### Scenario: Duplicate module name rejected
- **WHEN** `register()` is called with a module class whose `name` property matches an already-registered module
- **THEN** a `ValueError` is raised

#### Scenario: Unrelated import does not discover all modules
- **WHEN** only the roster resolver or root test infrastructure is imported
- **THEN** roster modules are not executed solely to populate a global registry
- **AND** an explicit subsequent default_registry call discovers the full healthy module set

#### Scenario: Broken explicit discovery is visible and retryable
- **WHEN** a module body fails during an explicit discovery request
- **THEN** the failure names the source module and cannot be represented as complete healthy discovery
- **AND** repairing the source permits a clean retry without a partial cached-success entry
- **AND** ordinary post-discovery module startup failure isolation remains governed separately
