"""Trusted stored-confirm source for the additive goal-admission runtime.

No callback JSON can construct Evidence. Admission reloads the immutable prompt and
accepted answer under its own restricted Switchboard connection.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.core.confirm_models import effect_digest
from butlers.core.goal_admission import Evidence, EvidenceRefused, GoalAdmission, SourceRef, digest
from butlers.tools.switchboard.ingestion.goal_input import GoalInput, InputSegment


@dataclass(frozen=True)
class ConfirmAnswerEvidence(Evidence):
    prepared_input: GoalInput | None = None


class ConfirmEvidence:
    async def load(self, connection, source: SourceRef) -> Evidence:
        if source.kind != "message" or not source.id.startswith("confirm:"):
            raise EvidenceRefused("confirm_source_invalid")
        try:
            confirm_id = UUID(source.id.removeprefix("confirm:"))
        except ValueError:
            raise EvidenceRefused("confirm_source_invalid") from None
        row = await connection.fetchrow(
            "SELECT * FROM public.pending_confirms WHERE id=$1", confirm_id
        )
        if (
            row is None
            or row["sink"] is not None
            or row["status"] == "pending"
            or not row["goal_delivery_ref"]
        ):
            raise EvidenceRefused("confirm_source_unavailable")
        if row["status"] == "expired":
            # A timeout is durable system evidence, never owner speech or a fabricated choice.
            return Evidence("feed", "confirm-timeout", record_only=True)
        # Provenance was verified by the authenticated resolver in the same
        # transaction as acceptance; queue delay never reclassifies that speech.
        is_owner = row["recipient_owner"] and row["answer_channel"] in {
            "dashboard",
            "telegram_owner",
        }
        prompt = row["prompt"]
        value = prompt["options"][row["answer_index"]]["value"]
        prepared = GoalInput(
            source.id,
            digest([str(row["id"]), prompt, value]),
            (
                InputSegment(prompt["text"], True, ("quoted",)),
                InputSegment(value, not is_owner),
            ),
            row["goal_delivery_ref"],
            bool(prompt.get("quotes")),
        )
        return ConfirmAnswerEvidence(
            "message",
            "telegram-confirm",
            owner=is_owner,
            outside_content=not is_owner or prepared.marked,
            reply_delivery=row["goal_delivery_ref"] if is_owner else None,
            owner_entity=row["recipient_entity"] if is_owner else None,
            owner_channel="telegram" if is_owner else None,
            prepared_input=prepared,
        )


class ConfirmAdmission:
    def __init__(self, pool, *, catalog=None):
        self.pool = pool
        self.admission = GoalAdmission(pool, ConfirmEvidence(), catalog)

    async def validate_prompt(self, producer, prompt, delivery_ref, *, connection):
        conn = connection
        row = await conn.fetchrow(
            "SELECT * FROM switchboard.effects WHERE delivery_ref=$1", delivery_ref
        )
        if (
            row is None
            or row["status"] != "dispatched"
            or row["capability"] != "messenger.notify"
            or row["args_digest"] != effect_digest(producer, prompt)
            or row["recipient_entity"] != prompt.recipient.entity_id
            or row["recipient_channel"] != "telegram"
            or not row["prompt_accepts_input"]
        ):
            raise ValueError("verified accepting prompt delivery required")
        if not await conn.fetchval(
            "SELECT $1::timestamptz > clock_timestamp() AND "
            "$1::timestamptz <= clock_timestamp()+$2::int*interval '1 second'",
            row["prompt_expires_at"],
            prompt.timeout_seconds,
        ):
            raise ValueError("verified prompt deadline required")
        return row["prompt_expires_at"]

    async def __call__(self, row, value):
        source = SourceRef("message", f"confirm:{row['id']}")
        result = await self.admission.admit_submission(
            source, uuid5(NAMESPACE_URL, f"butlers:confirm:{row['id']}")
        )
        if result.result not in {"admitted", "joined"} and result.reason != "recorded_without_goal":
            return {"status": "error", "code": "admission_refused"}
        return {
            "status": "applied",
            "admission_id": str(result.decision_id),
            "goal_id": str(result.goal_id) if result.goal_id else None,
        }
