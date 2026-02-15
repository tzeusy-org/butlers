# Research Decisions: roster/{butler}/api/ Conventions

**Issue:** butlers-920.1  
**Date:** 2026-02-15  
**Status:** Complete

## Executive Summary

All 8 research questions have been resolved with documented decisions. A proof-of-concept validates that FastAPI routers can be loaded from `roster/` via `importlib`, import shared models from `src/butlers/api/models`, and work correctly with FastAPI's dependency injection.

---

## Question 1: File Naming Convention

**Decision:** Use `router.py` as the canonical filename.

**Rationale:**
- Existing routers in `src/butlers/api/routers/` all use singular nouns (e.g., `approvals.py`, `health.py`, `sessions.py`)
- FastAPI examples and documentation commonly use `router.py` for modules that export an `APIRouter` instance
- `routes.py` (plural) suggests multiple route definitions, but we're exporting a single router object
- `views.py` is a Django convention that doesn't apply here
- Singular naming aligns with Python module naming conventions (one module, one primary export)

**Convention:** `roster/{butler}/api/router.py`

---

## Question 2: Models Co-location

**Decision:** Use `models.py` alongside `router.py` when butler-specific models exist.

**Rationale:**
- Keeps butler-specific Pydantic models close to the routers that use them
- Matches the existing pattern in `src/butlers/api/models/` where each butler has its own models file
- Simple, flat structure avoids overengineering for typical butler API surface area
- If a butler's API grows significantly, it can later be split into `models/` subpackage without breaking the discovery pattern

**Convention:** `roster/{butler}/api/models.py` (optional, only when needed)

**Import pattern:**
```python
# In router.py
from .models import ButlerSpecificModel  # relative import
from butlers.api.models import ApiResponse, PaginatedResponse  # shared models
```

---

## Question 3: `__init__.py` Requirement

**Decision:** No `__init__.py` required. Use bare `.py` files.

**Rationale:**
- `importlib.util.spec_from_file_location` does not require the target to be part of a package
- Keeping `roster/{butler}/api/` as a directory (not a package) reinforces that it's configuration, not installed code
- Simpler discovery logic: just check for `router.py` existence
- Consistent with the rest of `roster/` which is not a Python package

**Convention:** `roster/{butler}/api/` contains bare Python files, no `__init__.py`

---

## Question 4: Import Resolution Validation

**Decision:** Use `importlib.util.spec_from_file_location` with `sys.modules` registration.

**Rationale:**
- **Proof-of-concept validates this works correctly:**
  - Routers load successfully from `roster/{butler}/api/router.py`
  - Shared models (`ApiResponse`, `PaginatedResponse`) import correctly
  - FastAPI `Depends()` works as expected
  - No sys.path pollution required
- **Pattern already used in the codebase:**
  - `src/butlers/modules/memory/tools/_helpers.py` uses this exact pattern
  - Proven to work for dynamic module loading across the project

**Implementation pattern:**
```python
import importlib.util
import sys
from pathlib import Path

def load_router_module(router_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {router_path}")
    
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # Required for imports to resolve
    spec.loader.exec_module(module)
    return module
```

**Module naming convention:** Use `{butler_name}_api_router` as the module name to ensure uniqueness in `sys.modules`.

---

## Question 5: Shared Model Dependencies

**Decision:** Routers import shared models directly from `butlers.api.models`.

**Rationale:**
- **Proof-of-concept confirms this works:** The health butler router successfully imports and uses `ApiResponse` from `butlers.api.models`
- Shared models (`ApiResponse`, `PaginatedResponse`, `PaginationMeta`, `Issue`, etc.) remain in `src/butlers/api/models/__init__.py`
- Butler-specific models go in `roster/{butler}/api/models.py`
- Clear separation of concerns: shared infrastructure vs. butler-specific data models

**Import paths that work from dynamically-loaded modules:**
```python
from butlers.api.models import ApiResponse, PaginatedResponse  # ✓ works
from butlers.api.db import DatabaseManager  # ✓ works
from butlers.api.deps import get_mcp_manager  # ✓ works
from fastapi import APIRouter, Depends  # ✓ works
```

---

## Question 6: Switchboard Model Location

**Decision:** Move `RegistryEntry` and `RoutingEntry` to `roster/switchboard/api/models.py`.

**Rationale:**
- These models are currently in `src/butlers/api/models/general.py` but are 100% switchboard-specific
- They're only used by `src/butlers/api/routers/switchboard_views.py`
- The general butler has separate models (`Collection`, `Entity`) in the same file, but that's a current quirk, not a design goal
- Moving them to `roster/switchboard/api/models.py` follows the isolation principle
- `src/butlers/api/models/general.py` will only contain `Collection` and `Entity` (actual general butler models)

**Migration plan:**
1. Create `roster/switchboard/api/models.py` with `RegistryEntry` and `RoutingEntry`
2. Update `roster/switchboard/api/router.py` to import from `.models`
3. Remove these models from `src/butlers/api/models/general.py`
4. Update any tests that import these models

---

## Question 7: Test Location

**Decision:** Router tests move to `roster/{butler}/tests/` following the butlers-vyn pattern.

**Rationale:**
- **Consistency with butlers-vyn:** Tools, migrations, and tool tests are already co-located in `roster/{butler}/`
- **Example from existing code:** `roster/switchboard/tests/test_ingest_api.py` demonstrates butler-specific integration tests living in roster
- **Current state analysis:**
  - `tests/api/` contains 44 test files, including `test_switchboard_views.py`, `test_api_relationship.py`, `test_general.py`
  - These test butler-specific routers and should move with their routers
  - Generic/infrastructure tests (like `test_app.py`, `test_deps.py`, `test_db.py`) stay in `tests/api/`
- **Benefits:**
  - Full butler isolation: add a new butler, all its code + tests in one directory
  - Easier to run tests for a specific butler: `pytest roster/health/tests/`
  - Aligns with the manifesto-driven design principle

**Convention:**
- Butler-specific router tests: `roster/{butler}/tests/test_api_*.py` or `roster/{butler}/tests/test_router.py`
- Generic API infrastructure tests: `tests/api/test_*.py`

**Migration tasks for each butler:**
- Move `tests/api/test_{butler}.py` → `roster/{butler}/tests/test_api.py`
- Update imports to use the dynamically-loaded router module

---

## Question 8: Router Variable and Prefix Convention

**Decision:** Auto-discovery expects a module-level variable named `router` (an `APIRouter` instance).

**Rationale:**
- **Simplicity:** Single, well-known variable name is easier to discover and document
- **Precedent:** All existing routers in `src/butlers/api/routers/` export `router = APIRouter(...)`
- **Predictability:** Developers know exactly what to export
- **Type safety:** Can type-check `hasattr(module, 'router')` and `isinstance(module.router, APIRouter)`

**Auto-discovery logic:**
```python
def discover_butler_routers(roster_dir: Path) -> list[tuple[str, Any]]:
    """Discover all roster/{butler}/api/router.py and load them.
    
    Returns list of (butler_name, router_instance) tuples.
    """
    routers = []
    for butler_dir in sorted(roster_dir.iterdir()):
        if not butler_dir.is_dir():
            continue
        
        router_path = butler_dir / "api" / "router.py"
        if not router_path.exists():
            continue
        
        module_name = f"{butler_dir.name}_api_router"
        module = load_router_module(router_path, module_name)
        
        if hasattr(module, "router") and isinstance(module.router, APIRouter):
            routers.append((butler_dir.name, module.router))
        else:
            logger.warning(
                "Router module %s does not export 'router' (APIRouter instance)",
                router_path
            )
    
    return routers
```

**Prefix convention:** Each router should set its own prefix to avoid conflicts:
```python
router = APIRouter(prefix="/api/{butler-name}", tags=["{butler-name}"])
```

---

## Proof of Concept Results

The PoC script (`poc_router_loading.py`) validates all decisions:

✓ `importlib.util.spec_from_file_location` works for roster/ routers  
✓ FastAPI routers load and function correctly  
✓ Shared models from `src/butlers/api/models` import successfully  
✓ Butler-specific models can be co-located in `api/models.py`  
✓ Router discovery pattern is straightforward  
✓ Endpoints respond correctly via `TestClient`  

**Test output:**
```
GET /api/health-butler/vitals
Status: 200
Response: {'data': {'status': 'healthy', 'uptime_seconds': 12345, 'checks_passed': 42}, 'meta': {}}

GET /api/health-butler/checks
Status: 200
Response: {'data': ['database_connectivity', 'mcp_server_status', 'task_scheduler_active'], 'meta': {}}
```

---

## Summary of Conventions

| Question | Decision |
|----------|----------|
| **1. File naming** | `router.py` (singular, canonical) |
| **2. Models co-location** | `models.py` alongside `router.py` (optional) |
| **3. `__init__.py`** | Not needed (bare `.py` files) |
| **4. Import resolution** | `importlib.util.spec_from_file_location` + `sys.modules` registration |
| **5. Shared models** | Import from `butlers.api.models` (works correctly) |
| **6. Switchboard models** | Move to `roster/switchboard/api/models.py` |
| **7. Test location** | `roster/{butler}/tests/test_api*.py` (follow butlers-vyn) |
| **8. Router variable** | Module-level `router` (APIRouter instance) |

---

## Next Steps (for butlers-920.2)

The auto-discovery infrastructure in `src/butlers/api/app.py` should:

1. Scan `roster/` for all `{butler}/api/router.py` files
2. Load each using `importlib.util.spec_from_file_location`
3. Extract the `router` variable (validate it's an `APIRouter`)
4. Include each router in the FastAPI app
5. Auto-wire DB dependencies (scan router for `_get_db_manager` and override)

This research validates that all pieces work correctly. Implementation can proceed with confidence.
