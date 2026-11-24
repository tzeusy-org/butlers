"""Golden vectors for the D7 ``args_digest``, shared by broker and domain verifiers."""

import hashlib

import pytest

from butlers.core.capability_json import (
    InvalidArguments,
    arguments_digest,
    canonical_bytes,
    golden_vectors,
    parse_json_object,
)

pytestmark = pytest.mark.unit

# RFC 8785 section 3.2.2 publishes this exact output; it pins the vendored
# encoder to the standard independently of our own generated vectors.
_RFC_8785_OUTPUT = (
    '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
    '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
)


@pytest.mark.parametrize("vector", golden_vectors(), ids=lambda v: v["name"])
def test_golden_vector(vector):
    if "refused" in vector:
        with pytest.raises(InvalidArguments, match=vector["refused"]):
            parse_json_object(vector["raw"].encode())
        return
    parsed = parse_json_object(vector["raw"].encode())
    canonical = canonical_bytes(parsed)
    assert canonical.decode() == vector["canonical"]
    assert hashlib.sha256(canonical).hexdigest() == vector["digest"] == arguments_digest(parsed)
    if vector["name"] == "rfc8785_section_3_2_2":
        assert vector["canonical"] == _RFC_8785_OUTPUT
