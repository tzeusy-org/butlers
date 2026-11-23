## MODIFIED Requirements

### Requirement: Finding Extraction
Each qualifying log entry SHALL be normalized into a `QaFinding` with a computed fingerprint for deduplication.

ID: REQ-qa-log-scanner-003
Source: bu-ejow5p.4 (owner-confirmed spec-restoring fix); canonical Finding Extraction requirement
Scope: v1-mandatory

#### Scenario: Finding structure
- **WHEN** a qualifying log entry is processed
- **THEN** a `QaFinding` is produced with: `fingerprint` (str, SHA-256), `source_type` (str, `"log_scanner"`), `source_butler` (str), `source_file` (str, log filename), `severity` (int, 0=critical..3=low), `exception_type` (str or "unknown"), `event_summary` (str, first 200 chars of event, sanitized via `anonymize()` to strip PII), `call_site` (str, the innermost repository frame `file:function` when known, otherwise the logger name), `timestamp` (datetime)
- **AND** raw log line content is NOT included in the finding (privacy: raw logs may contain user data)
- **AND** `event_summary` is passed through the anonymizer before storage because error messages may contain user data (email addresses, contact names, etc.)

#### Scenario: Fingerprint computation
- **WHEN** computing a finding fingerprint
- **THEN** the fingerprint is a SHA-256 hash of: `exception_type + call_site + normalized_message`, where a logged exception contributes its own class, innermost repository frame and normalized message (`exc_type`, `exc_frame`, `exc_message_sanitized` written by `core.logging`), and any other line contributes its normalized event
- **AND** the normalization strips variable content (UUIDs, timestamps, numeric IDs, file paths) to group semantically identical errors
- **AND** the algorithm is compatible with `src/butlers/core/healing/fingerprint.py` to enable cross-source deduplication

#### Scenario: One defect is one fingerprint
- **WHEN** the same exception class is logged from the same repository frame with different dates, ids or line numbers
- **THEN** the scanner returns one finding with the summed `occurrence_count`, and the fingerprint is unchanged by a deploy that shifts line numbers
- **AND** the same class from two call sites gives two findings
- **AND** the rendered traceback is never the `exception_type`: a legacy line is parsed by its last `Type: message` line, and text naming no class gives `unknown`
- **AND** the anonymized traceback tail is kept in `structured_evidence.traceback_tail`, not in identity
