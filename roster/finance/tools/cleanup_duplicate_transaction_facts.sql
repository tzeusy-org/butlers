-- Cleanup: supersede duplicate transaction facts
--
-- Bug: record_transaction_fact was exposed as a direct MCP tool alongside
-- record_transaction (which auto-mirrors to facts via _mirror_to_spo).
-- The LLM agent called both, creating a transaction_debit AND a
-- transaction_credit fact for the same real-world transaction.
--
-- Fix: For each (entity_id, valid_at, merchant, amount, currency) group that
-- has BOTH a transaction_debit and transaction_credit fact, keep the debit
-- (the correct one for purchases) and supersede the credit duplicate.
--
-- Run with: psql -d butlers -f cleanup_duplicate_transaction_facts.sql

-- Preview: show duplicates before fixing
SELECT
    d.id   AS debit_id,
    c.id   AS credit_id,
    d.valid_at,
    d.metadata->>'merchant' AS merchant,
    d.metadata->>'amount'   AS amount,
    d.metadata->>'currency' AS currency
FROM finance.facts d
JOIN finance.facts c
  ON  c.entity_id = d.entity_id
  AND c.valid_at  = d.valid_at
  AND c.validity  = 'active'
  AND c.predicate = 'transaction_credit'
  AND c.metadata->>'merchant' = d.metadata->>'merchant'
  AND c.metadata->>'amount'   = d.metadata->>'amount'
  AND c.metadata->>'currency' = d.metadata->>'currency'
WHERE d.predicate = 'transaction_debit'
  AND d.validity  = 'active'
  AND d.scope     = 'finance'
ORDER BY d.valid_at DESC;

-- Supersede the credit duplicates (keeps the debit as the canonical record).
-- Uses validity='superseded' + invalid_at rather than DELETE so the audit
-- trail is preserved and the change is reversible.
UPDATE finance.facts
SET validity   = 'superseded',
    invalid_at = now()
WHERE id IN (
    SELECT c.id
    FROM finance.facts d
    JOIN finance.facts c
      ON  c.entity_id = d.entity_id
      AND c.valid_at  = d.valid_at
      AND c.validity  = 'active'
      AND c.predicate = 'transaction_credit'
      AND c.metadata->>'merchant' = d.metadata->>'merchant'
      AND c.metadata->>'amount'   = d.metadata->>'amount'
      AND c.metadata->>'currency' = d.metadata->>'currency'
    WHERE d.predicate = 'transaction_debit'
      AND d.validity  = 'active'
      AND d.scope     = 'finance'
);
