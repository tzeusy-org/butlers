# Butler Skills

Each butler has a git-based config directory that serves as the working directory for spawned Claude Code (CC) instances. This directory contains the butler's personality instructions, runtime notes, skill definitions, and configuration. Skills are passive documentation and scripts that CC reads and acts on -- they are not auto-registered capabilities. CC discovers skills via the butler's CLAUDE.md listing or by browsing the filesystem, reads SKILL.md for instructions, and executes accordingly using MCP tools or bash.

---

## ADDED Requirements

### Requirement: Butler config directory structure

Every butler's config directory SHALL follow a standard layout. The directory MUST contain a `CLAUDE.md` file, an `AGENTS.md` file, a `skills/` subdirectory, and a `butler.toml` file. The `butler.toml` file is covered by the butler-daemon spec and is not specified here.

```
butler-name/
├── CLAUDE.md       # Butler personality/instructions (required for CC)
├── AGENTS.md       # Runtime agent notes (populated at runtime by CC)
├── skills/         # Skills available to runtime instances
│   ├── morning-briefing/
│   │   ├── SKILL.md     # Prompt template / instructions
│   │   └── run.py       # Script CC can invoke via bash
│   └── inbox-triage/
│       └── SKILL.md     # Prompt-only skill
└── butler.toml     # Butler config (covered in butler-daemon spec)
```

#### Scenario: Config directory contains all required files

WHEN a butler named "relationship" has a properly initialized config directory,
THEN the directory `relationship/` SHALL contain a `CLAUDE.md` file,
AND the directory SHALL contain an `AGENTS.md` file,
AND the directory SHALL contain a `skills/` subdirectory,
AND the directory SHALL contain a `butler.toml` file.

#### Scenario: CC working directory is the butler config directory

WHEN the LLM CLI Spawner spawns a runtime instance for a butler,
THEN the runtime instance's working directory SHALL be the butler's config directory,
AND CC SHALL be able to read all files within the directory via file access,
AND CC SHALL be able to list the `skills/` subdirectory via bash.

---

### Requirement: CLAUDE.md -- Butler personality

Each butler's config directory MUST contain a `CLAUDE.md` file. The LLM CLI Spawner SHALL read this file and pass its contents as the `system_prompt` parameter to the Claude Code SDK when spawning a runtime instance. CLAUDE.md defines the butler's identity, behavioral instructions, constraints, an overview of available tools, and guidance on when and how to use those tools.

CLAUDE.md SHOULD list available skills so that CC knows what is available without needing to browse the filesystem. Each listed skill SHOULD include the skill name and a brief description of its purpose.

If CLAUDE.md is missing or empty, the LLM CLI Spawner SHALL spawn CC with a minimal default system prompt that identifies the butler by name only, in the form: `"You are the <name> butler."`.

#### Scenario: CLAUDE.md is passed as system prompt

WHEN the LLM CLI Spawner spawns a runtime instance for a butler whose `CLAUDE.md` contains the text "You are the Health butler. You track daily health metrics.",
THEN the `system_prompt` parameter passed to `claude_code_sdk.query` SHALL be "You are the Health butler. You track daily health metrics.".

#### Scenario: CLAUDE.md lists available skills

WHEN a butler's `CLAUDE.md` contains a section listing skills such as "morning-briefing: Generates a daily health summary" and "log-measurement: Records a new health measurement",
THEN CC SHALL be able to identify those skills and their purpose from the system prompt without reading the filesystem.

#### Scenario: Missing CLAUDE.md triggers default system prompt

WHEN the LLM CLI Spawner attempts to read `CLAUDE.md` from a butler named "general" and the file does not exist,
THEN the LLM CLI Spawner SHALL use the default system prompt "You are the general butler.",
AND the runtime instance SHALL still be spawned successfully.

#### Scenario: Empty CLAUDE.md triggers default system prompt

WHEN a butler named "health" has a `CLAUDE.md` file that exists but is empty (zero bytes),
THEN the LLM CLI Spawner SHALL use the default system prompt "You are the health butler.",
AND the runtime instance SHALL still be spawned successfully.

#### Scenario: CLAUDE.md contains tool usage guidance

WHEN a butler's `CLAUDE.md` contains instructions such as "Use the state_set tool to persist data between sessions" and "Always consult AGENTS.md before starting a task",
THEN CC SHALL receive these instructions as part of its system prompt and follow them during execution.

---

### Requirement: AGENTS.md -- Runtime notes

Each butler's config directory MUST contain an `AGENTS.md` file. This file MAY be initially empty. runtime instances can write to this file to persist notes, learnings, preferences, and patterns discovered across sessions.

AGENTS.md SHALL NOT be passed as part of the `system_prompt` to the CC SDK. It is accessible to CC via file read within the working directory. CC is instructed via CLAUDE.md to consult and update AGENTS.md as needed.

In v1, multiple runtime sessions MAY write to AGENTS.md. Concurrent writes SHALL use last-write-wins semantics with no merge conflict handling.

#### Scenario: CC reads AGENTS.md from the working directory

WHEN a runtime instance is spawned for a butler whose `AGENTS.md` contains "User prefers metric units for health data",
THEN CC SHALL be able to read `AGENTS.md` via file read,
AND CC SHALL see the content "User prefers metric units for health data".

#### Scenario: CC writes to AGENTS.md to persist learnings

WHEN a runtime instance discovers that the user prefers morning briefings before 8am,
THEN CC SHALL be able to write to `AGENTS.md` to append this learning,
AND subsequent runtime sessions SHALL be able to read this note from the file.

#### Scenario: AGENTS.md is not included in system prompt

WHEN the LLM CLI Spawner reads the butler's `CLAUDE.md` and spawns a runtime instance,
THEN the `system_prompt` parameter SHALL contain only the contents of `CLAUDE.md`,
AND the contents of `AGENTS.md` SHALL NOT be included in the `system_prompt`.

#### Scenario: Last-write-wins on concurrent updates

WHEN two runtime sessions run sequentially and both write to `AGENTS.md`,
THEN the file SHALL contain the content written by the session that wrote last,
AND no merge conflict resolution SHALL be attempted.

#### Scenario: Empty AGENTS.md does not cause errors

WHEN a butler's `AGENTS.md` file exists but is empty,
THEN CC SHALL be able to read the file without error,
AND CC SHALL be able to write new content to it.

---

### Requirement: Skill directory structure

Skills SHALL live in subdirectories under `skills/` within the butler's config directory. Each skill directory MUST contain a `SKILL.md` file that serves as the skill's prompt template and instructions. Skill directories MAY contain additional files such as scripts (`run.py`, `run.sh`), data files, and templates.

Skill directory names MUST use kebab-case (lowercase letters, digits, and hyphens only). Names MUST start with a letter and MUST NOT contain consecutive hyphens.

SKILL.md SHALL be free-form markdown. It typically contains the skill's purpose, step-by-step instructions, expected input and output format, and examples.

Skills are NOT automatically loaded or registered by the butler daemon. They are passive resources that CC discovers and reads at runtime.

#### Scenario: Skill directory with SKILL.md and a script

WHEN a butler has a skill at `skills/morning-briefing/`,
THEN the directory SHALL contain a `SKILL.md` file,
AND the directory MAY contain a `run.py` script,
AND the directory MAY contain additional files such as templates or data files.

#### Scenario: Skill directory with SKILL.md only (prompt-only skill)

WHEN a butler has a skill at `skills/inbox-triage/` that contains only a `SKILL.md` file,
THEN the skill SHALL be a valid prompt-only skill,
AND CC SHALL be able to use it by reading `SKILL.md` and following its instructions using MCP tools alone.

#### Scenario: Skill directory missing SKILL.md is invalid

WHEN a butler has a directory `skills/broken-skill/` that does not contain a `SKILL.md` file,
THEN CC SHOULD treat the directory as an incomplete or invalid skill,
AND CC SHOULD NOT attempt to execute the skill.

#### Scenario: Skill name uses valid kebab-case

WHEN a skill directory is named `daily-health-check`,
THEN the name SHALL be considered valid because it uses lowercase letters and hyphens only and starts with a letter.

#### Scenario: Skill name with invalid characters is rejected

WHEN `butlers init` or a manual setup creates a skill directory named `Morning_Briefing` or `morning briefing`,
THEN the name SHALL be considered invalid because it does not conform to kebab-case,
AND the framework SHOULD warn or reject the name at the earliest opportunity.

#### Scenario: SKILL.md contains structured instructions

WHEN a skill's `SKILL.md` contains a purpose section, step-by-step instructions, an input/output format, and examples,
THEN CC SHALL be able to parse and follow these instructions to execute the skill.

---

### Requirement: CC skill discovery

The primary discovery mechanism for skills SHALL be the butler's CLAUDE.md file. CLAUDE.md SHOULD list available skills by name and purpose so that CC knows what skills exist without filesystem exploration.

CC MAY also discover skills by listing the `skills/` directory via bash (e.g., `ls skills/`). After discovering a skill, CC SHALL read the skill's `SKILL.md` to understand what to do, then execute the skill by calling MCP tools, running scripts, or a combination of both.

Skills are passive -- they are documentation and scripts that CC reads and acts on. They are not auto-registered capabilities, auto-loaded plugins, or MCP tools.

#### Scenario: CC discovers skills from CLAUDE.md

WHEN a butler's `CLAUDE.md` lists "morning-briefing: Generates a daily summary of health metrics" and "log-measurement: Records a new health data point",
THEN CC SHALL know that these two skills are available,
AND CC SHALL be able to read `skills/morning-briefing/SKILL.md` or `skills/log-measurement/SKILL.md` to get detailed instructions.

#### Scenario: CC discovers skills by listing the directory

WHEN CC runs `ls skills/` via bash,
THEN the output SHALL list all skill directories (e.g., `morning-briefing`, `inbox-triage`),
AND CC SHALL be able to read the `SKILL.md` in any listed directory.

#### Scenario: CC reads SKILL.md before executing a skill

WHEN CC decides to use the "morning-briefing" skill,
THEN CC SHALL first read `skills/morning-briefing/SKILL.md`,
AND CC SHALL follow the instructions in SKILL.md to execute the skill.

#### Scenario: Skills not listed in CLAUDE.md are still discoverable

WHEN a skill directory exists at `skills/cleanup-old-data/` but is not mentioned in CLAUDE.md,
THEN CC SHALL still be able to discover it by listing the `skills/` directory,
AND CC SHALL be able to read its SKILL.md and execute it.

#### Scenario: Empty skills directory

WHEN a butler has a `skills/` directory that contains no subdirectories,
THEN CC listing `skills/` SHALL see an empty directory,
AND CC SHALL not attempt to execute any skills.

---

### Requirement: Skill scripts

Skill directories MAY contain executable scripts that CC can invoke via bash. Scripts run within the runtime instance's bash environment with the butler's config directory as the working directory.

Scripts SHALL have access to environment variables passed through by the LLM CLI Spawner (including `TRACEPARENT` if set and any butler-specific environment variables).

Scripts SHOULD be self-contained and limit dependencies to the Python standard library or dependencies that are explicitly documented in the skill's SKILL.md. If a script requires external packages, SKILL.md MUST document this requirement.

Script output on stdout SHALL be captured by CC and MAY be used in subsequent tool calls, reasoning, or responses. Script output on stderr SHALL also be visible to CC for error handling.

#### Scenario: CC executes a Python skill script

WHEN CC decides to run the "morning-briefing" skill and `skills/morning-briefing/run.py` exists,
THEN CC SHALL execute it via bash as `python skills/morning-briefing/run.py`,
AND the script SHALL run with the butler's config directory as the working directory.

#### Scenario: CC executes a shell skill script

WHEN CC decides to run a skill that contains a `run.sh` script,
THEN CC SHALL execute it via bash as `bash skills/<skill-name>/run.sh`,
AND the script SHALL run with the butler's config directory as the working directory.

#### Scenario: Script accesses environment variables

WHEN the LLM CLI Spawner has set environment variables including `TRACEPARENT` and butler-specific variables,
THEN a skill script executed by CC SHALL have access to those environment variables via `os.environ` (Python) or `$VAR` (shell).

#### Scenario: Script output captured by CC

WHEN a skill script writes "Summary: 3 tasks completed, 1 overdue" to stdout,
THEN CC SHALL receive this output,
AND CC MAY use it in subsequent MCP tool calls or in its response.

#### Scenario: Script with no external dependencies

WHEN a skill script `run.py` uses only Python standard library modules (e.g., `json`, `datetime`, `os`),
THEN the script SHALL execute successfully without any additional package installation.

#### Scenario: Script with documented external dependency

WHEN a skill's `SKILL.md` documents that `run.py` requires the `requests` package,
THEN the dependency MUST be documented in SKILL.md,
AND the framework SHALL NOT automatically install the dependency.

#### Scenario: Script failure is visible to CC

WHEN a skill script exits with a non-zero exit code and writes an error message to stderr,
THEN CC SHALL see the non-zero exit code and the stderr output,
AND CC SHALL be able to handle the error (e.g., retry, report, or skip).

#### Scenario: Script receives arguments from CC

WHEN CC invokes a skill script with arguments such as `python skills/morning-briefing/run.py --date 2026-02-09`,
THEN the script SHALL receive the arguments via `sys.argv`,
AND the script SHALL be able to parse and use them.

---

### Requirement: butlers init scaffolding

The `butlers init <name>` command SHALL scaffold a new butler config directory with the required files for skills and CC integration. This requirement extends the `butlers init` command defined in the CLI and deployment spec.

#### Scenario: butlers init creates CLAUDE.md with placeholder

WHEN `butlers init mybutler --port 40104` is invoked,
THEN the directory `butlers/mybutler/` SHALL contain a `CLAUDE.md` file,
AND the file SHALL contain a placeholder comment such as `<!-- Define this butler's personality, instructions, and available skills here -->`,
AND the file MUST NOT be empty (it SHALL contain the placeholder to guide the user).

#### Scenario: butlers init creates empty AGENTS.md

WHEN `butlers init mybutler --port 40104` is invoked,
THEN the directory `butlers/mybutler/` SHALL contain an `AGENTS.md` file,
AND the file SHALL be empty or contain a minimal placeholder comment such as `<!-- Runtime notes populated by runtime sessions -->`.

#### Scenario: butlers init creates empty skills directory

WHEN `butlers init mybutler --port 40104` is invoked,
THEN the directory `butlers/mybutler/` SHALL contain an empty `skills/` subdirectory,
AND the directory SHALL contain no skill subdirectories.

#### Scenario: Scaffolded butler is immediately startable

WHEN `butlers init mybutler --port 40104` completes,
THEN `butlers run --config butlers/mybutler` SHALL start the butler successfully,
AND the LLM CLI Spawner SHALL use the default system prompt derived from the placeholder CLAUDE.md,
AND the empty `skills/` directory SHALL not cause any errors.

#### Scenario: butlers init does not create sample skills

WHEN `butlers init mybutler --port 40104` is invoked,
THEN the `skills/` directory SHALL be empty,
AND no sample or template skill directories SHALL be created.
