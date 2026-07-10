## Why

The "I/O model removal" requirement in the `contacts-identity` spec governs the
**module tool-naming contract** — module MCP tools are named in plain
`<channel>_<action>` form (e.g. `telegram_send_message`, `email_send_message`)
and the `Module` ABC carries no per-audience (`user_*` / `bot_*`) I/O descriptor
model. This is not a contacts-identity concern. It survived the
`retire-contacts-table-specs` archival (PR #3033) precisely *because* it is not
table-centric, and that change's Out-of-Scope note explicitly deferred its
relocation to a follow-up (this change, bead bu-8hh59).

The proper owner is the `core-modules` spec ("Module System"), which already
defines the `Module` abstract base class, `register_tools()`, tool-group
filtering, and "Channel Egress Ownership Enforcement" (the latter already
references plain names like `telegram_send_message` / `email_send_message` as
examples). The naming convention itself, however, is not stated as a normative
requirement in any spec today — it is only exemplified across `core-modules`,
`butler-base-spec`, and `module-telegram`. So this is a **move**, not a
retire-as-duplicate.

Verified against code before relocating (the requirement must describe reality):

- Plain names are live: `src/butlers/modules/telegram.py` registers
  `telegram_send_message` via `mcp.tool()(telegram_send_message)`;
  `src/butlers/modules/email.py` registers `email_send_message`. The tool
  function name *is* the plain `<channel>_<action>` tool name.
- The removed I/O-descriptor internals are confirmed absent from
  `src/butlers/`: no `ToolIODescriptor`, `user_inputs` / `user_outputs` /
  `bot_inputs` / `bot_outputs`, `_validate_tool_name`,
  `_validate_module_io_descriptors`, `_is_user_send_or_reply_tool`,
  `_with_default_gated_user_outputs`, `_CHANNEL_EGRESS_ACTIONS`, or
  `ModuleToolValidationError`.

## What Changes

- **`contacts-identity`: remove the "I/O model removal" requirement.** It is
  relocated, not deleted — a `## REMOVED Requirements` delta carries a `Reason`
  and a `Migration` note pointing at its new home in `core-modules`.

- **`contacts-identity`: fix the Purpose prose.** The Purpose block currently
  says "Three requirements survive here" and lists the tool-naming contract as
  one of them. After relocation only two survive (the owner-identity "Secret key
  renames" contract and the live "[TARGET-STATE] Contact search endpoint for
  typeahead"). The prose is updated to reflect this and to point at
  `core-modules` for the tool-naming contract.

- **`core-modules`: add "Module Tool Naming Convention".** An `## ADDED
  Requirements` delta states the contract in steady-state, target-state terms:
  plain `<channel>_<action>` names, no `user_*` / `bot_*` prefix model, the
  `Module` ABC defines no I/O descriptor methods, and the daemon carries no
  tool-I/O validation layer (the removed symbols stay absent). The two live
  scenarios ("Tool registered with plain name", "Module ABC does not require
  descriptor methods") are carried over, minimally adapted.

- **`core-modules`: fix pre-existing strict-validation drift (unblock
  archive).** Eleven of the spec's requirement statements state their contract
  in descriptive prose without a SHALL/MUST keyword, so `core-modules` fails
  `openspec validate --strict` today (independent of this change) and blocks
  merging the ADDED requirement. This change adds the keyword to each affected
  statement (e.g. "discovers" → "SHALL discover", "is called" → "SHALL be
  called", "are prohibited from registering" → "SHALL NOT register") with no
  change in meaning. This mirrors the `retire-contacts-table-specs` precedent,
  which fixed the same class of drift in `module-contacts`.

## Archive Note — deliberate scenario drop

The original requirement carried a third scenario, "Legacy tool names rejected",
asserting that a `user_*` / `bot_*`-prefixed tool call MUST fail with a warning
and a "tool name has changed" error. **That behavior is dead in code** — it was
enforced by `_validate_tool_name()` / `ModuleToolValidationError`, both of which
were removed in the same refactor and are confirmed absent from `src/butlers/`.
A legacy-prefixed name now simply resolves to no known tool; there is no
dedicated "tool name has changed" rejection path. Because a spec must describe
reality, this scenario is **intentionally not carried into `core-modules`**. It
is dropped here rather than relocated. No live code or test depends on it.

## Impact

- Specs only. No code, no migrations, no schema changes.
- `openspec/specs/contacts-identity/spec.md`: 1 requirement removed, Purpose
  prose corrected (three → two surviving requirements).
- `openspec/specs/core-modules/spec.md`: 1 requirement added; 11 pre-existing
  requirement statements gain a SHALL/MUST keyword (meaning-preserving strict-
  validation fix).
- No runtime or test impact: the moved contract is already the live reality, and
  the dropped scenario describes behavior that no longer exists.

## Out of Scope

- **Editing `butler-base-spec` or `module-telegram`.** They reference plain tool
  names as examples but do not own the naming contract; `core-modules` is the
  single normative owner and the others need no change.
- **Restoring the removed legacy-name rejection behavior.** The refactor that
  removed it is complete and intended; this change documents the current
  reality, it does not re-introduce a transitional guard.
