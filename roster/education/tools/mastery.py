"""Education butler — mastery tracking: quiz response recording, scoring, and struggle detection."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_RESPONSE_TYPES = {"diagnostic", "teach", "review"}

# Exponential recency weights for up to 5 responses (oldest → newest).
# Weight for position i (0=oldest, 4=newest) = 2^i, normalized so sum=1.
# Weights: [1, 2, 4, 8, 16] / 31  (for 5 responses)
# For fewer responses, we use the last N weights (newest-biased subset), re-normalized.
_WEIGHTS_5 = [1.0, 2.0, 4.0, 8.0, 16.0]


def _compute_mastery_score(qualities: list[int]) -> float:
    """Compute exponential recency-weighted mastery score from quality list.

    Parameters
    ----------
    qualities:
        List of quality scores (0-5), ordered oldest to newest.
        At most 5 entries are used; extras from the front are discarded.

    Returns
    -------
    float
        Weighted average quality / 5.0, clamped to [0.0, 1.0].
    """
    if not qualities:
        return 0.0
    # Take at most last 5
    recent = qualities[-5:]
    n = len(recent)
    # Use the last n weights from _WEIGHTS_5 (newest-biased)
    weights = _WEIGHTS_5[-n:]
    total_weight = sum(weights)
    weighted_sum = sum(q * w for q, w in zip(recent, weights))
    score = weighted_sum / (total_weight * 5.0)
    return max(0.0, min(1.0, score))


def _determine_new_status(
    current_status: str,
    response_type: str,
    quality: int,
    mastery_score: float,
    last_3_review_qualities: list[int],
) -> str | None:
    """Determine the new mastery_status based on the state machine rules.

    Returns the new status string, or None if no transition should be applied.

    State machine:
    - unseen + diagnostic → diagnosed
    - unseen + teach (no prior diagnostic) → learning
    - diagnosed + teach → learning
    - diagnosed + quality<3 → learning (self-correction)
    - learning + quality>=3 → reviewing
    - learning + quality<3 → stays learning (no transition needed)
    - reviewing + quality<3 → learning (regression)
    - reviewing + score>=0.85 AND last 3 review qualities all >=4 → mastered
    - mastered → stays mastered (never demoted via this mechanism)
    """
    if current_status == "mastered":
        # Mastered nodes are never demoted via mastery_record_response
        return None

    if current_status == "unseen":
        if response_type == "diagnostic":
            return "diagnosed"
        elif response_type == "teach":
            return "learning"
        # review on unseen: no valid transition defined; leave as-is
        return None

    if current_status == "diagnosed":
        if response_type == "teach":
            return "learning"
        if quality < 3:
            # Self-correction: poor quiz result on diagnosed node
            return "learning"
        # Good quality on diagnosed without teach: no transition defined
        return None

    if current_status == "learning":
        if quality >= 3:
            return "reviewing"
        # quality < 3: stays learning
        return None

    if current_status == "reviewing":
        if quality < 3:
            # Regression
            return "learning"
        # Check for mastery graduation:
        # score >= 0.85 AND last 3 review responses all quality >= 4
        if (
            mastery_score >= 0.85
            and len(last_3_review_qualities) >= 3
            and all(q >= 4 for q in last_3_review_qualities)
        ):
            return "mastered"
        # Otherwise stays reviewing
        return None

    return None


async def mastery_record_response(
    pool: asyncpg.Pool,
    node_id: str,
    mind_map_id: str,
    question_text: str,
    user_answer: str | None,
    quality: int,
    response_type: str = "review",
    session_id: str | None = None,
) -> str:
    """Record a quiz response and atomically update node mastery score and status.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the mind map node being quizzed.
    mind_map_id:
        UUID of the mind map containing the node.
    question_text:
        The question that was asked.
    user_answer:
        The user's answer text, or None.
    quality:
        SM-2 quality score, integer 0-5 (0=blackout, 5=perfect).
    response_type:
        One of 'diagnostic', 'teach', 'review'. Defaults to 'review'.
    session_id:
        Optional UUID of the current learning session.

    Returns
    -------
    str
        UUID of the newly created quiz_responses row.

    Raises
    ------
    ValueError
        If quality is outside [0, 5] or response_type is invalid.
    """
    if not (0 <= quality <= 5):
        raise ValueError(f"quality must be between 0 and 5, got {quality!r}")
    if response_type not in _VALID_RESPONSE_TYPES:
        raise ValueError(
            f"Invalid response_type {response_type!r}. "
            f"Must be one of {sorted(_VALID_RESPONSE_TYPES)}"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Insert the quiz response
            response_id = await conn.fetchval(
                """
                INSERT INTO education.quiz_responses
                    (node_id, mind_map_id, question_text, user_answer, quality,
                     response_type, session_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                node_id,
                mind_map_id,
                question_text,
                user_answer,
                quality,
                response_type,
                session_id,
            )

            # 2. Fetch last 5 responses (ordered oldest→newest for weighting)
            rows = await conn.fetch(
                """
                SELECT quality FROM education.quiz_responses
                WHERE node_id = $1
                ORDER BY responded_at DESC
                LIMIT 5
                """,
                node_id,
            )
            # rows are newest→oldest; reverse to oldest→newest for weighting
            qualities = [row["quality"] for row in reversed(rows)]
            new_score = _compute_mastery_score(qualities)

            # 3. Fetch current node status and verify it belongs to the mind map
            node_row = await conn.fetchrow(
                """
                SELECT mastery_status FROM education.mind_map_nodes
                WHERE id = $1 AND mind_map_id = $2
                """,
                node_id,
                mind_map_id,
            )
            if not node_row:
                raise ValueError(
                    f"Node {node_id!r} not found in mind map {mind_map_id!r}. "
                    "Ensure node_id and mind_map_id are consistent."
                )
            current_status = node_row["mastery_status"]

            # 4. Fetch last 3 review-type qualities for mastery graduation check
            review_rows = await conn.fetch(
                """
                SELECT quality FROM education.quiz_responses
                WHERE node_id = $1 AND response_type = 'review'
                ORDER BY responded_at DESC
                LIMIT 3
                """,
                node_id,
            )
            last_3_review_qualities = [row["quality"] for row in review_rows]

            # 5. Determine new mastery status
            new_status = _determine_new_status(
                current_status=current_status,
                response_type=response_type,
                quality=quality,
                mastery_score=new_score,
                last_3_review_qualities=last_3_review_qualities,
            )

            # 6. Update the node's mastery_score (and status if transitioning)
            update_fields: dict[str, Any] = {"mastery_score": new_score}
            if new_status is not None and new_status != current_status:
                update_fields["mastery_status"] = new_status

            # We must update within the same transaction.
            # Build the SET clause manually (mind_map_node_update uses pool not conn,
            # so we update directly here within the transaction).
            set_parts = ["mastery_score = $1", "updated_at = now()"]
            values: list[Any] = [new_score]
            param_idx = 2

            if "mastery_status" in update_fields:
                set_parts.append(f"mastery_status = ${param_idx}")
                values.append(new_status)
                param_idx += 1

            values.append(node_id)
            sql = f"""
                UPDATE education.mind_map_nodes
                SET {", ".join(set_parts)}
                WHERE id = ${param_idx}
            """
            await conn.execute(sql, *values)

            # 7. Auto-completion: if node was just mastered, check if all nodes mastered
            if update_fields.get("mastery_status") == "mastered":
                unmastered_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM education.mind_map_nodes
                    WHERE mind_map_id = $1 AND mastery_status != 'mastered'
                    """,
                    mind_map_id,
                )
                if unmastered_count == 0:
                    node_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM education.mind_map_nodes WHERE mind_map_id = $1",
                        mind_map_id,
                    )
                    if node_count > 0:
                        await conn.execute(
                            """
                            UPDATE education.mind_maps
                            SET status = 'completed', updated_at = now()
                            WHERE id = $1
                            """,
                            mind_map_id,
                        )

            return str(response_id)


async def mastery_get_node_history(
    pool: asyncpg.Pool,
    node_id: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return quiz response history for a node, ordered most recent first.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the node.
    limit:
        Optional cap on number of results. When None, all responses are returned.

    Returns
    -------
    list of dict
        Quiz response rows ordered by responded_at DESC.
    """
    if limit is not None:
        rows = await pool.fetch(
            """
            SELECT id, node_id, mind_map_id, question_text, user_answer, quality,
                   response_type, session_id, responded_at
            FROM education.quiz_responses
            WHERE node_id = $1
            ORDER BY responded_at DESC
            LIMIT $2
            """,
            node_id,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, node_id, mind_map_id, question_text, user_answer, quality,
                   response_type, session_id, responded_at
            FROM education.quiz_responses
            WHERE node_id = $1
            ORDER BY responded_at DESC
            """,
            node_id,
        )
    return [_row_to_dict(row) for row in rows]


async def mastery_get_map_summary(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> dict[str, Any]:
    """Return aggregate mastery statistics for all nodes in a mind map.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict with keys:
        total_nodes, mastered_count, learning_count, reviewing_count,
        unseen_count, diagnosed_count, avg_mastery_score, struggling_node_ids
    """
    # Aggregate status counts and avg mastery score in one query.
    # COALESCE each SUM to 0: PostgreSQL returns NULL for SUM over an empty result set
    # (no rows match the WHERE clause), which would cause int(None) → TypeError.
    summary_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_nodes,
            COALESCE(
                SUM(CASE WHEN mastery_status = 'mastered'  THEN 1 ELSE 0 END), 0
            ) AS mastered_count,
            COALESCE(
                SUM(CASE WHEN mastery_status = 'learning'  THEN 1 ELSE 0 END), 0
            ) AS learning_count,
            COALESCE(
                SUM(CASE WHEN mastery_status = 'reviewing' THEN 1 ELSE 0 END), 0
            ) AS reviewing_count,
            COALESCE(
                SUM(CASE WHEN mastery_status = 'unseen'    THEN 1 ELSE 0 END), 0
            ) AS unseen_count,
            COALESCE(
                SUM(CASE WHEN mastery_status = 'diagnosed' THEN 1 ELSE 0 END), 0
            ) AS diagnosed_count,
            COALESCE(AVG(mastery_score), 0.0) AS avg_mastery_score
        FROM education.mind_map_nodes
        WHERE mind_map_id = $1
        """,
        mind_map_id,
    )

    # Get struggling node IDs
    struggles = await mastery_detect_struggles(pool, mind_map_id)
    struggling_node_ids = [s["id"] for s in struggles]

    return {
        "total_nodes": int(summary_row["total_nodes"]),
        "mastered_count": int(summary_row["mastered_count"]),
        "learning_count": int(summary_row["learning_count"]),
        "reviewing_count": int(summary_row["reviewing_count"]),
        "unseen_count": int(summary_row["unseen_count"]),
        "diagnosed_count": int(summary_row["diagnosed_count"]),
        "avg_mastery_score": float(summary_row["avg_mastery_score"]),
        "struggling_node_ids": struggling_node_ids,
    }


async def mastery_detect_struggles(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> list[dict[str, Any]]:
    """Identify nodes with declining or consistently low mastery.

    A node is flagged as struggling if:
    1. Its 3 most recent quiz responses (any type) all have quality <= 2, OR
    2. Its mastery_score has declined over the last 3 responses
       (score from responses 1-2 > score from responses 2-3 > current score,
        i.e., each subsequent window is lower than the previous).

    Nodes with mastery_status = 'mastered' are excluded.
    Nodes with fewer than 3 responses are not flagged.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to scan.

    Returns
    -------
    list of dict
        Each dict has: id, label, mastery_score, mastery_status, reason.
        reason is one of: 'consecutive_low_quality', 'declining_score', or
        'consecutive_low_quality,declining_score' when both apply.
    """
    # Fetch all non-mastered nodes in this map
    node_rows = await pool.fetch(
        """
        SELECT id, label, mastery_score, mastery_status
        FROM education.mind_map_nodes
        WHERE mind_map_id = $1 AND mastery_status != 'mastered'
        """,
        mind_map_id,
    )

    if not node_rows:
        return []

    node_ids = [str(row["id"]) for row in node_rows]

    # Batch-fetch the last 3 quiz responses for all nodes in a single query,
    # using ROW_NUMBER() to avoid an N+1 query per node.
    # Responses are ordered newest-first within each node partition.
    response_rows = await pool.fetch(
        """
        SELECT node_id::text AS node_id, quality, rn
        FROM (
            SELECT node_id, quality,
                   ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY responded_at DESC) AS rn
            FROM education.quiz_responses
            WHERE node_id = ANY($1::uuid[])
        ) ranked
        WHERE rn <= 3
        ORDER BY node_id, rn
        """,
        node_ids,
    )

    # Group qualities per node (rn=1 is newest)
    qualities_by_node: dict[str, list[int]] = defaultdict(list)
    for row in response_rows:
        qualities_by_node[row["node_id"]].append(row["quality"])
    # Each node's list is already ordered newest-first (rn=1..3), sorted by rn above.

    node_index = {str(row["id"]): row for row in node_rows}

    results = []
    for node_id in node_ids:
        qualities = qualities_by_node.get(node_id, [])  # newest first

        if len(qualities) < 3:
            # Insufficient history — not flagged
            continue

        node_row = node_index[node_id]
        reasons = []

        # Check condition 1: consecutive low quality (all 3 most recent quality <= 2)
        if all(q <= 2 for q in qualities):
            reasons.append("consecutive_low_quality")

        # Check condition 2: declining mastery score over last 3 responses.
        # We compare recency-weighted scores computed from progressively larger windows:
        # - score_1: weighted score of the newest response only
        # - score_2: weighted score of the 2 most recent responses (oldest→newest order)
        # - score_3: weighted score of the 3 most recent responses (oldest→newest order)
        # If score_3 > score_2 > score_1, the further-back windows yield a higher score,
        # meaning the student's performance has been declining over these 3 responses.
        # This catches both strict-monotone declines and plateau-then-crash patterns
        # (e.g. [3, 3, 0] — oldest to newest — is correctly flagged).
        score_1 = _compute_mastery_score([qualities[0]])
        score_2 = _compute_mastery_score([qualities[1], qualities[0]])
        score_3 = _compute_mastery_score([qualities[2], qualities[1], qualities[0]])
        if score_3 > score_2 > score_1:
            reasons.append("declining_score")

        if reasons:
            results.append(
                {
                    "id": node_id,
                    "label": str(node_row["label"]),
                    "mastery_score": float(node_row["mastery_score"]),
                    "mastery_status": str(node_row["mastery_status"]),
                    "reason": ",".join(reasons),
                }
            )

    return results
