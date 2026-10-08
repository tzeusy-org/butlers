"""Actual owning delegated-question birth before the public ledger copy.

The fixed configured runtime supplies its pool/role; the private registered
native tool supplies the receiving session. Canonical source inputs and the
new public body share the policy-first writer transaction. A ledger UUID or
caller question alone supplies neither ancestry nor a receiving permission.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError
from butlers.location_retention import content_digest


def question_digest(fields: dict) -> bytes:
    from butlers.chronicler.location_projection import _digest_value

    fixed = {
        key: fields[key]
        for key in (
            "asking_butler",
            "question",
            "target_butler",
            "catalog_match_id",
            "catalog_score",
            "metadata",
        )
    }
    return content_digest({"native_delegated_question.v1": _digest_value(fixed)})


class NativeDelegationWriter:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.pending: dict[str, Any] = {}
        self.receiving: dict[Any, Any] = {}

    def capture_active(self) -> bool:
        from butlers.chronicler.location_tool_copies import _current_tool_copy

        tool = _current_tool_copy.get()
        return tool is not None and tool.runtime is self.runtime

    async def _capture_input(self, conn: Any, tool: Any):
        """Complete owning input bundle shared by actual question/answer writers."""
        runtime = self.runtime
        intent = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
            "WHERE tool_generation=$1 AND receiving_session=$2 "
            "AND module_name='core' AND tool_name=$3)",
            tool.generation,
            tool.session,
            tool.name,
        )
        frozen = await conn.fetchrow(
            "SELECT * FROM location_runtime_context_bindings WHERE receiving_session=$1",
            tool.session,
        )
        if intent is not True or frozen is None:
            raise PolicyUnavailableError("Native delegation input binding is unavailable")
        current = await conn.fetchrow(
            "SELECT prompt,effective_system_prompt FROM sessions WHERE id=$1 FOR UPDATE",
            tool.session,
        )
        if (
            current is None
            or not isinstance(current["prompt"], str)
            or not isinstance(current["effective_system_prompt"], str)
            or hashlib.sha256(current["prompt"].encode()).digest() != frozen["prompt_digest"]
            or hashlib.sha256(current["effective_system_prompt"].encode()).digest()
            != frozen["system_digest"]
        ):
            raise PolicyUnavailableError("Native delegation composed input changed")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions "
            "WHERE input_generation=$1)",
            frozen["input_generation"],
        ):
            raise PolicyUnavailableError("Native delegation receiving context is disposed")
        exclusive = frozen["exclusive_input"] is True and not tool.mixed_inputs
        # Each receiver sees only its OWN committed loan source history.
        context_loans = await conn.fetch(
            "SELECT l.loan_id AS parent_generation,l.body_digest AS parent_digest "
            "FROM location_catalog_copy_loans l "
            "JOIN location_catalog_copy_lifetimes h USING(loan_id,body_digest) "
            "WHERE h.holder_id=$1 AND h.holder_kind='unbound_processing' "
            "ORDER BY l.loan_id",
            frozen["input_generation"],
        )
        reconstructed = content_digest(
            {
                "loans": [
                    [str(row["parent_generation"]), row["parent_digest"].hex()]
                    for row in context_loans
                ],
                "context": frozen["context_digest"].hex(),
                "system": frozen["system_digest"].hex(),
                "prompt": frozen["prompt_digest"].hex(),
            }
        )
        if reconstructed != frozen["bundle_digest"]:
            raise PolicyUnavailableError("Native delegation complete context loans differ")
        later = await conn.fetch(
            "SELECT i.loan_id AS parent_generation,i.body_digest AS parent_digest,"
            "l.body_digest AS loan_digest,h.body_digest AS lifetime_digest "
            "FROM location_runtime_tool_inputs i "
            "JOIN location_runtime_tool_intents t USING(tool_generation) "
            "LEFT JOIN location_catalog_copy_loans l USING(loan_id) "
            "LEFT JOIN location_catalog_copy_lifetimes h ON h.loan_id=i.loan_id "
            "AND h.holder_id=t.tool_generation AND h.holder_kind='unbound_processing' "
            "WHERE t.receiving_session=$1 ORDER BY i.loan_id",
            tool.session,
        )
        if any(
            row["loan_digest"] != row["parent_digest"]
            or row["lifetime_digest"] != row["parent_digest"]
            for row in later
        ):
            raise PolicyUnavailableError("Native delegation complete tool loans differ")
        indexed = {row["parent_generation"]: row["parent_digest"] for row in context_loans}
        for row in later:
            if (
                row["parent_generation"] in indexed
                and indexed[row["parent_generation"]] != row["parent_digest"]
            ):
                raise PolicyUnavailableError("Native delegation loan generation differs")
            indexed[row["parent_generation"]] = row["parent_digest"]
        parents = [("catalog_loan", selected, body) for selected, body in sorted(indexed.items())]
        inherited = await conn.fetch(
            "SELECT n.claim_generation,q.bundle_digest,c.receiving_generation,"
            "i.body_digest FROM location_runtime_context_question_intents n "
            "LEFT JOIN location_received_delegation_contexts q "
            "USING(input_generation,claim_generation) "
            "LEFT JOIN location_received_delegation_claims c USING(claim_generation) "
            "LEFT JOIN location_received_delegation_inputs i USING(receiving_generation) "
            "WHERE n.input_generation=$1 ORDER BY c.receiving_generation",
            frozen["input_generation"],
        )
        for dependency in inherited:
            if (
                dependency["bundle_digest"] != frozen["bundle_digest"]
                or dependency["receiving_generation"] is None
                or not isinstance(dependency["body_digest"], bytes)
                or len(dependency["body_digest"]) != 32
            ):
                raise PolicyUnavailableError("Native delegation receiving ancestry differs")
            parents.append(
                (
                    "received_question",
                    dependency["receiving_generation"],
                    dependency["body_digest"],
                )
            )
        if runtime.name == "chronicler":
            from butlers.chronicler.location_memory_ancestry import require_complete_parents

            dispatch = await conn.fetch(
                "SELECT i.parent_count,p.copy_generation,p.input_digest,"
                "b.output_id,b.input_digest AS birth_digest,"
                "b.lineage_known,b.exclusive_input "
                "FROM location_native_dispatch_sessions s "
                "JOIN location_native_dispatch_inputs i USING(input_generation) "
                "LEFT JOIN location_native_dispatch_parents p USING(input_generation) "
                "LEFT JOIN location_native_copy_births b "
                "ON b.copy_generation=p.copy_generation "
                "WHERE s.receiving_session=$1 ORDER BY p.copy_generation,b.output_id",
                tool.session,
            )
            require_complete_parents(dispatch)
            own = await conn.fetch(
                "SELECT DISTINCT copy_generation,input_digest,"
                "lineage_known,exclusive_input "
                "FROM location_native_copy_births "
                "WHERE receiving_session=$1 ORDER BY copy_generation,input_digest",
                tool.session,
            )
            exclusive = exclusive and all(
                row["lineage_known"] is True and row["exclusive_input"] is True for row in own
            )
            # Retain original declared parents even if a native mirror
            # birth is missing. The question cannot derive a smaller
            # ancestry merely from the current receiving-session rows.
            native = {}
            for row in list(dispatch) + list(own):
                selected, body = row["copy_generation"], row["input_digest"]
                if selected in native and native[selected] != body:
                    raise PolicyUnavailableError("Native delegation input generation differs")
                native[selected] = body
            exclusive = exclusive and all(
                row["lineage_known"] is True and row["exclusive_input"] is True for row in dispatch
            )
            parents.extend(
                ("native_copy", selected, body) for selected, body in sorted(native.items())
            )
            for kind, selected, body in parents:
                if kind == "native_copy" and await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_copy_dispositions "
                    "WHERE copy_generation=$1) OR EXISTS("
                    "SELECT 1 FROM location_native_copy_births b "
                    "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
                    "WHERE b.copy_generation=$1 AND b.input_digest=$2)",
                    selected,
                    body,
                ):
                    raise PolicyUnavailableError("Native delegation input is fenced")
        for kind, selected, body in parents:
            if kind == "received_question":
                from butlers.chronicler.location_delegation_receivers import (
                    receiving_question_fenced,
                )

                if await receiving_question_fenced(conn, selected):
                    raise PolicyUnavailableError("Native delegation receiving input is fenced")
            if kind == "catalog_loan" and await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_dispositions "
                "WHERE loan_id=$1) OR EXISTS(SELECT 1 FROM location_catalog_copy_finished "
                "WHERE loan_id=$1 AND body_digest=$2)",
                selected,
                body,
            ):
                raise PolicyUnavailableError("Native delegation loan input is disposed")
        if len({(kind, selected) for kind, selected, body in parents}) != len(parents):
            raise PolicyUnavailableError("Native delegation input generation differs")
        return frozen, parents, exclusive

    async def capture_ask(self, fields: dict, write: Any) -> str:
        from butlers.chronicler.location_tool_copies import current_tool_copy

        runtime = self.runtime
        tool = current_tool_copy(runtime)
        if (
            tool is None
            or tool.module != "core"
            or tool.name != "delegate_ask"
            or fields["asking_butler"] != runtime.name
        ):
            raise PolicyUnavailableError("Native delegation source producer differs")
        generation, ledger = uuid4(), uuid4()
        digest = question_digest(fields)
        parents = []
        async with runtime.domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                frozen, parents, exclusive = await self._capture_input(conn, tool)
                await conn.execute(
                    "INSERT INTO location_native_delegation_inputs "
                    "(question_generation,ledger_id,receiving_session,tool_generation,"
                    "context_generation,body_digest,parent_count,exclusive_input) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                    generation,
                    ledger,
                    tool.session,
                    tool.generation,
                    frozen["input_generation"],
                    digest,
                    len(parents),
                    exclusive,
                )
                for kind, selected, body in parents:
                    await conn.execute(
                        "INSERT INTO location_native_delegation_parents "
                        "(question_generation,parent_kind,parent_generation,parent_digest) "
                        "VALUES($1,$2,$3,$4)",
                        generation,
                        kind,
                        selected,
                        body,
                    )
                stored = await write(conn, ledger)
                if str(stored) != str(ledger):
                    raise PolicyUnavailableError("Native delegation ledger birth differs")
        async with runtime.domain.acquire() as observed:
            receipt = await observed.fetchrow(
                "SELECT * FROM location_native_delegation_inputs "
                "WHERE question_generation=$1 AND ledger_id=$2",
                generation,
                ledger,
            )
            actual = await observed.fetchrow(
                "SELECT asking_butler,question,target_butler,catalog_match_id,catalog_score,"
                "status,reason,metadata FROM public.delegation_ledger WHERE id=$1",
                ledger,
            )
            committed_parents = await observed.fetch(
                "SELECT parent_kind,parent_generation,parent_digest "
                "FROM location_native_delegation_parents WHERE question_generation=$1 "
                "ORDER BY parent_kind,parent_generation",
                generation,
            )
        if (
            receipt is None
            or receipt["body_digest"] != digest
            or receipt["tool_generation"] != tool.generation
            or receipt["receiving_session"] != tool.session
            or receipt["context_generation"] != frozen["input_generation"]
            or receipt["exclusive_input"] is not exclusive
            or receipt["parent_count"] != len(parents)
            or {
                (r["parent_kind"], r["parent_generation"], r["parent_digest"])
                for r in committed_parents
            }
            != set(parents)
            or actual is None
            or question_digest(dict(actual)) != digest
        ):
            raise PolicyUnavailableError("Committed native delegation birth is unknown")
        return str(ledger)


async def delegation_frontier_closed(conn: Any, decision: Any) -> bool:
    """Actual owning question history stays in the frontier after its session ends.

    A terminal ledger status, cleared body, vanished parent or finished source
    session cannot attest the receiving schedule/runtime copies. Only an exact
    own immutable disposition after receiver reconciliation can close it.
    Receiver reconciliation is separate required source work, not manufactured
    by this necessary census predicate.
    """
    for header, parents, dispositions, generation in (
        (
            "location_native_delegation_inputs",
            "location_native_delegation_parents",
            "location_native_delegation_dispositions",
            "question_generation",
        ),
        (
            "location_native_delegation_answers",
            "location_native_delegation_answer_parents",
            "location_native_delegation_answer_dispositions",
            "answer_generation",
        ),
    ):
        if await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {header} q WHERE q.parent_count <> "
            f"(SELECT count(*) FROM {parents} p WHERE p.{generation}=q.{generation})) "
            f"OR EXISTS(SELECT 1 FROM {header} q "
            f"JOIN {parents} p USING({generation}) "
            "WHERE p.parent_kind='native_copy' AND (NOT EXISTS("
            "SELECT 1 FROM location_native_copy_births b "
            "WHERE b.copy_generation=p.parent_generation "
            "AND b.input_digest=p.parent_digest) OR EXISTS("
            "SELECT 1 FROM location_native_copy_births b "
            "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
            "WHERE o.decision_id=$1 AND b.copy_generation=p.parent_generation "
            "AND b.input_digest=p.parent_digest)) AND NOT EXISTS("
            f"SELECT 1 FROM {dispositions} d "
            "JOIN location_retention_plans plan USING(decision_id) "
            f"WHERE d.{generation}=q.{generation} AND d.decision_id=$1 "
            "AND d.body_digest=q.body_digest AND d.manifest_digest=plan.manifest_digest))",
            decision,
        ):
            return False
    return True
