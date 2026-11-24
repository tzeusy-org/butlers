#!/usr/bin/env python3
"""Fetch only the public Debian archives pinned by the reviewed base manifest.

This prepares build inputs; it never installs or executes package content.
Proxies, credentials and package-manager configuration are not consulted.
Every redirect is refused before another destination can be opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


class _RefuseRedirects(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        if 300 <= response.status < 400:
            response.close()
            raise ValueError("system artifact redirects are forbidden")
        return super().http_response(request, response)

    https_response = http_response


def fetch(manifest: Path, output: Path) -> None:
    value = json.loads(manifest.read_text())
    if value["schema"] != "butlers.debian-base-inputs/v1":
        raise ValueError("unsupported system artifacts schema")
    if output.exists() or output.is_symlink():
        raise ValueError("system artifacts output already exists")
    records = value["debs"]
    names = [item["filename"] for item in records]
    if not names or len(names) != len(set(names)):
        raise ValueError("empty or duplicate system artifacts")
    urls = []
    for item in records:
        url = urllib.parse.urlsplit(item["url"])
        if (
            Path(item["filename"]).name != item["filename"]
            or not item["filename"].endswith(".deb")
            or url.scheme not in {"http", "https"}
            or url.netloc != "deb.debian.org"
            or not url.path.startswith(("/debian/pool/", "/debian-security/pool/"))
            or url.query
            or url.fragment
        ):
            raise ValueError("system artifact must use its pinned public Debian URL")
        urls.append(urllib.parse.urlunsplit(("https", url.netloc, url.path, "", "")))
    output.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
    with tempfile.TemporaryDirectory(prefix="system-artifacts-", dir=output.parent) as temporary:
        context = Path(temporary) / "context"
        debs = context / "debs"
        debs.mkdir(parents=True)
        for item, url in zip(records, urls, strict=True):
            digest = hashlib.sha256()
            size = 0
            with (
                opener.open(url, timeout=60) as response,
                (debs / item["filename"]).open("wb") as file,
            ):
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > item["size"]:
                        raise ValueError("system artifact exceeds the pinned size")
                    digest.update(chunk)
                    file.write(chunk)
            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("system artifact differs from the pinned bytes")
            (debs / item["filename"]).chmod(0o644)
        (context / "provenance.json").write_text(
            json.dumps(
                {
                    "schema": "butlers.system-artifacts/v1",
                    "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "archive_count": len(records),
                    "scope": "Pinned public inputs; no installation or image attestation.",
                },
                indent=2,
            )
            + "\n"
        )
        context.rename(output)
    print(json.dumps({"archive_count": len(records), "output": str(output)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    fetch(arguments.manifest, arguments.output)


if __name__ == "__main__":
    main()
