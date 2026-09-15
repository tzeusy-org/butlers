"""Tests for attachment_view (bu-2jtfw.7): the vision content-block tool.

Covers the real seam — ``butlers.tools.attachments.attachment_view``, the
function ``register_media_tools`` (src/butlers/core_tools/_media.py) wraps
with no additional branching — plus a repo-wide contract guard: no tool
result anywhere embeds a ``data_base64`` payload over 64KB.
"""

from __future__ import annotations

import ast
import base64
import pathlib

import boto3
import pytest
from fastmcp.utilities.types import Image
from moto.server import ThreadedMotoServer

from butlers.storage import S3BlobStore
from butlers.tools.attachments import (
    MAX_ATTACHMENT_SIZE_BYTES,
    MAX_INLINE_BASE64_BYTES,
    attachment_view,
    get_attachment,
)

TEST_BUCKET = "test-butlers-blobs"
TEST_BUTLER = "testbutler"


@pytest.fixture(scope="module")
def moto_s3_server():
    """Start a moto HTTP server for S3."""
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    endpoint = f"http://localhost:{server._server.server_address[1]}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
    )
    client.create_bucket(Bucket=TEST_BUCKET)
    yield endpoint
    server.stop()


@pytest.fixture
def blob_store(moto_s3_server):
    return S3BlobStore(
        bucket=TEST_BUCKET,
        butler_name=TEST_BUTLER,
        endpoint_url=moto_s3_server,
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
    )


async def test_attachment_view_returns_image_content_block_for_image(blob_store):
    """An in-cap image/* blob returns a real Image, not a JSON base64 dict."""
    data = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
    storage_ref = await blob_store.put(data, content_type="image/png", filename="test.png")

    result = await attachment_view(blob_store, storage_ref)

    assert isinstance(result, Image)
    content = result.to_image_content()
    assert content.type == "image"
    assert content.mimeType == "image/png"
    assert base64.b64decode(content.data) == data


async def test_attachment_view_refuses_over_cap_blob(blob_store):
    """A blob over the vision size cap returns a typed refusal, not a data dump."""
    large_data = b"x" * (MAX_ATTACHMENT_SIZE_BYTES + 1)
    storage_ref = await blob_store.put(large_data, content_type="image/jpeg")

    result = await attachment_view(blob_store, storage_ref)

    assert isinstance(result, dict)
    assert result["status"] == "refused"
    assert result["reason"] == "attachment_too_large"
    assert result["storage_ref"] == storage_ref
    # The refusal must never carry the payload itself.
    assert "data" not in result
    assert "data_base64" not in result


async def test_attachment_view_refuses_non_image_media_type(blob_store):
    """A non-image attachment (e.g. a PDF) is refused, not silently served as an image."""
    storage_ref = await blob_store.put(b"%PDF-1.4 fake", content_type="application/pdf")

    result = await attachment_view(blob_store, storage_ref)

    assert isinstance(result, dict)
    assert result["status"] == "refused"
    assert result["reason"] == "unsupported_media_type"


async def test_attachment_view_refuses_missing_blob_store():
    result = await attachment_view(None, "s3://test-butlers-blobs/testbutler/2026/01/01/x.jpg")
    assert result["status"] == "refused"
    assert result["reason"] == "blob_store_unavailable"


async def test_attachment_view_refuses_not_found(blob_store):
    result = await attachment_view(
        blob_store, f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nope.jpg"
    )
    assert result["status"] == "refused"
    assert result["reason"] == "not_found"


# ---------------------------------------------------------------------------
# Contract: no tool result anywhere contains data_base64 over 64KB.
# ---------------------------------------------------------------------------


async def test_get_attachment_never_returns_data_base64_over_inline_cap(blob_store):
    """get_attachment refuses (never truncates/dumps) once base64 would exceed the cap."""
    just_under = b"x" * (MAX_INLINE_BASE64_BYTES // 2)  # well under the 64KB cap
    ref_small = await blob_store.put(just_under, content_type="application/octet-stream")
    small_result = await get_attachment(blob_store, ref_small)
    assert "data_base64" in small_result
    assert len(small_result["data_base64"]) <= MAX_INLINE_BASE64_BYTES

    # Comfortably over the inline cap but under the absolute 5MB ceiling —
    # this used to return several hundred KB of base64 JSON; now it refuses.
    over_cap = b"x" * (MAX_INLINE_BASE64_BYTES * 4)
    ref_large = await blob_store.put(over_cap, content_type="application/octet-stream")
    large_result = await get_attachment(blob_store, ref_large)
    assert large_result["status"] == "refused"
    assert "data_base64" not in large_result


def test_no_other_tool_source_embeds_a_data_base64_field() -> None:
    """Static guard: `data_base64` as a dict key exists only in tools/attachments.py.

    get_attachment is the one sanctioned, capped inline-base64 path
    (bu-2jtfw.7). If a future tool reintroduces the anti-pattern this fixed —
    embedding a blob's base64 directly in a JSON tool result — this guard
    catches it at review time instead of relying on every author to remember
    the 64KB rule.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    src_dirs = [repo_root / "src" / "butlers", repo_root / "roster"]
    allowed_files = {repo_root / "src" / "butlers" / "tools" / "attachments.py"}

    offenders: list[str] = []
    for src_dir in src_dirs:
        for path in src_dir.rglob("*.py"):
            if path in allowed_files or "/tests/" in str(path) or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "data_base64":
                    offenders.append(f"{path.relative_to(repo_root)}:{node.lineno}")

    assert not offenders, (
        "Found 'data_base64' outside the sanctioned inline-cap path "
        f"(tools/attachments.py): {offenders}. Route images through "
        "attachment_view() instead of embedding base64 in a JSON tool result."
    )
