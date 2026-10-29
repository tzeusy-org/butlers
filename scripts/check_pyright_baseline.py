import hashlib
import json
from collections import Counter

from pydantic_core import SchemaValidator
from pydantic_core import core_schema as schema


def fields(**members):
    items = {k: schema.typed_dict_field(v, required=k != "rule") for k, v in members.items()}
    return schema.typed_dict_schema(items, extra_behavior="forbid", strict=True)


text = schema.str_schema(strict=True)
count = schema.int_schema(strict=True, ge=0)
rule = schema.nullable_schema(schema.str_schema(strict=True, min_length=1))
point = fields(line=count, character=count)
span = fields(start=point, end=point)
severity = schema.literal_schema(["error", "warning", "information"])
checker = schema.literal_schema(["1.1.414"])
diagnostic = fields(file=text, message=text, range=span, severity=severity, rule=rule)
finite = schema.float_schema(strict=True, allow_inf_nan=False, ge=0)
summary_names = "filesAnalyzed errorCount warningCount informationCount".split()
summary = fields(**dict.fromkeys(summary_names, count), timeInSec=finite)
report_shape = fields(
    version=checker, time=text, generalDiagnostics=schema.list_schema(diagnostic), summary=summary
)
positive = schema.int_schema(strict=True, gt=0)
digest = schema.str_schema(strict=True, pattern=r"^[0-9a-f]{64}$")
# Canonical relative src/roster paths: no empty, dot, parent, absolute or trailing component.
file_pattern = r"^(?!.*(?:^|/)\.\.?(?:/|$))(src|roster)/(?:[^/]+/)*[^/]+$"
file_schema = schema.str_schema(strict=True, pattern=file_pattern, regex_engine="python-re")
key_shape = schema.tuple_positional_schema([file_schema, rule, digest])
entries = schema.dict_schema(text, positive)
one = schema.int_schema(strict=True, ge=1, le=1)
baseline_shape = fields(format=one, checker=checker, errors=entries)


def decode(raw, shape):
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate-json-key")
        return result

    return SchemaValidator(shape).validate_python(
        json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    )


def errors(raw, root, code):
    report = decode(raw, report_shape)
    counts = Counter(d["severity"] for d in report["generalDiagnostics"])
    if code not in {0, 1} or code != int(bool(counts["error"])):
        raise ValueError("checker-exit")
    totals = {key + "Count": counts[key] for key in ("error", "warning", "information")}
    if any(report["summary"][key] != value for key, value in totals.items()):
        raise ValueError("checker-summary")
    result = Counter()
    for d in report["generalDiagnostics"]:
        file = root.joinpath(d["file"]).resolve(strict=True).relative_to(root.resolve()).as_posix()
        if d["severity"] == "error":
            message = hashlib.sha256(d["message"].encode()).hexdigest()
            key = SchemaValidator(key_shape).validate_python([file, d.get("rule"), message])
            result[json.dumps(key)] += 1
    return result


def baseline(raw):
    result = decode(raw, baseline_shape)["errors"]
    for key in result:
        if key != json.dumps(decode(key.encode(), key_shape)):
            raise ValueError("noncanonical-baseline")
    return Counter(result)


def compare(current, committed, trusted):
    if current != committed or committed - trusted:
        raise ValueError("baseline-stale-or-growth")
