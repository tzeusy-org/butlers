"""An effect's delivery reference and prompt are recorded once, at its commit.

Revision ID: sw_066
Revises: sw_065

RFC 0038 S2.2 (bu-dhloi1.295). sw_047 froze ``delivery_ref`` and the prompt columns at insert, but
the provider's delivery reference exists only after the domain dispatches, and a prompt's expiry is
the domain's receipt. So nothing could ever bind an owner delivery to the reply that answers it
(``goal__wait_owner``, S2 reply joins). The recipient and channel are known when the call is
reserved and are recorded then; they stay fully immutable.

This revision replaces only the ``effects`` branch of ``switchboard.goal_ledger_immutable()``.
``delivery_ref``, ``prompt_accepts_input`` and ``prompt_expires_at`` may change from NULL (false)
to a value exactly once, and only in the UPDATE that moves the effect from ``requested`` or
``dispatched`` to ``committed``. A value never changes; no other transition may write them,
including ``requested`` to ``dispatched``; nothing changes after the commit.
``ck_effect_prompt_binding`` still holds. Lineage (goal, invocation, capability, argument
digest), ``recipient_entity`` and ``recipient_channel`` are immutable as before.

The function is edited in place from its installed definition, so the other tables' branches are
untouched. The downgrade restores the sw_063 guard; bindings already recorded stay, because that
guard only forbids changing them.
"""

from alembic import op

revision = "sw_066"
down_revision = "sw_065"
branch_labels = None
depends_on = None

_OLD = """            IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.args_digest,
                NEW.delivery_ref,NEW.recipient_entity,NEW.recipient_channel) IS DISTINCT FROM
               (OLD.goal_id,OLD.invocation_id,OLD.capability,OLD.args_digest,
                OLD.delivery_ref,OLD.recipient_entity,OLD.recipient_channel)
               OR (OLD.label_version IS NOT NULL AND"""

_NEW = """            -- The delivery reference and prompt are set once, by the commit that settles the effect.
            IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.args_digest,
                NEW.recipient_entity,NEW.recipient_channel) IS DISTINCT FROM
               (OLD.goal_id,OLD.invocation_id,OLD.capability,OLD.args_digest,
                OLD.recipient_entity,OLD.recipient_channel)
               OR (OLD.delivery_ref IS NOT NULL
                   AND NEW.delivery_ref IS DISTINCT FROM OLD.delivery_ref)
               OR (OLD.prompt_accepts_input AND NOT NEW.prompt_accepts_input)
               OR (OLD.prompt_expires_at IS NOT NULL
                   AND NEW.prompt_expires_at IS DISTINCT FROM OLD.prompt_expires_at)
               OR (NOT (OLD.status IN ('requested','dispatched') AND NEW.status = 'committed')
                   AND (NEW.delivery_ref,NEW.prompt_accepts_input,NEW.prompt_expires_at)
                       IS DISTINCT FROM
                       (OLD.delivery_ref,OLD.prompt_accepts_input,OLD.prompt_expires_at))
               OR (OLD.label_version IS NOT NULL AND"""

_SWAP = """
DO $swap$
DECLARE
    definition TEXT;
BEGIN
    SELECT pg_get_functiondef('switchboard.goal_ledger_immutable()'::regprocedure)
        INTO definition;
    IF position(%(old)s IN definition) = 0 THEN
        RAISE EXCEPTION 'goal_ledger_immutable() does not hold the expected effects guard';
    END IF;
    EXECUTE replace(definition, %(old)s, %(new)s);
END
$swap$;
"""


def _swap(old: str, new: str) -> None:
    sql = _SWAP.replace("%(old)s", _quote(old)).replace("%(new)s", _quote(new))
    op.execute(sql)


def _quote(text: str) -> str:
    return "$q$" + text + "$q$"


def upgrade() -> None:
    op.execute("LOCK TABLE switchboard.effects IN ACCESS EXCLUSIVE MODE")
    _swap(_OLD, _NEW)


def downgrade() -> None:
    op.execute("LOCK TABLE switchboard.effects IN ACCESS EXCLUSIVE MODE")
    _swap(_NEW, _OLD)
