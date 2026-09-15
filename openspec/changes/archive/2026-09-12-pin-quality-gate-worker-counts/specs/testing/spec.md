## ADDED Requirements

### Requirement: Quality-Gate Targets State Their Own Execution Mode
Each quality-gate make target SHALL state its xdist worker count on its own command line rather than inheriting one. `pyproject.toml`'s `addopts` carries `-n 3 --dist loadfile` and is prepended to every pytest invocation in this repository, so an omitted `-n` is not "the default" — it is silently three workers, and a target whose meaning depends on the worker count cannot be read from its recipe.

#### Scenario: The serial gate target really is serial
- **WHEN** `make test-qg-serial` runs
- **THEN** the pytest process it launches resolves `-n` to `0`, with `--dist` `no`, an empty `tx` list, and no xdist distributed session registered
- **AND** the target passes `-n 0` explicitly to reach that state, because `-p no:xdist` would turn the `-n 3` inherited from `addopts` into an unrecognized-argument error instead of disabling it
- **BECAUSE** the target exists for order-dependent debugging, and parallel workers reshuffle exactly the execution order it is reached for

#### Scenario: The default gate target really is parallel
- **WHEN** `make test-qg` runs
- **THEN** the pytest process it launches registers an xdist distributed session with a nonzero worker count
- **AND** the serial target's explicit `-n 0` does not reach it

#### Scenario: The guard reads the merged value, never either half alone
- **WHEN** a test pins a gate target's execution mode
- **THEN** it obtains the target's arguments by expanding the recipe (`make -n`) rather than parsing Makefile variables, and obtains the effective `-n` from a real pytest process rather than from those arguments
- **BECAUSE** the effective value does not exist until pytest merges `addopts` with argv: a Makefile grep for `-n 0` would pass while `addopts` changed the answer, which is how the serial target's documented meaning was inverted without any diff appearing to touch it
