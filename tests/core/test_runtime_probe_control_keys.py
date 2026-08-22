"""Strict parsing and startup behaviour for runtime-probe control keys.

Covers REQ-core-credentials-002 (Asymmetric Runtime-Probe Control Capability):
the deployment-provisioned signer and verifier keyring, their exact document
shapes, the retirement overlap bounds, and the immutable restart-only process
snapshots.

Every key in this module is synthetic and generated inside the test from
``os.urandom``.  No key material is printed, logged, or asserted on positively;
redaction is checked by asserting the *absence* of the generated material.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.core.runtime_probe_control import keys

_CUTOVER = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Synthetic document builders
# ---------------------------------------------------------------------------


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _synthetic_pair() -> tuple[bytes, bytes]:
    """A throwaway Ed25519 keypair as ``(seed, raw public key)``."""
    seed = os.urandom(32)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    return seed, private_key.public_key().public_bytes_raw()


def _stamp(value: datetime) -> str:
    return keys.format_utc_second(value)


def _signer_document(
    seed: bytes,
    *,
    kid: str = "probe-2026-05a",
    sign_from: datetime = _CUTOVER,
    sign_until: datetime | None = None,
    **overrides: object,
) -> dict[str, object]:
    document: dict[str, object] = {
        "version": 1,
        "alg": "EdDSA",
        "kid": kid,
        "private_key_b64u": _b64u(seed),
        "sign_from": _stamp(sign_from),
        "sign_until": None if sign_until is None else _stamp(sign_until),
    }
    document.update(overrides)
    return document


def _current_entry(
    public_key: bytes,
    *,
    kid: str = "probe-2026-05a",
    sign_from: datetime = _CUTOVER,
    **overrides: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "alg": "EdDSA",
        "kid": kid,
        "public_key_b64u": _b64u(public_key),
        "sign_from": _stamp(sign_from),
    }
    entry.update(overrides)
    return entry


def _retiring_entry(
    public_key: bytes,
    *,
    kid: str = "probe-2026-04a",
    sign_from: datetime = _CUTOVER - timedelta(days=30),
    sign_until: datetime = _CUTOVER,
    overlap: timedelta = timedelta(seconds=120),
    **overrides: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "alg": "EdDSA",
        "kid": kid,
        "public_key_b64u": _b64u(public_key),
        "sign_from": _stamp(sign_from),
        "sign_until": _stamp(sign_until),
        "accept_until": _stamp(sign_until + overlap),
    }
    entry.update(overrides)
    return entry


def _keyring_document(
    current: dict[str, object],
    retiring: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    document: dict[str, object] = {
        "version": 1,
        "current": current,
        "retiring": [] if retiring is None else retiring,
    }
    document.update(overrides)
    return document


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


def _write_document(path: Path, document: object, *, mode: int) -> Path:
    # Replace rather than rewrite: an existing fixture is mode 0400.
    path.unlink(missing_ok=True)
    path.write_bytes(_encode(document))
    path.chmod(mode)
    return path


@pytest.fixture
def matched_pair(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """A signer file and keyring file that agree, plus the synthetic seed."""
    seed, public_key = _synthetic_pair()
    signer_path = _write_document(
        tmp_path / "signing_key", _signer_document(seed), mode=keys.SIGNER_FILE_MODE
    )
    keyring_path = _write_document(
        tmp_path / "verifiers", _keyring_document(_current_entry(public_key)), mode=0o444
    )
    return signer_path, keyring_path, seed


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    keys._reset_snapshots_for_tests()
    yield
    keys._reset_snapshots_for_tests()


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------


def test_signer_document_derives_its_public_half_from_the_seed() -> None:
    """The pairing is computed, not trusted: a document cannot assert it."""
    seed, public_key = _synthetic_pair()

    signer = keys.parse_signer_document(_encode(_signer_document(seed)))

    assert signer.kid == "probe-2026-05a"
    assert signer.public_key == public_key
    assert signer.sign_until is None
    assert signer.sign_from == _CUTOVER


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"version": 2}, id="future-version"),
        pytest.param({"version": 0}, id="zero-version"),
        pytest.param({"version": "1"}, id="string-version"),
        pytest.param({"version": True}, id="bool-version-would-equal-one"),
        pytest.param({"version": 1.0}, id="float-version"),
        pytest.param({"alg": "Ed25519"}, id="near-miss-algorithm"),
        pytest.param({"alg": "none"}, id="unsigned-algorithm"),
        pytest.param({"alg": "HS256"}, id="symmetric-algorithm"),
        pytest.param({"alg": None}, id="null-algorithm"),
    ],
)
def test_signer_document_requires_the_approved_version_and_algorithm(
    overrides: dict[str, object],
) -> None:
    seed, _ = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(_signer_document(seed, **overrides)))


def test_signer_document_rejects_an_unknown_field() -> None:
    """An unknown field is a configuration mistake, not something to ignore."""
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    document["accept_until"] = _stamp(_CUTOVER)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_signer_document_rejects_a_missing_field() -> None:
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    del document["sign_until"]

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_signer_document_rejects_a_repeated_field() -> None:
    """``json.loads`` keeps the last duplicate silently; the parser must not."""
    seed, decoy = _synthetic_pair()
    raw = _encode(_signer_document(seed))
    injected = f', "private_key_b64u": "{_b64u(decoy)}"'.encode()
    raw = raw[:-1] + injected + b"}"

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(raw)


def test_signer_document_rejects_non_finite_numbers() -> None:
    """Python's JSON decoder accepts ``NaN`` by default; this one must not."""
    seed, _ = _synthetic_pair()
    raw = _encode(_signer_document(seed)).replace(b'"version": 1', b'"version": NaN')

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(raw)


def test_signer_document_rejects_invalid_utf8() -> None:
    seed, _ = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(_signer_document(seed)) + b"\xff")


@pytest.mark.parametrize(
    "kid",
    [
        pytest.param("probe-2026-05a", id="hyphenated"),
        pytest.param("a", id="single-character"),
        pytest.param("A.b_c-0", id="full-charset"),
        pytest.param("k" * 64, id="maximum-length"),
    ],
)
def test_key_id_grammar_accepts_the_approved_shapes(kid: str) -> None:
    seed, _ = _synthetic_pair()

    assert keys.parse_signer_document(_encode(_signer_document(seed, kid=kid))).kid == kid


@pytest.mark.parametrize(
    "kid",
    [
        pytest.param("", id="empty"),
        pytest.param("k" * 65, id="too-long"),
        pytest.param("probe 2026", id="space"),
        pytest.param("probe/2026", id="path-separator"),
        pytest.param("../probe", id="traversal"),
        pytest.param("probe\n2026", id="newline"),
        pytest.param("probe:2026", id="colon"),
        pytest.param("prôbe", id="non-ascii"),
    ],
)
def test_key_id_grammar_rejects_everything_else(kid: str) -> None:
    seed, _ = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(_signer_document(seed, kid=kid)))


def test_key_id_must_be_a_string() -> None:
    seed, _ = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(_signer_document(seed, kid=1)))


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(lambda raw: base64.urlsafe_b64encode(raw).decode(), id="padded"),
        pytest.param(
            lambda raw: base64.b64encode(raw).rstrip(b"=").decode(), id="standard-alphabet"
        ),
        pytest.param(lambda raw: raw.hex(), id="hex"),
        pytest.param(lambda raw: _b64u(raw[:31]), id="thirty-one-bytes"),
        pytest.param(lambda raw: _b64u(raw + b"\x00"), id="thirty-three-bytes"),
        pytest.param(lambda raw: _b64u(raw)[:-1] + " ", id="trailing-space"),
        pytest.param(lambda raw: "", id="empty"),
    ],
)
def test_key_material_must_be_unpadded_base64url_of_32_bytes(encoded) -> None:
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    document["private_key_b64u"] = encoded(seed)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_key_material_rejects_a_non_canonical_spelling_of_the_same_bytes() -> None:
    """A 43-character encoding has spare low bits; only one spelling is accepted."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    seed, _ = _synthetic_pair()
    canonical = _b64u(seed)
    non_canonical = canonical[:-1] + alphabet[alphabet.index(canonical[-1]) + 1]
    assert base64.urlsafe_b64decode(non_canonical + "=") == seed

    document = _signer_document(seed)
    document["private_key_b64u"] = non_canonical

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


@pytest.mark.parametrize(
    "stamp",
    [
        pytest.param("2026-05-01T12:00:00+00:00", id="offset-form"),
        pytest.param("2026-05-01T12:00:00.500Z", id="fractional-seconds"),
        pytest.param("2026-05-01T12:00:00", id="no-zone"),
        pytest.param("2026-05-01T12:00:00z", id="lowercase-zone"),
        pytest.param("2026-05-01 12:00:00Z", id="space-separator"),
        pytest.param("2026-05-01T12:00:00+01:00", id="non-utc-offset"),
        pytest.param("2026-13-01T12:00:00Z", id="impossible-month"),
        pytest.param("2026-02-30T12:00:00Z", id="impossible-day"),
        pytest.param("", id="empty"),
    ],
)
def test_timestamps_must_be_utc_rfc3339_seconds(stamp: str) -> None:
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    document["sign_from"] = stamp

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_timestamps_must_be_strings() -> None:
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    document["sign_from"] = 1777982400

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_retiring_signer_must_sign_before_its_bound() -> None:
    seed, _ = _synthetic_pair()
    document = _signer_document(seed, sign_from=_CUTOVER, sign_until=_CUTOVER)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_signer_document(_encode(document))


def test_retiring_signer_is_a_valid_nullable_state() -> None:
    """``sign_until`` is the only optional-valued field, and both states parse."""
    seed, _ = _synthetic_pair()
    document = _signer_document(seed, sign_from=_CUTOVER - timedelta(days=30), sign_until=_CUTOVER)

    signer = keys.parse_signer_document(_encode(document))

    assert signer.is_retiring
    assert signer.sign_until == _CUTOVER


# ---------------------------------------------------------------------------
# Keyring shape
# ---------------------------------------------------------------------------


def test_keyring_accepts_no_rotation_in_flight() -> None:
    _, public_key = _synthetic_pair()

    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(public_key)))
    )

    assert keyring.retiring is None
    assert keyring.entry_for("probe-2026-05a") is keyring.current
    assert keyring.entry_for("probe-2026-04a") is None


def test_keyring_accepts_exactly_one_retiring_entry() -> None:
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(current_key), [_retiring_entry(retiring_key)]))
    )

    assert keyring.retiring is not None
    assert keyring.retiring.is_retiring
    assert not keyring.current.is_retiring


def test_keyring_rejects_two_retiring_entries() -> None:
    """Two retiring keys would widen the accepted set past a single rotation."""
    _, current_key = _synthetic_pair()
    _, first = _synthetic_pair()
    _, second = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key),
                    [
                        _retiring_entry(first, kid="probe-2026-04a"),
                        _retiring_entry(second, kid="probe-2026-03a"),
                    ],
                )
            )
        )


def test_keyring_requires_the_retiring_field_to_be_an_array() -> None:
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key), retiring=_retiring_entry(retiring_key)
                )
            )
        )


def test_keyring_entries_must_use_distinct_key_ids() -> None:
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key, kid="probe-2026-05a"),
                    [_retiring_entry(retiring_key, kid="probe-2026-05a")],
                )
            )
        )


def test_keyring_entries_must_use_distinct_public_keys() -> None:
    """A reused public key would make the two entries' bounds meaningless."""
    _, shared = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(shared, kid="probe-2026-05a"),
                    [_retiring_entry(shared, kid="probe-2026-04a")],
                )
            )
        )


def test_keyring_cutover_instants_must_agree() -> None:
    """``current.sign_from`` is the same instant as ``retiring.sign_until``."""
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key, sign_from=_CUTOVER + timedelta(seconds=1)),
                    [_retiring_entry(retiring_key, sign_until=_CUTOVER)],
                )
            )
        )


@pytest.mark.parametrize(
    "overlap",
    [
        pytest.param(timedelta(seconds=70), id="minimum"),
        pytest.param(timedelta(seconds=180), id="mid-range"),
        pytest.param(timedelta(minutes=5), id="maximum"),
    ],
)
def test_retirement_overlap_within_bounds_is_accepted(overlap: timedelta) -> None:
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    keyring = keys.parse_verifier_keyring_document(
        _encode(
            _keyring_document(
                _current_entry(current_key),
                [_retiring_entry(retiring_key, overlap=overlap)],
            )
        )
    )

    assert keyring.retiring is not None
    assert keyring.retiring.accept_until == _CUTOVER + overlap


@pytest.mark.parametrize(
    "overlap",
    [
        pytest.param(timedelta(seconds=0), id="no-overlap"),
        pytest.param(timedelta(seconds=69), id="one-second-short"),
        pytest.param(timedelta(minutes=5, seconds=1), id="one-second-long"),
        pytest.param(timedelta(hours=1), id="far-too-long"),
        pytest.param(timedelta(seconds=-10), id="negative"),
    ],
)
def test_retirement_overlap_outside_bounds_is_rejected(overlap: timedelta) -> None:
    """Below 70s an in-flight capability could outlive its key; above 5m the old
    key stays live longer than the rotation contract allows."""
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key),
                    [_retiring_entry(retiring_key, overlap=overlap)],
                )
            )
        )


def test_retiring_entry_must_sign_before_its_bound() -> None:
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(
            _encode(
                _keyring_document(
                    _current_entry(current_key),
                    [_retiring_entry(retiring_key, sign_from=_CUTOVER, sign_until=_CUTOVER)],
                )
            )
        )


def test_keyring_entry_rejects_an_unknown_field() -> None:
    _, current_key = _synthetic_pair()
    entry = _current_entry(current_key)
    entry["private_key_b64u"] = _b64u(os.urandom(32))

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(_encode(_keyring_document(entry)))


def test_current_entry_rejects_retirement_bounds() -> None:
    """Only the retiring entry carries bounds; the current entry has none."""
    _, current_key = _synthetic_pair()
    entry = _current_entry(current_key)
    entry["sign_until"] = _stamp(_CUTOVER + timedelta(days=30))

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.parse_verifier_keyring_document(_encode(_keyring_document(entry)))


# ---------------------------------------------------------------------------
# Rotation semantics
# ---------------------------------------------------------------------------


def test_rotation_window_governs_issuance_and_acceptance_separately() -> None:
    """Issuance stops at cutover; acceptance continues through the overlap.

    That asymmetry is the whole point of the retiring entry: a capability
    issued a moment before cutover must still verify after it.
    """
    _, current_key = _synthetic_pair()
    _, retiring_key = _synthetic_pair()
    overlap = timedelta(seconds=120)
    keyring = keys.parse_verifier_keyring_document(
        _encode(
            _keyring_document(
                _current_entry(current_key),
                [_retiring_entry(retiring_key, overlap=overlap)],
            )
        )
    )
    assert keyring.retiring is not None
    old = keyring.retiring.kid
    new = keyring.current.kid

    before = _CUTOVER - timedelta(seconds=1)
    assert keyring.issuable_entry(old, before) is not None
    assert keyring.issuable_entry(new, before) is None

    # ``iat == sign_until`` is still issuable, with no extra skew allowance.
    assert keyring.issuable_entry(old, _CUTOVER) is not None
    assert keyring.issuable_entry(new, _CUTOVER) is not None

    just_after = _CUTOVER + timedelta(seconds=1)
    assert keyring.issuable_entry(old, just_after) is None
    assert keyring.acceptable_entry(old, just_after) is not None

    assert keyring.acceptable_entry(old, _CUTOVER + overlap) is not None
    assert keyring.acceptable_entry(old, _CUTOVER + overlap + timedelta(seconds=1)) is None
    assert keyring.acceptable_entry(new, _CUTOVER + overlap + timedelta(days=365)) is not None


def test_unknown_key_id_is_never_issuable_or_acceptable() -> None:
    _, current_key = _synthetic_pair()
    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(current_key)))
    )

    assert keyring.issuable_entry("probe-1999-01a", _CUTOVER) is None
    assert keyring.acceptable_entry("probe-1999-01a", _CUTOVER) is None


def test_signer_matches_the_current_verifier_entry() -> None:
    seed, public_key = _synthetic_pair()
    signer = keys.parse_signer_document(_encode(_signer_document(seed)))
    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(public_key)))
    )

    assert keys.match_signer_to_keyring(signer, keyring) is keyring.current


def test_signer_with_a_foreign_public_half_is_rejected() -> None:
    """The key id can agree while the key material does not; that fails closed."""
    seed, _ = _synthetic_pair()
    _, unrelated_key = _synthetic_pair()
    signer = keys.parse_signer_document(_encode(_signer_document(seed)))
    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(unrelated_key)))
    )

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.match_signer_to_keyring(signer, keyring)


def test_signer_with_a_disagreeing_sign_from_is_rejected() -> None:
    seed, public_key = _synthetic_pair()
    signer = keys.parse_signer_document(_encode(_signer_document(seed)))
    keyring = keys.parse_verifier_keyring_document(
        _encode(
            _keyring_document(_current_entry(public_key, sign_from=_CUTOVER + timedelta(seconds=1)))
        )
    )

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.match_signer_to_keyring(signer, keyring)


def test_retiring_signer_matches_the_retiring_entry() -> None:
    seed, retiring_key = _synthetic_pair()
    _, current_key = _synthetic_pair()
    signer = keys.parse_signer_document(
        _encode(
            _signer_document(
                seed,
                kid="probe-2026-04a",
                sign_from=_CUTOVER - timedelta(days=30),
                sign_until=_CUTOVER,
            )
        )
    )
    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(current_key), [_retiring_entry(retiring_key)]))
    )

    assert keys.match_signer_to_keyring(signer, keyring) is keyring.retiring


def test_retiring_signer_without_a_retiring_entry_is_rejected() -> None:
    """A half-applied rotation must not leave the signer silently unmatched."""
    seed, public_key = _synthetic_pair()
    signer = keys.parse_signer_document(
        _encode(
            _signer_document(seed, sign_from=_CUTOVER - timedelta(days=30), sign_until=_CUTOVER)
        )
    )
    keyring = keys.parse_verifier_keyring_document(
        _encode(_keyring_document(_current_entry(public_key)))
    )

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.match_signer_to_keyring(signer, keyring)


# ---------------------------------------------------------------------------
# Fixture-file properties
# ---------------------------------------------------------------------------


def test_signer_file_must_be_owner_read_only(matched_pair) -> None:
    """Mode 0400 is checked with ``fstat`` on the descriptor actually read.

    This proves a *file property* the deployment contract asks for.  It is not
    isolation from a child process running as the same identity: such a child
    can open the file no matter what its mode says.  Keeping the signer away
    from same-identity children is the launcher's job, not this check's.
    """
    signer_path, keyring_path, _ = matched_pair
    assert stat.S_IMODE(signer_path.stat().st_mode) == keys.SIGNER_FILE_MODE

    assert keys.load_signer(signer_path).sign_until is None
    assert keys.load_verifier_keyring(keyring_path).retiring is None


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o600, id="owner-writable"),
        pytest.param(0o440, id="group-readable"),
        pytest.param(0o444, id="world-readable"),
        pytest.param(0o404, id="world-readable-only"),
    ],
)
def test_signer_file_with_a_wider_mode_is_refused(matched_pair, mode: int) -> None:
    signer_path, _, _ = matched_pair
    signer_path.chmod(mode)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(signer_path)


def test_signer_file_may_not_be_a_symlink(matched_pair, tmp_path: Path) -> None:
    """``O_NOFOLLOW`` stops a swapped link from redirecting the read."""
    signer_path, _, _ = matched_pair
    link = tmp_path / "linked_signing_key"
    link.symlink_to(signer_path)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(link)


def test_signer_file_must_be_a_regular_file(tmp_path: Path) -> None:
    """A FIFO at the mount point is refused, and refused promptly.

    Opening a FIFO for reading blocks until a writer arrives, so this also
    pins the non-blocking open: without it a hostile or mis-provisioned mount
    would park startup on ``open`` instead of failing the regular-file check.
    """
    fifo = tmp_path / "signing_key_fifo"
    os.mkfifo(fifo, 0o400)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(fifo)


def test_signer_path_may_not_be_a_directory(tmp_path: Path) -> None:
    directory = tmp_path / "signing_key_dir"
    directory.mkdir(mode=0o400)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(directory)


def test_missing_signer_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(tmp_path / "absent")


def test_oversized_signer_file_is_refused(tmp_path: Path) -> None:
    """A bounded read keeps a hostile mount from driving memory use."""
    seed, _ = _synthetic_pair()
    document = _signer_document(seed)
    document["kid"] = "k"
    path = tmp_path / "signing_key"
    path.write_bytes(_encode(document) + b" " * 9000)
    path.chmod(keys.SIGNER_FILE_MODE)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_signer(path)


def test_keyring_file_is_shared_and_need_not_be_owner_only(matched_pair) -> None:
    """The keyring holds no secret, so a root-owned world-readable mount is fine."""
    _, keyring_path, _ = matched_pair
    keyring_path.chmod(0o444)

    assert keys.load_verifier_keyring(keyring_path).current.kid == "probe-2026-05a"


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o464, id="group-writable"),
        pytest.param(0o446, id="world-writable"),
    ],
)
def test_writable_keyring_file_is_refused(matched_pair, mode: int) -> None:
    """Non-secret still means non-tamperable: anyone who can write it chooses
    which public keys Switchboard will accept."""
    _, keyring_path, _ = matched_pair
    keyring_path.chmod(mode)

    with pytest.raises(keys.RuntimeProbeControlKeyError):
        keys.load_verifier_keyring(keyring_path)


# ---------------------------------------------------------------------------
# Startup snapshots
# ---------------------------------------------------------------------------


def test_signer_snapshot_freezes_a_matched_pair(matched_pair) -> None:
    signer_path, keyring_path, _ = matched_pair

    snapshot = keys.read_signer_snapshot(signer_path=signer_path, keyring_path=keyring_path)

    assert snapshot.available
    assert snapshot.unavailable_reason is None
    assert snapshot.matched is not None
    assert snapshot.may_issue_at(_CUTOVER)
    assert not snapshot.may_issue_at(_CUTOVER - timedelta(seconds=1))


def test_signer_snapshot_is_immutable(matched_pair) -> None:
    signer_path, keyring_path, _ = matched_pair
    snapshot = keys.read_signer_snapshot(signer_path=signer_path, keyring_path=keyring_path)

    with pytest.raises(FrozenInstanceError):
        snapshot.unavailable_reason = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.matched.sign_from = _CUTOVER  # type: ignore[union-attr]


def test_signer_snapshot_fails_closed_without_raising(tmp_path: Path) -> None:
    """A missing mount must not take unrelated startup down with it."""
    snapshot = keys.read_signer_snapshot(
        signer_path=tmp_path / "absent", keyring_path=tmp_path / "also-absent"
    )

    assert not snapshot.available
    assert snapshot.signer is None
    assert snapshot.matched is None
    assert not snapshot.may_issue_at(_CUTOVER)


def test_signer_snapshot_fails_closed_on_a_mismatched_pair(tmp_path: Path) -> None:
    seed, _ = _synthetic_pair()
    _, unrelated_key = _synthetic_pair()
    signer_path = _write_document(
        tmp_path / "signing_key", _signer_document(seed), mode=keys.SIGNER_FILE_MODE
    )
    keyring_path = _write_document(
        tmp_path / "verifiers", _keyring_document(_current_entry(unrelated_key)), mode=0o444
    )

    snapshot = keys.read_signer_snapshot(signer_path=signer_path, keyring_path=keyring_path)

    assert not snapshot.available
    assert snapshot.signer is None


def test_verifier_snapshot_backs_a_probe_without_the_private_half(matched_pair) -> None:
    _, keyring_path, _ = matched_pair

    snapshot = keys.read_verifier_snapshot(keyring_path=keyring_path)

    assert snapshot.available
    assert snapshot.is_ready_for("probe-2026-05a", _CUTOVER)
    assert not snapshot.is_ready_for("probe-2026-05a", _CUTOVER - timedelta(seconds=1))
    assert not snapshot.is_ready_for("probe-1999-01a", _CUTOVER)
    assert not hasattr(snapshot, "signer")


def test_verifier_snapshot_reports_unready_when_the_keyring_is_absent(tmp_path: Path) -> None:
    snapshot = keys.read_verifier_snapshot(keyring_path=tmp_path / "absent")

    assert not snapshot.available
    assert not snapshot.is_ready_for("probe-2026-05a", _CUTOVER)


def test_process_snapshot_reloads_only_on_restart(matched_pair, monkeypatch) -> None:
    """Replacing the file under a running process changes nothing.

    Rotation is deliberately restart-driven and readiness-gated, so the cached
    snapshot must survive a mid-life file swap; a fresh process picks the new
    keyring up.
    """
    signer_path, keyring_path, _ = matched_pair
    monkeypatch.setattr(keys, "SIGNER_PATH", signer_path)
    monkeypatch.setattr(keys, "VERIFIER_KEYRING_PATH", keyring_path)

    first = keys.signer_snapshot()
    assert first.available
    assert keys.verifier_snapshot().is_ready_for("probe-2026-05a", _CUTOVER)

    rotated_seed, rotated_key = _synthetic_pair()
    _write_document(
        signer_path,
        _signer_document(rotated_seed, kid="probe-2026-06a"),
        mode=keys.SIGNER_FILE_MODE,
    )
    _write_document(
        keyring_path,
        _keyring_document(_current_entry(rotated_key, kid="probe-2026-06a")),
        mode=0o444,
    )

    assert keys.signer_snapshot() is first
    assert not keys.verifier_snapshot().is_ready_for("probe-2026-06a", _CUTOVER)

    keys._reset_snapshots_for_tests()  # stands in for the process restart

    assert keys.signer_snapshot() is not first
    assert keys.verifier_snapshot().is_ready_for("probe-2026-06a", _CUTOVER)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_representations_disclose_no_key_material(matched_pair) -> None:
    """Asserted as absence: the material itself is never written down here."""
    signer_path, keyring_path, seed = matched_pair
    snapshot = keys.read_signer_snapshot(signer_path=signer_path, keyring_path=keyring_path)
    assert snapshot.signer is not None

    spellings = (_b64u(seed), seed.hex(), str(seed), _b64u(snapshot.signer.public_key))
    rendered = " ".join(
        (
            repr(snapshot),
            str(snapshot),
            repr(snapshot.signer),
            repr(snapshot.keyring),
            repr(snapshot.matched),
        )
    )

    for spelling in spellings:
        assert spelling not in rendered


def test_parse_failures_never_echo_the_offending_material() -> None:
    """Error strings reach logs and operator UIs, so they stay value-free."""
    seed, _ = _synthetic_pair()
    document = _signer_document(seed, kid="probe 2026 invalid")
    document["private_key_b64u"] = _b64u(seed) + "!"

    with pytest.raises(keys.RuntimeProbeControlKeyError) as raised:
        keys.parse_signer_document(_encode(document))

    message = str(raised.value)
    assert _b64u(seed) not in message
    assert seed.hex() not in message
    assert "probe 2026 invalid" not in message


def test_unavailable_signer_logs_without_key_material(tmp_path, caplog) -> None:
    seed, _ = _synthetic_pair()
    _, unrelated_key = _synthetic_pair()
    signer_path = _write_document(
        tmp_path / "signing_key", _signer_document(seed), mode=keys.SIGNER_FILE_MODE
    )
    keyring_path = _write_document(
        tmp_path / "verifiers", _keyring_document(_current_entry(unrelated_key)), mode=0o444
    )

    with caplog.at_level(logging.WARNING):
        keys.read_signer_snapshot(signer_path=signer_path, keyring_path=keyring_path)

    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert emitted
    assert _b64u(seed) not in emitted
    assert seed.hex() not in emitted
    assert _b64u(unrelated_key) not in emitted


def test_reserved_secret_name_is_declared_for_the_generic_store() -> None:
    """The generic Secrets surface excludes this name; see the secrets tests."""
    assert keys.RESERVED_SIGNING_KEY_SECRET_NAME == "RUNTIME_PROBE_CONTROL_SIGNING_KEY"
    assert keys.CONTROL_AUDIENCE == "switchboard.runtime_probe_control.v1"
