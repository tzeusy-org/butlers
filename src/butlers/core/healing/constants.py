"""Shared historical attempt and current QA tracking constants."""

CIRCUIT_BREAKER_FAILURE_STATUSES = frozenset({"failed", "timeout", "anonymization_failed"})
UNFIXABLE_SENTINEL_FILENAME = "UNFIXABLE"

#: Consecutive launched-execution failures that trip the QA circuit breaker. One constant for
#: dispatch admission, the dashboard and the remediation-liveness conditions (bu-ejow5p.8).
QA_BREAKER_THRESHOLD = 5

#: Marker prefix of the ``error_detail`` a QA attempt carries when publication is unavailable.
QA_PUBLICATION_UNAVAILABLE_PREFIX = "publication_unavailable:"
#: Effective status of a ``failed`` attempt that kept a sealed, retained proposal.
QA_PROPOSAL_RETAINED_STATUS = "proposal_retained"

#: Shared definition of one launched QA execution for the circuit breaker (bu-ejow5p.2).
#: Dispatch admission and the dashboard both select ``status`` through this, so they cannot
#: drift.  A failed attempt that retained a sealed proposal is an execution success: the
#: investigation worked, only publication is absent.
QA_BREAKER_EFFECTIVE_STATUS_SQL = f"""CASE
        WHEN status = 'failed'
             AND error_detail LIKE '{QA_PUBLICATION_UNAVAILABLE_PREFIX}%'
             AND EXISTS (
                 SELECT 1 FROM public.qa_publication_exports AS retained
                 WHERE retained.attempt_id = healing_attempts.id
             )
        THEN '{QA_PROPOSAL_RETAINED_STATUS}'
        ELSE status
    END"""

#: Only launched QA executions that closed after the latest manual reset count. A refused reset
#: (``outcome = 'refused'``, bu-ejow5p.8) is an audit row and never clears the breaker.
QA_BREAKER_WHERE_SQL = """FROM public.healing_attempts
WHERE qa_patrol_id IS NOT NULL
  AND closed_at IS NOT NULL
  AND healing_session_id IS NOT NULL
  AND closed_at > COALESCE(
        (SELECT max(reset_at) FROM public.breaker_resets
          WHERE breaker = 'qa' AND outcome = 'reset'),
        '-infinity'::timestamptz
      )
"""
