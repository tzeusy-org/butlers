"""UNEXECUTED closed-sidecar validator. No raw log or payload parser."""
from pathlib import Path
import hashlib
import json
import sys

def closed_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate closed field")
        obj[key] = value
    return obj

raw, published = map(Path, sys.argv[1:3])
code = int(sys.argv[3])
published.mkdir(mode=0o700, exist_ok=False)
allowed = {
    0: set(), 1: {"c"}, 2: {"n"}, 3: {"n", "p", "o", "x"},
    4: {"n", "p"}, 5: {"n", "s", "v"}, 6: {"n", "r"},
    7: {"n", "f", "p", "x"}, 8: {"x", "c", "a", "f"}, 9: set(),
}
limits = {"n": (0, 5), "c": (0, 6), "p": (0, 45), "o": (0, 2),
          "x": (0, 6), "s": (0, 4), "v": (0, 4), "r": (0, 1),
          "f": (0, 2), "a": (0, 1)}
points = set(range(1, 17)) | set(range(20, 32)) | set(range(40, 46))
traces = []
process_records = []
unknown = []
files = sorted(raw.glob("*.jsonl"))
if not 1 <= len(files) <= 16:
    unknown.append("file_count")
for f in files:
    if f.name != "controller.jsonl" and not (f.stem.startswith("w") and f.stem[1:].isdigit() and 0 <= int(f.stem[1:]) <= 14):
        unknown.append("filename")
        continue
    b = f.read_bytes()
    if len(b) > 24576 or not b.endswith(b"\n"):
        unknown.append("bytes_or_torn")
        continue
    lines = b.splitlines()
    if not 1 <= len(lines) <= 160:
        unknown.append("record_count")
        continue
    records = []
    try:
        previous = -1
        for line in lines:
            r = json.loads(line, object_pairs_hook=closed_object)
            assert isinstance(r, dict) and type(r.get("e")) is int and r["e"] in allowed
            event = r["e"]
            if event == 9:
                assert r == {"e": 9}
                unknown.append("observer_cap")
            else:
                assert set(r) <= {"e", "ms"} | allowed[event]
                assert type(r.get("ms")) is int and 0 <= r["ms"] and previous <= r["ms"] < 24 * 60 * 60 * 1000
                previous = r["ms"]
                for k, v in r.items():
                    if k in {"e", "ms"}:
                        continue
                    if k == "n" and v is None and event == 7:
                        continue
                    assert type(v) is int and limits[k][0] <= v <= limits[k][1]
                required = {
                    0: {"e", "ms"}, 1: {"e", "ms", "c"},
                    2: {"e", "ms", "n"}, 3: {"e", "ms", "n", "p", "o", "x"},
                    4: {"e", "ms", "n", "p"}, 5: {"e", "ms", "n", "s", "v"},
                    6: {"e", "ms", "n", "r"},
                    7: {"e", "ms", "n", "f", "p"} | ({"x"} if r.get("p") == 1 else set()),
                    8: {"e", "ms", "x", "c", "a", "f"},
                }
                assert set(r) == required[event]
                if event == 1:
                    assert r["c"] == 6
                if event == 7:
                    assert r["p"] <= 1 and (r["p"] == 0 or r["x"] <= 1)
                if event == 8:
                    assert r["c"] <= 1 and r["f"] <= 1
                if event == 5 and (r["s"] == 4 or r["v"] == 4):
                    unknown.append("other_return_category")
                if event == 4:
                    assert set(r) == {"e", "ms", "n", "p"} and r["n"] == 0 and r["p"] in points
                elif event == 3:
                    assert set(r) == {"e", "ms", "n", "p", "o", "x"} and r["p"] <= 2 and r["x"] <= 3
                elif event == 5:
                    assert set(r) == {"e", "ms", "n", "s", "v"} and r["n"] == 0
            records.append(r)
        assert records[0]["e"] == 0
    except Exception:
        unknown.append("schema_or_order")
        continue
    if records[-1]["e"] != 8:
        unknown.append("missing_footer")
    if any(r["e"] == 8 and r.get("f") for r in records):
        unknown.append("observer_fault")
    # Only closed records qualify for artifact publication.
    (published / f.name).write_bytes(b)
    process_records.append(records)
    traces.append({"slot": len(traces), "sha256": hashlib.sha256(b).hexdigest(), "records": len(records)})
def qualify_process(records):
    """Correlate only events from this closed process and target protocol.

    Point numbers are identifiers, not a sequence; no complete point inventory
    is required. Partial/failed setup and interrupted protocols stay UNKNOWN.
    """
    issues = []
    counts = {event: sum(r["e"] == event for r in records) for event in (0, 1, 6, 8)}
    if counts[0] != 1 or records[0]["e"] != 0:
        issues.append("configure_multiplicity_or_position")
    if counts[1] > 1:
        issues.append("collection_multiplicity")
    if counts[8] != 1 or records[-1]["e"] != 8:
        issues.append("footer_multiplicity_or_position")
    if counts[6] > 1:
        issues.append("restoration_multiplicity")
    active = None
    footer_seen = False
    collection_seen = False
    protocols = []
    phases = {}
    source_points = set()
    held_returns = 0
    restoration = False
    for index, r in enumerate(records):
        event = r["e"]
        if footer_seen:
            issues.append("record_after_footer")
        if event == 0:
            if index != 0:
                issues.append("configure_multiplicity_or_position")
        elif event == 1:
            if protocols:
                issues.append("collection_after_protocol")
            collection_seen = True
        elif event == 2:
            if active is not None:
                issues.append("unfinished_protocol")
            if not collection_seen:
                issues.append("protocol_without_collection")
            protocols.append(r["n"])
            if protocols.count(r["n"]) > 1:
                issues.append("duplicate_protocol")
            active = r["n"]
        elif event == 3:
            if active != r["n"]:
                issues.append("phase_outside_protocol")
            seen = phases.setdefault(r["n"], [])
            if r["p"] in seen or (seen and r["p"] < seen[-1]):
                issues.append("duplicate_or_reversed_case_phase")
            seen.append(r["p"])
            # The producer restores the target tracer after teardown report.
            if r["p"] == 2 and r["n"] != 0:
                active = None
        elif event in (4, 5, 6):
            if active != 0 or protocols.count(0) != 1 or restoration:
                issues.append("target_witness_outside_target_protocol")
            if event == 4:
                if r["p"] in source_points:
                    issues.append("duplicate_source_point")
                source_points.add(r["p"])
            elif event == 5:
                held_returns += 1
                if held_returns > 1:
                    issues.append("held_return_multiplicity")
                if 1 not in source_points:
                    issues.append("held_return_without_target_activation")
            else:
                if r["r"] != 1:
                    issues.append("trace_restoration_unobserved")
                if phases.get(0) != [0, 1, 2]:
                    issues.append("target_phase_population_incomplete")
                restoration = r["r"] == 1
                active = None
        elif event == 7:
            if r["n"] != active:
                issues.append("fixture_outside_protocol")
        elif event == 8:
            footer_seen = True
            if r["c"] != int(collection_seen) or r["a"] != int(bool(source_points)):
                issues.append("contradictory_footer_flags")
            if r["f"]:
                issues.append("observer_fault")
            if active is not None:
                issues.append("unfinished_protocol")
    target_count = protocols.count(0)
    if target_count:
        if 1 not in source_points:
            issues.append("target_not_entered")
        if held_returns != 1:
            issues.append("held_return_unobserved_or_ambiguous")
        if not restoration:
            issues.append("trace_restoration_unobserved")
    elif source_points or held_returns or counts[6]:
        issues.append("target_witness_without_target_protocol")
    qualified = (not issues and target_count == 1 and counts[1] == 1
                 and 1 in source_points and held_returns == 1 and restoration)
    return {"issues": issues, "protocols": protocols,
            "target_count": target_count, "qualified": qualified}


all_records = [r for records in process_records for r in records]
lifecycles = [qualify_process(records) for records in process_records]
for lifecycle in lifecycles:
    unknown.extend(lifecycle["issues"])
qualified_targets = [i for i, lifecycle in enumerate(lifecycles) if lifecycle["qualified"]]
activations = sum(lifecycle["target_count"] for lifecycle in lifecycles)
if activations != 1:
    unknown.append("target_invocation_count")
if len(qualified_targets) != 1:
    unknown.append("target_lifecycle_unqualified")
# Qualification cannot be assembled from any-record booleans in other processes.
activation = restoration = len(qualified_targets) == 1
collection = any(lifecycle["qualified"] for lifecycle in lifecycles)
if not activation:
    unknown.append("target_not_entered")
if not collection:
    unknown.append("six_owner_collection_not_observed")
if not restoration:
    unknown.append("trace_restoration_unobserved")
all_protocols = [n for lifecycle in lifecycles for n in lifecycle["protocols"]]
if any(all_protocols.count(n) != 1 for n in range(6)):
    unknown.append("owner_protocol_population_incomplete_or_duplicate")
results = {}
for r in all_records:
    if r["e"] != 3:
        continue
    key = (r["n"], r["p"])
    if key in results:
        unknown.append("duplicate_case_phase")
    else:
        results[key] = r["o"]
if set(results) != {(n, p) for n in range(6) for p in range(3)}:
    unknown.append("owner_phase_population_incomplete")
# Cleanup is a distinct post-owner observation, never inferred from finally.
# Only the fixture's cached 64-hex ID is queried; no list, prune, stop, removal,
# database connection or unrelated-container inspection. No ID/attrs/error text
# is exported. API discovery and exact-ID inspect are bounded by fixed Unix-socket timeout5s
# and the launcher's outer TERM20/KILL5s validator watchdog.
cleanup = {"fixture_container": "UNKNOWN", "owned_count": 0,
           "ownership_private_files_removed": False,
           "not_a_graceful_teardown_claim": True}
owned = sorted(raw.glob("owned-*.id"))
try:
    if len(owned) != 1:
        unknown.append("ownership_capture_missing_or_multiple")
    else:
        f = owned[0]
        assert f.name in {"owned-controller.id"} | {"owned-w" + str(i) + ".id" for i in range(15)}
        assert not f.is_symlink() and f.stat().st_size == 64
        identifier = f.read_text(encoding="ascii")
        assert len(identifier) == 64 and all(c in "0123456789abcdef" for c in identifier)
        cleanup["owned_count"] = 1
        import http.client
        import re
        import socket

        class OwnedDockerConnection(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect("/var/run/docker.sock")

        client = None
        try:
            # Fixed hosted socket only: no environment, config/credential
            # discovery or extra resource listing. Version response is capped,
            # read internally, and only its syntactically closed API version is used.
            client = OwnedDockerConnection("localhost", timeout=5)
            client.request("GET", "/version")
            response = client.getresponse()
            assert response.status == 200
            body = response.read(16385)
            assert len(body) <= 16384
            version = json.loads(body)["ApiVersion"]
            assert type(version) is str and re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,3}", version)
            client.close()
            client = OwnedDockerConnection("localhost", timeout=5)
            client.request("GET", "/v" + version + "/containers/" + identifier + "/json")
            response = client.getresponse()
            # Do not read/export inspect response bodies, paths, attrs or errors.
            if response.status == 404:
                cleanup["fixture_container"] = "CONFIRMED_ABSENT"
            elif response.status == 200:
                cleanup["fixture_container"] = "PRESENT"
                unknown.append("owned_container_present")
            else:
                unknown.append("owned_container_inspect_unavailable")
        except Exception:
            unknown.append("owned_container_inspect_unavailable")
        finally:
            if client is not None:
                client.close()
except Exception:
    unknown.append("ownership_capture_invalid")
finally:
    for f in owned:
        try:
            f.unlink()
        except OSError:
            pass
    cleanup["ownership_private_files_removed"] = not any(raw.glob("owned-*.id"))
    if not cleanup["ownership_private_files_removed"]:
        unknown.append("private_ownership_cleanup_unproven")
if cleanup["fixture_container"] == "UNKNOWN":
    unknown.append("fixture_container_cleanup_unknown")
receipt = {"scope": "DIAGNOSTIC_ONLY", "observational_pass": False,
           "subject_exit": code, "observation_complete": not unknown, "cleanup": cleanup,
           "unknown_reasons": sorted(set(unknown)), "target_activated": activation,
           "six_case_collection_observed": collection, "trace_restoration_observed": restoration,
           "qualified_target_process_slots": qualified_targets,
           "authoritative_case_phase_counts": {str(i): {str(p): results.get((i, p), "UNKNOWN_UNOBSERVED") for p in range(3)} for i in range(6)},
           "source_mutations": 0, "traces": traces,
           "natural_failure_erased": False, "ordinary_PG_PASS_claimed": False}
(published / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
for f in published.iterdir():
    f.chmod(0o600)
# Validator exit only attests artifact handling; it does not replace subject exit.
