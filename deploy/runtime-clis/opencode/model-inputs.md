# Complete pinned models.dev data snapshot

`model-inputs.tar.gz` contains the entire original public API response and all
235 provider logos selected by `build-inputs.json`: 235 providers and 8,939
models, with no omitted or rewritten records. The catalogue remains exactly
5,965,098 bytes, SHA256
`e5f5f63b5fc6460b7f42f592f3c33c517f06205f1781be50a1def87c2b23a421`.
The existing per-logo URL, size and SHA256 declarations remain unchanged.

Primary sources are [the models.dev API](https://models.dev/api.json) and the
235 exact `https://models.dev/logos/<provider>.svg` URLs in that manifest. The
[upstream API documentation](https://github.com/anomalyco/models.dev#api)
describes a generated live API. It is mutable: a later acquisition already had
8,940 models and different bytes. The producer therefore stages this complete
source-owned snapshot instead of fetching a replacement during a build.

The accompanying `LICENSE.models-dev` is the exact
[upstream MIT license](https://github.com/anomalyco/models.dev/blob/dev/LICENSE),
SHA256 `dc2dc41c9fea2fd3c41c21c6f484844cab367800b88ff24781f980ad3a9d160a`.
That license capture establishes attribution; it does not assert a historical
Git commit for the generated API response. No dataset commit or immutable
upstream distribution URL is invented.

The archive has exactly 236 regular data members: `api.json` and all 235
`logos/<provider>.svg` files. Names are globally lexicographically ordered;
all modes are 0644, uid/gid and mtime are zero, user/group names are empty.
It uses USTAR, gzip level 9 with mtime zero and an empty gzip filename. Its
875,537 bytes and SHA256 are pinned in `build-inputs.json`. Two independent
assemblies from the verified original payloads produced identical bytes.

To reproduce, verify the retained API and each logo against the existing
manifest, then add those exact bytes in sorted member-name order using
`tarfile.USTAR_FORMAT`, `TarInfo` with the metadata above and
`gzip.GzipFile(filename="", mtime=0, compresslevel=9)`. No extraction path,
link, executable, package code, secret or task receipt is included.

The producer refuses changed archive bytes, missing/extra/duplicate/reordered
members, links, unsafe paths, noncanonical metadata, oversize data and any
per-member hash or catalogue/logo population mismatch. It checks all headers
before writing, stages into a private temporary directory, validates the whole
population and renames it atomically. A failed stage leaves no model output.

Tests: the removed live-download atomic/hash, User-Agent and 100 redirect
cases are superseded by `test_model_snapshot_is_complete_atomic_and_transport_independent`
and `test_model_snapshot_refuses_noncanonical_or_changed_data`: there is no
model HTTP operation to follow a redirect. The original archive acquisition
controls for source, toolchain and npm inputs remain in their owning helpers.
