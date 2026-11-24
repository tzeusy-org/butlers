"""The SQL twin of ``calendar_provenance.is_quarantined_invitation`` (REQ-module-calendar-041).

A reader that filters in SQL, before a LIMIT, uses this fragment so quarantined invitations never
consume a batch or reach a prompt. Readers that fetch first and filter in Python use
``is_owner_attending`` / ``is_quarantined_invitation``. A parity test runs both against the same
cases on a real Postgres, so the two cannot drift.
"""

from __future__ import annotations

from typing import Final

TRUSTED_ORGANIZER_VERDICTS_SQL: Final = "('owner', 'known_entity', 'known_domain', 'vouched')"


def quarantined_invitation_sql(alias: str = "e") -> str:
    """A boolean SQL expression true when ``{alias}.metadata`` is a quarantined invitation."""
    metadata = f"{alias}.metadata"
    return f"""(
        COALESCE(lower({metadata} ->> 'butler_generated'), 'false') <> 'true'
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof({metadata} -> 'attendees') = 'array'
                     THEN {metadata} -> 'attendees' ELSE '[]'::jsonb END
            ) AS att
            WHERE att ->> 'self' = 'true'
              AND COALESCE(att ->> 'organizer', 'false') <> 'true'
              AND lower(regexp_replace(
                    COALESCE(att ->> 'response_status', att ->> 'responseStatus', ''),
                    '^\\s+|\\s+$', '', 'g'))
                  NOT IN ('accepted', 'declined', 'tentative')
              AND (lower(btrim(COALESCE({metadata} ->> 'organizer', ''))) = ''
                   OR lower(btrim(att ->> 'email'))
                      IS DISTINCT FROM lower(btrim({metadata} ->> 'organizer')))
        )
        AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof({metadata} -> 'attendees') = 'array'
                     THEN {metadata} -> 'attendees' ELSE '[]'::jsonb END
            ) AS own
            WHERE own ->> 'self' = 'true' AND own ->> 'organizer' = 'true'
        )
        AND COALESCE({metadata} ->> 'organizer_verdict', '')
            NOT IN {TRUSTED_ORGANIZER_VERDICTS_SQL}
    )"""


QUARANTINED_INVITATION_SQL: Final = quarantined_invitation_sql("e")
