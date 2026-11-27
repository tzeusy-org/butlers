## Decision requested

Adopt the exact Rule 2 and architecture wording in [design.md](design.md), and
approve the accompanying scheduler acceptance for bu-dss7he.10. The
bu-i4khaz release authorizes this proposal. Adoption requires the owner's
approval of the linked text.

## Why

The core registry already dispatches Memory maintenance through the loaded
module, and the QA wrappers call the loaded QA module. Maintaining these
bindings in a hand-written cross-roster map and registering Memory defaults in
a module startup hook obscures who owns dispatch and schedule persistence.
Tying a handler declaration to its owner lets the core scheduler validate it
at synchronization, before a scheduled occurrence can reach the wrong owner
or discover a missing handler.

## Scope

The approved contract would move existing deterministic handler declarations
beside their module, roster or core owners. Core would assemble and validate
the dispatch map for each daemon and retain scheduling, dispatch and
lifecycle. [The source inventory](job-inventory.md) accounts for the current
handlers, defaults and API consumers on the reviewed base.

This proposal leaves the canonical doctrine, specs, schedules and runtime
unchanged while the owner considers the text. The implementation and its
runtime verification belong to bu-dss7he.10 after adoption. Chronicler's
projection adapter aggregation belongs to bu-qozovd.5.
