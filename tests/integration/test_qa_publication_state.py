"""Real-Postgres contract for the QA publication operation ledger (bu-mc01y5.5).

REQ-qa-publication-authority-002 (immutable binding) and -006 (durable
ambiguous outcome, held lineage).  The pool assumes ``butler_qa_rw`` because
forced row security admits no other writer, exactly as in production.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import timedelta

import asyncpg
import pytest

from butlers.core.healing.tracking import get_publication_hold
from butlers.core.qa.publication.binding import (
    OperationKind,
    PublicationBinding,
    labels_payload,
    request_digest,
)
from butlers.core.qa.publication.state import (
    ABSENCE_SETTLE,
    PublicationRefused,
    begin_operation,
    mark_egress_started,
    record_outcome,
    record_reconciliation,
)
from butlers.testing.db import open_pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SHA = "b" * 40


@pytest.fixture
def qa_url(fresh_db) -> str:
    return fresh_db("core")


async def _set_qa_role(connection: asyncpg.Connection) -> None:
    await connection.execute("SET ROLE butler_qa_rw")


@pytest.fixture
async def qa_pool(qa_url: str):
    qa_pool = await open_pool(qa_url, max_size=10, init=_set_qa_role)
    try:
        yield qa_pool
    finally:
        await qa_pool.close()


@pytest.fixture
async def attempt(qa_pool) -> tuple[uuid.UUID, str]:
    attempt_id = uuid.uuid4()
    fingerprint = uuid.uuid4().hex * 2
    await qa_pool.execute(
        "INSERT INTO public.healing_attempts "
        "(id, fingerprint, butler_name, status, severity, exception_type, call_site) "
        "VALUES ($1, $2, 'qa', 'investigating', 2, 'E', 'x:y')",
        attempt_id,
        fingerprint,
    )
    return attempt_id, fingerprint


def _binding(**overrides) -> PublicationBinding:
    fields = {
        "repository": "acme/butlers",
        "branch": "qa/fix-abc",
        "base_ref": "main",
        "head_sha": _SHA,
    }
    return PublicationBinding(**{**fields, **overrides})


_PAYLOAD = request_digest({"title": "Fix", "labels": ["qa"]})


async def test_same_binding_returns_the_existing_operation(qa_pool, attempt) -> None:
    attempt_id, _ = attempt
    first = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.BRANCH_PUSH,
        binding=_binding(),
        request_digest=_PAYLOAD,
    )
    assert first.stage == "bound"
    assert await mark_egress_started(qa_pool, first.id)

    again = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.BRANCH_PUSH,
        binding=_binding(),
        request_digest=_PAYLOAD,
    )

    assert again.id == first.id
    assert again.stage == "egress_started"  # the duplicate must not egress again
    assert not await mark_egress_started(qa_pool, first.id)


async def test_changed_target_or_payload_refuses_and_leaves_prior_status(qa_pool, attempt) -> None:
    attempt_id, _ = attempt
    first = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.PR_CREATE,
        binding=_binding(),
        request_digest=_PAYLOAD,
    )
    assert await mark_egress_started(qa_pool, first.id)
    assert await record_outcome(qa_pool, first.id, "succeeded", new_object_id="pr:17")

    for binding, digest, code in (
        (_binding(branch="qa/other"), _PAYLOAD, "binding_changed"),
        (_binding(head_sha="c" * 40), _PAYLOAD, "binding_changed"),
        (_binding(), request_digest({"title": "Different"}), "payload_changed"),
    ):
        with pytest.raises(PublicationRefused) as refused:
            await begin_operation(
                qa_pool,
                attempt_id=attempt_id,
                kind=OperationKind.PR_CREATE,
                binding=binding,
                request_digest=digest,
            )
        assert refused.value.code == code

    row = await qa_pool.fetchrow(
        "SELECT stage, result, new_object_id, binding_digest "
        "FROM public.qa_publication_operations WHERE id = $1",
        first.id,
    )
    assert (row["stage"], row["result"], row["new_object_id"]) == ("succeeded", "success", "pr:17")
    assert row["binding_digest"] == _binding().digest()


async def test_concurrent_writers_produce_one_row_and_one_egress_claim(qa_pool, attempt) -> None:
    attempt_id, _ = attempt

    operations = await asyncio.gather(
        *(
            begin_operation(
                qa_pool,
                attempt_id=attempt_id,
                kind=OperationKind.BRANCH_PUSH,
                binding=_binding(),
                request_digest=_PAYLOAD,
            )
            for _ in range(8)
        )
    )

    assert len({op.id for op in operations}) == 1
    assert (
        await qa_pool.fetchval(
            "SELECT count(*) FROM public.qa_publication_operations WHERE attempt_id = $1",
            attempt_id,
        )
        == 1
    )
    claims = await asyncio.gather(
        *(mark_egress_started(qa_pool, operations[0].id) for _ in range(8))
    )
    assert sum(claims) == 1


async def test_unavailable_datastore_refuses_before_egress(qa_url: str, attempt) -> None:
    attempt_id, _ = attempt
    closed = await asyncpg.create_pool(qa_url, min_size=1, max_size=1)
    await closed.close()

    with pytest.raises(PublicationRefused) as refused:
        await begin_operation(
            closed,
            attempt_id=attempt_id,
            kind=OperationKind.BRANCH_PUSH,
            binding=_binding(),
            request_digest=_PAYLOAD,
        )

    assert refused.value.code == "datastore_unavailable"


async def test_unknown_attempt_is_refused(qa_pool) -> None:
    with pytest.raises(PublicationRefused) as refused:
        await begin_operation(
            qa_pool,
            attempt_id=uuid.uuid4(),
            kind=OperationKind.BRANCH_PUSH,
            binding=_binding(),
            request_digest=_PAYLOAD,
        )
    assert refused.value.code == "unknown_attempt"


async def test_ambiguous_outcome_holds_until_exact_reconciliation(qa_pool, attempt) -> None:
    attempt_id, fingerprint = attempt
    operation = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.PR_CREATE,
        binding=_binding(),
        request_digest=_PAYLOAD,
        expected_object_id="pr:17",
    )
    assert not await get_publication_hold(qa_pool, fingerprint)  # bound: no egress yet
    assert await mark_egress_started(qa_pool, operation.id)
    assert await get_publication_hold(qa_pool, fingerprint)  # persisted before egress
    assert await record_outcome(qa_pool, operation.id, "ambiguous")
    assert await get_publication_hold(qa_pool, fingerprint)

    # A reconciliation that does not name the bound resource releases nothing.
    assert not await record_reconciliation(
        qa_pool, operation.id, "resource_found", observed_object_id="pr:99"
    )
    assert not await record_reconciliation(qa_pool, operation.id, "resource_found")
    assert await get_publication_hold(qa_pool, fingerprint)

    assert await record_reconciliation(
        qa_pool, operation.id, "resource_found", observed_object_id="pr:17"
    )
    assert not await get_publication_hold(qa_pool, fingerprint)
    assert (
        await qa_pool.fetchval(
            "SELECT new_object_id FROM public.qa_publication_operations WHERE id = $1",
            operation.id,
        )
        == "pr:17"
    )


_ADOPTED_URL = "https://github.com/acme/butlers/pull/{}"


@pytest.mark.parametrize(
    ("kind", "observed", "url", "prefilled"),
    [
        (OperationKind.PR_CREATE, "17", _ADOPTED_URL.format(17), 5),
        (OperationKind.PR_CREATE, "9999999999", _ADOPTED_URL.format(9999999999), None),
        (OperationKind.PR_CREATE, "17", _ADOPTED_URL.format(18), None),
        (OperationKind.PR_CREATE, "17", "https://evil.example/acme/butlers/pull/17", None),
        (OperationKind.BRANCH_PUSH, _SHA, _ADOPTED_URL.format(_SHA), None),
    ],
    ids=["attempt-names-a-pr", "integer-overflow", "url-mismatch", "foreign-url", "not-pr_create"],
)
async def test_reconciliation_releases_the_hold_but_skips_an_unsafe_attempt_fill(
    qa_pool, attempt, kind, observed, url, prefilled
) -> None:
    """The adoption fill's guards skip the attempt write, never the reconciliation."""
    attempt_id, fingerprint = attempt
    before = (prefilled, _ADOPTED_URL.format(prefilled)) if prefilled else (None, None)
    await qa_pool.execute(
        "UPDATE public.healing_attempts SET pr_number = $2::integer, pr_url = $3::text "
        "WHERE id = $1",
        attempt_id,
        *before,
    )
    operation = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=kind,
        binding=_binding(),
        request_digest=_PAYLOAD,
        expected_object_id=_SHA if kind is OperationKind.BRANCH_PUSH else None,
    )
    assert await mark_egress_started(qa_pool, operation.id)
    assert await record_outcome(qa_pool, operation.id, "ambiguous")

    assert await record_reconciliation(
        qa_pool, operation.id, "resource_found", observed_object_id=observed, adopted_pr_url=url
    )

    assert not await get_publication_hold(qa_pool, fingerprint)
    row = await qa_pool.fetchrow(
        "SELECT stage, reconciliation, new_object_id FROM public.qa_publication_operations "
        "WHERE id = $1",
        operation.id,
    )
    assert tuple(row) == ("reconciled", "resource_found", observed)
    pr = await qa_pool.fetchrow(
        "SELECT pr_number, pr_url FROM public.healing_attempts WHERE id = $1", attempt_id
    )
    assert tuple(pr) == before


@pytest.mark.parametrize("terminal_status", ["timeout", "failed"])
async def test_inactive_attempt_cannot_enter_egress(qa_pool, attempt, terminal_status) -> None:
    attempt_id, fingerprint = attempt
    operation = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.BRANCH_PUSH,
        binding=_binding(),
        request_digest=_PAYLOAD,
    )
    await qa_pool.execute(
        "UPDATE public.healing_attempts SET status = $2, closed_at = now() WHERE id = $1",
        attempt_id,
        terminal_status,
    )

    with pytest.raises(PublicationRefused) as refused:
        await mark_egress_started(qa_pool, operation.id)

    assert refused.value.code == "attempt_not_active"
    assert (
        await qa_pool.fetchval(
            "SELECT stage FROM public.qa_publication_operations WHERE id = $1", operation.id
        )
        == "bound"
    )
    assert not await get_publication_hold(qa_pool, fingerprint)


async def test_sweep_adopts_the_only_pr_for_a_lost_pr_create(qa_pool, attempt) -> None:
    """The sweep rebuilds the binding from the attempt and export, then adopts the match."""
    from butlers.core.qa.publication.reconcile import PullRequestView, sweep_held_operations

    attempt_id, fingerprint = attempt
    binding = _binding()
    await qa_pool.execute(
        "UPDATE public.healing_attempts SET branch_name = $2 WHERE id = $1",
        attempt_id,
        binding.branch,
    )
    await qa_pool.execute(
        "INSERT INTO public.qa_publication_exports "
        "(attempt_id, base_sha, head_sha, commits, objects, digest) "
        "VALUES ($1, $2, $3, $4, ''::bytea, $5)",
        attempt_id,
        "a" * 40,
        binding.head_sha,
        [binding.head_sha],
        "d" * 64,
    )
    operation = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.PR_CREATE,
        binding=binding,
        request_digest=_PAYLOAD,
    )
    assert await mark_egress_started(qa_pool, operation.id)
    assert await record_outcome(qa_pool, operation.id, "ambiguous")
    # A non-number, or a second adoption, never releases the hold.
    assert not await record_reconciliation(
        qa_pool, operation.id, "resource_found", observed_object_id="pr:17"
    )

    class Reader:
        repository = binding.repository

        async def read_ref(self, *, branch):  # pragma: no cover - pr_create never reads refs
            raise AssertionError("pr_create reconciles by PR lookup")

        async def find_pull_requests(self, *, head, base):
            assert (head, base) == (binding.branch, binding.base_ref)
            return (17,)

    entries = await sweep_held_operations(qa_pool, Reader(), limit=1000)

    mine = [e for e in entries if e.attempt_id == attempt_id]
    assert [(e.kind, e.status, e.category) for e in mine] == [
        ("pr_create", "reconciled", "resource_found")
    ]
    assert not await get_publication_hold(qa_pool, fingerprint)
    row = await qa_pool.fetchrow(
        "SELECT stage, reconciliation, new_object_id FROM public.qa_publication_operations "
        "WHERE id = $1",
        operation.id,
    )
    assert tuple(row) == ("reconciled", "resource_found", "17")
    # The adoption lands on the attempt in the same write, so the case API shows the PR.
    pr = await qa_pool.fetchrow(
        "SELECT pr_number, pr_url FROM public.healing_attempts WHERE id = $1", attempt_id
    )
    assert tuple(pr) == (17, "https://github.com/acme/butlers/pull/17")

    # A lost label set on the adopted PR binds that PR and is swept against its labels.
    labels = ("self-healing", "automated")
    labelled = await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.PR_LABELS,
        binding=_binding(pr_number=17),
        request_digest=request_digest(labels_payload(labels)),
        expected_object_id="17",
    )
    assert await mark_egress_started(qa_pool, labelled.id)
    assert await record_outcome(qa_pool, labelled.id, "ambiguous")

    class LabelReader(Reader):
        async def read_pull_request(self, *, number):
            assert number == 17
            return PullRequestView(17, binding.branch, binding.base_ref, "Fix", "", labels)

    entries = await sweep_held_operations(qa_pool, LabelReader(), limit=1000, labels=labels)

    mine = [e for e in entries if e.attempt_id == attempt_id]
    assert [(e.kind, e.status, e.category) for e in mine] == [
        ("pr_labels", "reconciled", "resource_found")
    ]
    assert not await get_publication_hold(qa_pool, fingerprint)


# --- core_300: absence settles from egress, judged by database time in the write ---


async def _insert_backdated_operation(
    postgres_container, migrated_db_url: str, attempt_id: uuid.UUID, *, stage_sql: str
) -> uuid.UUID:
    """Insert a ``pr_create`` row with backdated timestamps.

    The container's privileged login bypasses row security, and the guard only
    fires on UPDATE, so this is the one way to make a row look old.
    """
    from butlers.testing.migration import migration_bootstrap_db_url

    operation_id = uuid.uuid4()
    connection = await asyncpg.connect(
        migration_bootstrap_db_url(postgres_container, migrated_db_url.rsplit("/", 1)[1]).replace(
            "postgresql+psycopg2://", "postgresql://"
        )
    )
    try:
        await connection.execute(
            f"""
            INSERT INTO public.qa_publication_operations
                (id, attempt_id, kind, binding_digest, request_digest,
                 stage, stage_order, started_at, egress_started_at)
            VALUES ($1, $2, 'pr_create', $3, $4, {stage_sql})
            """,
            operation_id,
            attempt_id,
            "a" * 64,
            _PAYLOAD,
        )
    finally:
        await connection.close()
    return operation_id


async def test_resumed_late_egress_stays_held_while_in_flight(
    qa_pool, attempt, postgres_container, qa_url
) -> None:
    """Bound an hour ago, egress resumed now: the bind time must not settle the absence."""
    attempt_id, fingerprint = attempt
    operation_id = await _insert_backdated_operation(
        postgres_container,
        qa_url,
        attempt_id,
        stage_sql="'bound', 1, now() - interval '1 hour', NULL",
    )
    assert await mark_egress_started(qa_pool, operation_id)
    started_at, egress_started_at, db_now = await qa_pool.fetchrow(
        "SELECT started_at, egress_started_at, now() FROM public.qa_publication_operations "
        "WHERE id = $1",
        operation_id,
    )
    assert db_now - started_at > ABSENCE_SETTLE  # settled by the bind-time rule
    assert db_now - egress_started_at < ABSENCE_SETTLE  # egress just began

    assert not await record_reconciliation(
        qa_pool, operation_id, "resource_absent", expected_stage="egress_started"
    )
    assert not await record_reconciliation(qa_pool, operation_id, "resource_absent")

    assert await get_publication_hold(qa_pool, fingerprint)
    assert (
        await qa_pool.fetchval(
            "SELECT stage FROM public.qa_publication_operations WHERE id = $1", operation_id
        )
        == "egress_started"
    )


async def test_outcome_recorded_between_read_and_write_is_not_overwritten(
    qa_pool, attempt, postgres_container, qa_url
) -> None:
    """The sweep read ``egress_started`` (long settled); the dispatcher then records ``ambiguous``."""
    attempt_id, fingerprint = attempt
    old = "now() - interval '1 hour'"
    operation_id = await _insert_backdated_operation(
        postgres_container,
        qa_url,
        attempt_id,
        stage_sql=f"'egress_started', 2, {old}, {old}",
    )
    assert await record_outcome(qa_pool, operation_id, "ambiguous")

    # Stage guard alone: the observed stage is gone even with no settle required.
    assert not await record_reconciliation(
        qa_pool,
        operation_id,
        "resource_absent",
        expected_stage="egress_started",
        absence_settle=timedelta(0),
    )
    # Settle alone: the fresh ended_at is what counts, not the old egress stamp.
    assert not await record_reconciliation(qa_pool, operation_id, "resource_absent")

    row = await qa_pool.fetchrow(
        "SELECT stage, result, reconciliation FROM public.qa_publication_operations WHERE id = $1",
        operation_id,
    )
    assert tuple(row) == ("ambiguous", "ambiguous", None)
    assert await get_publication_hold(qa_pool, fingerprint)

    # Control: a reconciler that observed the outcome may record once it has settled.
    assert await record_reconciliation(
        qa_pool,
        operation_id,
        "resource_absent",
        expected_stage="ambiguous",
        absence_settle=timedelta(0),
    )
    assert not await get_publication_hold(qa_pool, fingerprint)


async def _backdate_close(db_url: str, attempt_id: uuid.UUID, ago: timedelta) -> None:
    """Move a close into the past as the table owner, the one writer able to lift the stamp."""
    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():
            await conn.execute(
                "ALTER TABLE public.healing_attempts DISABLE TRIGGER healing_attempts_closed_at_stamp"
            )
            await conn.execute(
                "UPDATE public.healing_attempts SET closed_at = now() - $2::interval WHERE id = $1",
                attempt_id,
                ago,
            )
            await conn.execute(
                "ALTER TABLE public.healing_attempts ENABLE TRIGGER healing_attempts_closed_at_stamp"
            )
    finally:
        await conn.close()


async def test_retained_export_round_trips_then_prunes_to_a_binding_tombstone(
    qa_pool, attempt, tmp_path, qa_url: str
) -> None:
    """Retention as ``butler_qa_rw`` (bu-mc01y5.19) and its pruning (bu-mc01y5.25, core_301)."""
    from butlers.core.qa.publication.export import build_sealed_export
    from butlers.core.qa.publication.retention import (
        load_retained_export,
        prune_retained_exports,
        retain_export,
    )
    from tests.core.qa.publication_fakes import QA_IDENTITY, commit_file, init_repo

    attempt_id, _ = attempt
    repo = tmp_path / "repo"
    base = init_repo(repo)
    commit_file(repo, "src/app.py", "value = 2\n", "fix: guard value")
    head = commit_file(repo, "tests/test_app.py", "def test_value():\n    pass\n", "test: pin")
    export = await build_sealed_export(
        repo / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
    )
    await retain_export(qa_pool, attempt_id, export)
    await retain_export(qa_pool, attempt_id, export)  # idempotent
    assert await load_retained_export(qa_pool, attempt_id) == export
    changed = commit_file(repo, "src/app.py", "value = 3\n", "fix: tighten")
    different = await build_sealed_export(
        repo / ".git" / "objects", base_sha=base, head_sha=changed, identity=QA_IDENTITY
    )
    with pytest.raises(PublicationRefused, match="export_changed"):
        await retain_export(qa_pool, attempt_id, different)

    # Inside the window nothing prunes; past it the bytes go and the binding stays.
    # The QA role cannot backdate the close the window keys on (core_305 stamps it).
    await qa_pool.execute(
        "UPDATE public.healing_attempts SET status = 'failed', closed_at = now() - $2::interval "
        "WHERE id = $1",
        attempt_id,
        timedelta(days=31),
    )
    await prune_retained_exports(qa_pool)
    assert await load_retained_export(qa_pool, attempt_id) == export
    await _backdate_close(qa_url, attempt_id, timedelta(days=29))
    await prune_retained_exports(qa_pool)
    assert await load_retained_export(qa_pool, attempt_id) == export
    await _backdate_close(qa_url, attempt_id, timedelta(days=31))
    assert await prune_retained_exports(qa_pool) >= 1
    with pytest.raises(PublicationRefused, match="export_pruned"):
        await load_retained_export(qa_pool, attempt_id)
    with pytest.raises(PublicationRefused, match="export_pruned"):
        await retain_export(qa_pool, attempt_id, export)
    row = await qa_pool.fetchrow(
        "SELECT head_sha, digest, octet_length(objects) AS size "
        "FROM public.qa_publication_exports WHERE attempt_id = $1",
        attempt_id,
    )
    assert (row["head_sha"], row["digest"], row["size"]) == (export.head_sha, export.digest, 0)
