#!/usr/bin/env python3
"""Migrate blob storage from local filesystem to S3-compatible backend.

Reads attachment_refs rows with local:// blob_ref values, uploads the
corresponding files from data/blobs/ to S3, and updates the DB to s3:// refs.

Usage:
    BLOB_S3_ENDPOINT_URL=http://nas:9000 \
    BLOB_S3_BUCKET=butlers-blobs \
    BLOB_S3_ACCESS_KEY_ID=... \
    BLOB_S3_SECRET_ACCESS_KEY=... \
    BLOB_S3_REGION=garage \
    python scripts/migrate_blobs_to_s3.py \
        --local-blobs-dir data/blobs \
        --butler-name switchboard \
        --db-url postgres://butlers:butlers@localhost:54320/butlers

The script is idempotent: re-running skips already-migrated s3:// refs.
"""

import argparse
import asyncio
import mimetypes
import os
import sys
from pathlib import Path

import aioboto3
import asyncpg
from botocore.config import Config as BotoConfig


async def migrate(
    *,
    db_url: str,
    schema: str,
    local_blobs_dir: Path,
    butler_name: str,
    endpoint_url: str,
    bucket: str,
    access_key_id: str | None,
    secret_access_key: str | None,
    region: str,
    dry_run: bool = False,
) -> dict:
    """Run the migration. Returns summary stats."""
    stats = {"total": 0, "migrated": 0, "skipped": 0, "failed": 0}

    conn = await asyncpg.connect(db_url)
    try:
        # Set search path to the butler's schema
        await conn.execute(f"SET search_path TO {schema}, shared, public")

        rows = await conn.fetch(
            "SELECT message_id, attachment_id, blob_ref FROM attachment_refs "
            "WHERE blob_ref IS NOT NULL"
        )
        stats["total"] = len(rows)

        session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        boto_config = BotoConfig(s3={"addressing_style": "path"})

        async with session.client("s3", endpoint_url=endpoint_url, config=boto_config) as s3:
            for row in rows:
                blob_ref = row["blob_ref"]

                # Skip already-migrated refs
                if blob_ref.startswith("s3://"):
                    stats["skipped"] += 1
                    continue

                if not blob_ref.startswith("local://"):
                    print(f"  WARN: Unknown scheme, skipping: {blob_ref}", file=sys.stderr)
                    stats["failed"] += 1
                    continue

                local_key = blob_ref.split("://", 1)[1]
                local_path = local_blobs_dir / local_key

                if not local_path.exists():
                    print(
                        f"  WARN: Local file missing, skipping: {local_path}",
                        file=sys.stderr,
                    )
                    stats["failed"] += 1
                    continue

                # Build S3 key: butler_name/original_key
                s3_key = f"{butler_name}/{local_key}"
                content_type = (
                    mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
                )

                if dry_run:
                    print(f"  DRY RUN: {blob_ref} -> s3://{bucket}/{s3_key}")
                    stats["migrated"] += 1
                    continue

                # Upload to S3
                data = local_path.read_bytes()
                await s3.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=data,
                    ContentType=content_type,
                )

                # Update DB
                new_ref = f"s3://{bucket}/{s3_key}"
                await conn.execute(
                    "UPDATE attachment_refs SET blob_ref = $1 "
                    "WHERE message_id = $2 AND attachment_id = $3",
                    new_ref,
                    row["message_id"],
                    row["attachment_id"],
                )
                stats["migrated"] += 1
                print(f"  OK: {blob_ref} -> {new_ref}")
    finally:
        await conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate local blob refs to S3")
    parser.add_argument(
        "--local-blobs-dir",
        type=Path,
        default=Path("data/blobs"),
        help="Path to local blobs directory (default: data/blobs)",
    )
    parser.add_argument(
        "--butler-name",
        required=True,
        help="Butler name for S3 key prefix",
    )
    parser.add_argument(
        "--schema",
        default="switchboard",
        help="Database schema containing attachment_refs (default: switchboard)",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get(
            "DATABASE_URL", "postgres://butlers:butlers@localhost:54320/butlers"
        ),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    args = parser.parse_args()

    endpoint_url = os.environ.get("BLOB_S3_ENDPOINT_URL", "")
    bucket = os.environ.get("BLOB_S3_BUCKET", "")
    access_key_id = os.environ.get("BLOB_S3_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("BLOB_S3_SECRET_ACCESS_KEY")
    region = os.environ.get("BLOB_S3_REGION", "us-east-1")

    if not endpoint_url or not bucket:
        print(
            "ERROR: BLOB_S3_ENDPOINT_URL and BLOB_S3_BUCKET must be set",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Migrating blobs from {args.local_blobs_dir} to s3://{bucket}/")
    print(f"  Endpoint: {endpoint_url}")
    print(f"  Butler: {args.butler_name}")
    print(f"  Schema: {args.schema}")
    print(f"  Dry run: {args.dry_run}")
    print()

    stats = asyncio.run(
        migrate(
            db_url=args.db_url,
            schema=args.schema,
            local_blobs_dir=args.local_blobs_dir,
            butler_name=args.butler_name,
            endpoint_url=endpoint_url,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
            dry_run=args.dry_run,
        )
    )

    print()
    print("Summary:")
    print(f"  Total refs:  {stats['total']}")
    print(f"  Migrated:    {stats['migrated']}")
    print(f"  Skipped:     {stats['skipped']} (already s3://)")
    print(f"  Failed:      {stats['failed']} (missing files or unknown scheme)")


if __name__ == "__main__":
    main()
