# Healing Anonymizer

## Purpose

Data sanitization pipeline that scrubs PII, credentials, user content, and environment-specific paths from error context before inclusion in PR descriptions, commit messages, or branch metadata. This is the hard safety gate for a public repository — no PR can be created without passing anonymization and validation.

## ADDED Requirements

### Requirement: Credential Redaction
The anonymizer SHALL scrub known credential patterns from all text. This extends the existing `CredentialRedactionFilter` pattern set with additional rules.

#### Scenario: API key patterns redacted
- **WHEN** text contains `sk-ant-api03-abc123...` or `AKIA1234567890ABCDEF`
- **THEN** they are replaced with `[REDACTED-API-KEY]`

#### Scenario: Database URLs redacted
- **WHEN** text contains `postgresql://user:password@host:5432/dbname`
- **THEN** it is replaced with `[REDACTED-DB-URL]`

#### Scenario: JWT tokens redacted
- **WHEN** text contains a JWT pattern (`eyJ...`)
- **THEN** it is replaced with `[REDACTED-JWT]`

#### Scenario: Existing redaction rules preserved
- **WHEN** text contains Telegram bot tokens or Bearer tokens
- **THEN** the existing `_REDACTION_RULES` patterns are applied in addition to new rules

### Requirement: PII Scrubbing
The anonymizer SHALL replace personally identifiable information with typed placeholders.

#### Scenario: Email addresses scrubbed (case-insensitive)
- **WHEN** text contains `user@example.com` or `User@Example.COM`
- **THEN** they are replaced with `[REDACTED-EMAIL]`
- **AND** the email regex is case-insensitive (email domains are case-insensitive per RFC)

#### Scenario: Phone numbers scrubbed
- **WHEN** text contains `+1-555-123-4567` or `(555) 123-4567`
- **THEN** it is replaced with `[REDACTED-PHONE]`

#### Scenario: IP addresses scrubbed
- **WHEN** text contains `192.168.1.100` or `2001:db8::1`
- **THEN** it is replaced with `[REDACTED-IP]`

#### Scenario: Localhost and loopback IPs preserved
- **WHEN** text contains `127.0.0.1` or `localhost` or `::1`
- **THEN** they are NOT scrubbed (these are generic, not environment-specific)

### Requirement: Path Normalization
The anonymizer SHALL convert absolute filesystem paths to repository-relative paths, stripping user home directories and system-specific prefixes.

#### Scenario: Home directory path normalized
- **WHEN** text contains `/home/tze/gt/butlers/mayor/rig/src/butlers/core/spawner.py`
- **THEN** it is replaced with `src/butlers/core/spawner.py`

#### Scenario: Non-repo paths scrubbed
- **WHEN** text contains an absolute path that is NOT under the repo root
- **THEN** it is replaced with `[REDACTED-PATH]`

### Requirement: User Content Exclusion
The anonymizer SHALL enforce that session prompts and session outputs are NEVER included in any PR content. Only structural metadata (exception type, sanitized message, call site, butler name) is permitted.

#### Scenario: Session prompt excluded
- **WHEN** the healing agent constructs PR content
- **THEN** the original session prompt text does NOT appear in the PR title, body, or commit messages

#### Scenario: Session output excluded
- **WHEN** the healing agent constructs PR content
- **THEN** the original session result/output text does NOT appear in the PR title, body, or commit messages

### Requirement: Environment Scrubbing
The anonymizer SHALL replace environment-specific values with placeholders.

#### Scenario: Hostnames scrubbed
- **WHEN** text contains internal hostnames (e.g. `db.internal.example.com`)
- **THEN** they are replaced with `[REDACTED-HOST]`

#### Scenario: Database names scrubbed
- **WHEN** text contains database or schema names from error messages
- **THEN** butler-specific schema names are preserved (they're public in the repo) but host/credential portions are scrubbed

### Requirement: Validation Pass
After anonymization, the system SHALL run a validation pass that scans the output for residual sensitive patterns. If any are detected, the PR is NOT created.

#### Scenario: Clean output passes validation
- **WHEN** anonymized text contains no residual email, IP, JWT, URL-with-credentials, or API key patterns
- **THEN** validation passes and the text is cleared for PR inclusion

#### Scenario: Residual PII blocks PR creation
- **WHEN** anonymized text still contains an email address pattern (anonymizer regex missed a format)
- **THEN** validation fails
- **AND** the healing attempt is marked `anonymization_failed`
- **AND** no PR is created

#### Scenario: Validation failure is logged with detail
- **WHEN** validation fails
- **THEN** the specific pattern type, character offset range, and length of the match are logged at ERROR level
- **AND** the `healing_attempts.error_detail` field records: pattern type detected, count of violations, and surrounding anonymized context (5 chars before/after the match, with the match itself replaced by `[MATCH]`) — never the actual sensitive value
- **AND** this gives operators enough information to tune anonymizer rules without exposing PII

#### Scenario: Multiple violations reported
- **WHEN** validation detects 3 residual patterns (2 emails, 1 IP)
- **THEN** all 3 are reported in the violation list
- **AND** `error_detail` includes all violations, not just the first

### Requirement: False Positive Handling
Code identifiers that resemble PII patterns (e.g. variable names like `user_email`, version strings like `1.2.3.4`, hex hashes) SHALL NOT trigger false positive scrubbing or validation failures.

#### Scenario: Code identifiers not scrubbed
- **WHEN** text contains `self.user_email` or `config["smtp_host"]`
- **THEN** these are NOT treated as email addresses or hostnames

#### Scenario: Version strings not treated as IPs
- **WHEN** text contains `version 1.2.3.4` or `Python 3.12.0`
- **THEN** these are NOT treated as IP addresses

#### Scenario: Git commit hashes not treated as API keys
- **WHEN** text contains a 40-character hex git SHA like `7c42c7a8...`
- **THEN** it is NOT treated as a credential

### Requirement: Anonymizer Function Signature
The system SHALL expose:
- `anonymize(text: str, repo_root: Path) -> str` — applies all scrubbing transforms
- `validate_anonymized(text: str) -> tuple[bool, list[str]]` — returns `(is_clean, list_of_violation_descriptions)`

#### Scenario: Anonymize returns cleaned text
- **WHEN** `anonymize("Error at /home/tze/gt/butlers/src/foo.py for user@test.com", repo_root)` is called
- **THEN** it returns `"Error at src/foo.py for [REDACTED-EMAIL]"`

#### Scenario: Validate reports violations
- **WHEN** `validate_anonymized("Contact admin@corp.com for help")` is called
- **THEN** it returns `(False, ["email pattern detected"])`
