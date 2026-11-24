"""Bind owner-published PRs only from retained, independently verified evidence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from butlers.core.qa.publication.binding import (
    PublicationBinding,
    pull_request_url,
)
from butlers.core.qa.publication.github_reader import GithubPublicationReader
from butlers.core.qa.publication.retention import EXPORT_RETENTION, load_retained_export

logger = logging.getLogger(__name__)


async def bind_owner_publications(
    pool: Any,
    token: str,
    *,
    reader_factory: Callable[..., Any] | None = None,
) -> int:
    """Observe candidates read-only and atomically associate each proven PR once."""
    reader_factory = reader_factory or GithubPublicationReader
    rows = await pool.fetch(
        """
        SELECT a.id, a.branch_name, x.target_repository, x.target_base_ref, x.target_branch
        FROM public.healing_attempts a
        JOIN public.qa_publication_exports x ON x.attempt_id = a.id
        WHERE a.qa_patrol_id IS NOT NULL AND a.status = 'failed'
          AND a.error_detail LIKE 'publication_unavailable:%'
          AND x.pruned_at IS NULL AND a.closed_at > now() - $1::interval
        ORDER BY a.closed_at, a.id
    """,
        EXPORT_RETENTION,
    )
    count = 0
    for row in rows:
        try:
            if (
                not row["target_repository"]
                or not row["target_base_ref"]
                or not row["target_branch"]
            ):
                logger.info("QA owner publication skipped: target_unproven")
                continue
            if row["branch_name"] != row["target_branch"]:
                logger.info("QA owner publication skipped: target_mismatch")
                continue
            export = await load_retained_export(pool, row["id"])
            if export is None:
                logger.info("QA owner publication skipped: export_unavailable")
                continue
            binding = PublicationBinding(
                row["target_repository"],
                row["branch_name"],
                row["target_base_ref"],
                export.head_sha,
            )
            allowed = await pool.fetchval(
                """
                SELECT EXISTS (SELECT 1 FROM public.qa_allowed_repositories
                    WHERE enabled AND lower(owner || '/' || repo) = lower($1))
            """,
                binding.repository,
            )
            if not allowed:
                logger.info("QA owner publication skipped: target_disallowed")
                continue
            reader = reader_factory(binding.repository, token=token)
            if reader.repository.lower() != binding.repository.lower():
                logger.info("QA owner publication skipped: target_mismatch")
                continue
            numbers = await reader.find_pull_requests(head=binding.branch, base=binding.base_ref)
            if len(numbers) != 1:
                logger.info("QA owner publication skipped: pr_not_unique")
                continue
            pr = await reader.read_pull_request(number=numbers[0])
            if (
                pr is None
                or pr.number != numbers[0]
                or pr.head != binding.branch
                or pr.base != binding.base_ref
                or pr.head_sha != binding.head_sha
            ):
                logger.info("QA owner publication skipped: pr_mismatch")
                continue
            # Recheck eligibility and immutable evidence using database time in the write.
            updated = await pool.fetchval(
                """
                UPDATE public.healing_attempts a
                   SET status = 'pr_open', pr_url = $2, pr_number = $3,
                       publication_provenance = 'owner_published', updated_at = now()
                  FROM public.qa_publication_exports x
                 WHERE a.id = $1 AND x.attempt_id = a.id
                   AND a.qa_patrol_id IS NOT NULL AND a.status = 'failed'
                   AND a.error_detail LIKE 'publication_unavailable:%'
                   AND a.pr_url IS NULL AND a.pr_number IS NULL AND a.branch_name = $4
                   AND a.closed_at > now() - $5::interval AND x.pruned_at IS NULL
                   AND x.target_repository = $6 AND x.target_base_ref = $7
                   AND x.target_branch = $4
                   AND x.head_sha = $8 AND x.base_sha = $9 AND x.digest = $10
                   AND EXISTS (SELECT 1 FROM public.qa_allowed_repositories
                       WHERE enabled AND lower(owner || '/' || repo) = lower($6))
                   AND NOT EXISTS (SELECT 1 FROM public.qa_publication_operations op
                       WHERE op.attempt_id = a.id AND op.stage IN ('egress_started', 'ambiguous'))
                RETURNING a.id
            """,
                row["id"],
                pull_request_url(binding.repository, pr.number),
                pr.number,
                binding.branch,
                EXPORT_RETENTION,
                binding.repository,
                binding.base_ref,
                export.head_sha,
                export.base_sha,
                export.digest,
            )
            count += updated is not None
        except Exception as exc:  # observation or persistence failures must not alter the attempt
            logger.warning("QA owner publication skipped: %s", type(exc).__name__)
    return count
