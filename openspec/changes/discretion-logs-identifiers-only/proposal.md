## Why

`DiscretionEvaluator` logged up to 500 prompt characters, the model result, unparseable responses and exception messages at INFO, WARNING and ERROR for every discretion caller, including live-listener transcripts and third-party message text. Producer logs already follow "logs carry identifiers, not content" (bu-n356fz); discretion did not.

## What Changes

- Add a scenario to the Shared Discretion Layer requirement fixing the log contract: identifiers only, never content, exception text or tracebacks.
- Mint `REQ-connector-base-spec-007` for the block (the block had no ID).

## Impact

- `src/butlers/connectors/discretion.py` log sites; no change to verdicts, metrics, the failover-suppression ledger or the dispatcher.
- One retained assertion changes (contract evolution): `test_evaluator_withholds_bearer_text_from_prompt_log_and_window` expects the prompt hash instead of the scrubbed placeholder in the log.
