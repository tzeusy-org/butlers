# Subagent Prompt Templates

Copy-ready dispatch prompts for the per-butler / per-module subagents used in
[SKILL.md](../SKILL.md) Phases 1, 2, 3, and 7.

**Inventory agent (per butler, Phase 1):**
```
Read roster/{butler}/butler.toml. List all enabled modules with their
configured groups. Query effective runtime_config and/or list the live MCP
surface before calling a core count actual; runtime_seed is first-boot input,
not live-state evidence. If live state is unavailable, label the result a
configured maximum/source inventory. For each module, collect the tools that
would register with the effective groups config. Report as a markdown table.
Core daemon tools: inspect `butlers.core_tools.register_all_core_tools()` and
the owning registration functions. Count group-decorated and direct tools for
the butler's type and name; use `references/tool-budget.md` as the current
inventory guide rather than constructing a second name catalog.
```

**Docstring audit agent (per module, Phase 2):**
```
Read {module_file}. For each @mcp.tool() or @_tool() decorated function,
assess the docstring against these criteria:
1. Clear purpose line (first sentence)
2. All parameters documented with types and valid values
3. Return schema described
4. LLM guidance on when to use this tool vs alternatives
Rate each GOOD/NEEDS_WORK/MISSING. List specific issues per tool.
Report as a markdown table.
```

**Error audit agent (per module, Phase 3):**
```
Read {module_file}. For each tool function, find all error return paths
({"status": "error"}, raise, except blocks). For each error:
1. Is the message actionable? (tells LLM what to fix)
2. Is it specific? (names the bad param/value)
3. Does it indicate retryability?
4. Does it avoid bare str(exc) without context?
Rate each GOOD/BAD. Report as a markdown table with the error path
description and specific issues.
```

**Historical usage audit agent (per butler, Phase 7):**
```
Read and follow `references/historical-usage-audit.md` in full. It is the
single canonical source for connection handling, supported call-shape SQL,
inventory/alias resolution, and removal-evidence rules.
```
