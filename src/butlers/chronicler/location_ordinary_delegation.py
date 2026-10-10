"""Fixed birthday-job payload classification under the actual owning producer.

No caller text, metadata label or absent native row can classify a question.
The only classified payload is rendered here from a server-selected date;
its private immutable birth and public ledger body commit together before
routing. Receiving classification uses the existing registered challenge.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError


def birthday_fields(target_date: date) -> dict:
    from butlers.jobs.briefing import (
        DELEGATION_GIFT_ASK_DAYS_AHEAD,
        DELEGATION_GIFT_ASK_TARGET_BUTLER,
        _delegation_gift_ask_origin_key,
    )

    if type(target_date) is not date:
        raise PolicyUnavailableError("Ordinary job date is unavailable")
    value = target_date.isoformat()
    return {
        "asking_butler": "relationship",
        "question": (
            f"A household birthday is coming up in {DELEGATION_GIFT_ASK_DAYS_AHEAD} "
            f"days ({value}). What is the household's typical gift or "
            "discretionary-budget guidance for a birthday like this?"
        ),
        "target_butler": DELEGATION_GIFT_ASK_TARGET_BUTLER,
        "catalog_match_id": None,
        "catalog_score": None,
        "metadata": {
            "origin_key": _delegation_gift_ask_origin_key(value),
            "seed": "birthday_gift_budget_ask",
            "target_date": value,
        },
    }


def require_job_writer(writer: Any) -> None:
    from butlers.chronicler.location_catalog_copies import _server_copy_scope
    from butlers.chronicler.location_memory_context import _current_runtime_context
    from butlers.chronicler.location_tool_copies import _current_tool_copy

    if (
        not writer.runtime.active
        or writer.runtime.name != "relationship"
        or _current_tool_copy.get() is not None
        or _current_runtime_context.get() is not None
        or _server_copy_scope.get() is not None
    ):
        raise PolicyUnavailableError("Ordinary job producer differs")


async def record_birthday_ask(writer: Any, conn: Any, target_date: date) -> str:
    from butlers.chronicler.location_delegation_copies import question_digest

    require_job_writer(writer)
    # The job locks this configured domain before its atomic date dedup lock.
    # Recheck its constructor identity at the actual same-connection writer.
    await writer.runtime.lock_domain(conn)
    fields = birthday_fields(target_date)
    ledger = uuid4()
    actual = await conn.fetchval(
        "INSERT INTO public.delegation_ledger "
        "(id,asking_butler,question,target_butler,status,metadata) "
        "VALUES($1,$2,$3,$4,'pending',$5::jsonb) RETURNING id",
        ledger,
        fields["asking_butler"],
        fields["question"],
        fields["target_butler"],
        fields["metadata"],
    )
    if actual != ledger:
        raise PolicyUnavailableError("Ordinary question birth is unknown")
    await conn.execute(
        "INSERT INTO location_ordinary_delegation_inputs "
        "(source_generation,ledger_id,producer_kind,render_date,body_digest) "
        "VALUES($1,$2,'birthday_gift_budget_ask',$3,$4)",
        uuid4(),
        ledger,
        target_date,
        question_digest(fields),
    )
    return str(ledger)


async def load_ordinary_question(writer: Any, conn: Any, canonical: Any) -> dict:
    from butlers.chronicler.location_delegation_copies import question_digest

    runtime = writer.runtime
    row = await conn.fetchrow(
        "SELECT * FROM location_ordinary_delegation_inputs WHERE ledger_id=$1",
        canonical["id"],
    )
    if (
        row is None
        or runtime.name != "relationship"
        or not runtime.active
        or row["producer_kind"] != "birthday_gift_budget_ask"
        or canonical["status"] not in {"pending", "routed"}
        or canonical["asking_butler"] != runtime.name
        or await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_delegation_inputs WHERE ledger_id=$1)",
            canonical["id"],
        )
    ):
        raise PolicyUnavailableError("Ordinary question source classification is unavailable")
    fields = birthday_fields(row["render_date"])
    digest = question_digest(fields)
    if question_digest(dict(canonical)) != digest or row["body_digest"] != digest:
        raise PolicyUnavailableError("Ordinary question frozen payload differs")
    return {
        "question_generation": row["source_generation"],
        "body_digest": digest,
        "parent_count": 0,
        "exclusive_input": False,
        "classification": "ordinary",
    }


async def committed_birthday_ask(writer: Any, ledger: str) -> dict:
    async with writer.runtime.domain.acquire() as conn:
        async with conn.transaction():
            await writer.runtime.lock_domain(conn)
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                UUID(ledger),
            )
            if canonical is None:
                raise PolicyUnavailableError("Committed ordinary question is unknown")
            await load_ordinary_question(writer, conn, canonical)
    return dict(canonical)
