"""Response DLP and canary detection (RFC 0039 D15).

Every text field is NFKC-normalized and stripped of Cc and Cf characters (tab and newline are
kept and treated as separators), then scanned. Credential and identifier matches are counted;
personal-contact matches are redacted but not counted. Finance and health results (OD17) skip
the identifier and contact rules, need a card word near a Luhn run, and skip the entropy and
URL path-segment rules on the columns and JSON paths the manifest marks ``dlp = "identifier"``.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from butlers.knowledge_read.core import tables_for
from butlers.knowledge_read.manifest import Table
from butlers.knowledge_read.records import JSON_TABLE, record_kinds

SHARED_DOMAINS = frozenset({"finance", "health"})
MAX_COUNTED = 5
# Host- and reader-generated fields outside published data: deep links, cursors, envelopes.
# Structural assumption: these key names are written only by the readers themselves (cursor,
# source envelope, deep link, freshness), never copied from a published column. A projection
# column that reached a result under one of these names would skip the scan, so a manifest or
# reader change that lets published data use them must drop the name from this set.
_SERVER_KEYS = frozenset({"next_cursor", "source", "url", "uri", "as_of"})
_NUMERIC_KEYS = frozenset({"amount", "total", "value", "balance", "minutes", "count", "score"})
_NUMERIC_TYPES = ("numeric", "integer", "bigint", "smallint", "double precision", "real")
_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_KEEP = {"\n", "\t"}

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]{1,40}-----.*?(?:-----END [A-Z0-9 ]{1,40}-----|\Z)", re.S)
_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{1,15}://[^\s<>\"'`]+")
_TOKENS = (
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)
_LUHN_RUN = re.compile(r"(?<!\d)\d(?:[ \t\n-]?\d){12,18}(?!\d)")
_CARD_WORD = re.compile(r"(?i)\b(?:card|visa|mastercard|amex|credit|debit|cvv|expiry)\b")
_OTP = re.compile(
    r"(?i:\b(?:otp|one[- ]time (?:password|passcode|code|pin)|verification code|security code"
    r"|login code|sign[- ]in code|auth(?:entication)? code|reset code|password reset|2fa|mfa"
    r"|passcode)\b)\D{0,24}?(?<!\d)(\d{4,8})(?!\d)"
)
_ENTROPY = re.compile(r"[A-Za-z0-9+_=-]{20,}")
_SECRET_PARAMS = frozenset(
    {"token", "code", "sig", "signature", "key", "pwd", "passcode", "password", "credential"}
    | {"auth"}
)
_PARAM_SPLIT = re.compile(r"[-_]|(?<=[a-z0-9])(?=[A-Z])")
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,}\Z")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
_PNR = re.compile(
    r"(?i:\b(?:pnr|confirmation|booking|reservation|locator)(?:\s+(?:ref(?:erence)?|code"
    r"|number|no\.?))?)[\s:#-]{1,4}([A-Z0-9]{6})\b"
)
_DOCUMENT = re.compile(
    r"(?i:\b(?:passport|national id|nric|id number|id no\.?|identity card|ssn"
    r"|social security(?: number)?|driver'?s licen[cs]e)(?:\s*(?:no\.?|number|#))?)"
    r"[\s:#-]{1,4}([A-Z0-9][A-Z0-9-]{5,14})\b"
)
_ACCOUNT = re.compile(
    r"(?i:\b(?:account|acct|a/c|routing|sort code|bsb)(?:\s*(?:no\.?|number|#))?)"
    r"[\s:#-]{1,4}(\d[\d -]{6,22}\d)(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w+])(?:\+[1-9](?:[ .-]?\d){6,14}|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}"
    r"|[689]\d{3}[ -]\d{4}|0\d{1,4}[ .-]\d{3,4}[ .-]\d{3,4})(?!\d)"
)


def normalize(text: str) -> str:
    """NFKC, then drop Cc and Cf characters except tab and newline."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text)
        if c in _KEEP or unicodedata.category(c) not in ("Cc", "Cf")
    )


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _iban(candidate: str) -> bool:
    code = candidate.replace(" ", "")
    if not 15 <= len(code) <= 34:
        return False
    moved = code[4:] + code[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _entropy(token: str) -> float:
    counts = Counter(token)
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())


def _tokenish(token: str) -> bool:
    return (
        any(c.isdigit() for c in token)
        and any(c.isalpha() for c in token)
        and not _UUID.match(token)
    )


@dataclass
class Scan:
    """One result's scan: ``counted`` drives the drop threshold; ``rules`` feeds metrics."""

    shared: bool
    counted: int = 0
    rules: Counter[str] = field(default_factory=Counter)
    luhn_uncontexted: int = 0

    def _hit(self, rule: str, *, counted: bool = True) -> str:
        self.rules[rule] += 1
        self.counted += counted
        return f"[redacted:{rule}]"

    def _sub(
        self, pattern: re.Pattern[str], text: str, keep: Callable[[re.Match[str]], str | None]
    ) -> str:
        """Replace each match (or its group 1) for which ``keep`` returns a rule name."""

        def replace(match: re.Match[str]) -> str:
            rule = keep(match)
            if rule is None:
                return match.group(0)
            if match.re.groups:
                start, end = match.span(1)
                whole = match.group(0)
                offset = match.start()
                return (
                    whole[: start - offset]
                    + self._hit(rule, counted=rule not in ("email", "phone"))
                    + whole[end - offset :]
                )
            return self._hit(rule, counted=rule not in ("email", "phone"))

        return pattern.sub(replace, text)

    def text(self, value: str, *, marked: bool = False) -> str:
        text = normalize(value)
        text = self._sub(_PEM, text, lambda m: "pem")
        text = self._sub(_URL, text, lambda m: _url_rule(m.group(0), marked))
        for name, pattern in _TOKENS:
            text = self._sub(pattern, text, lambda m, name=name: name)
        text = self._sub(_LUHN_RUN, text, lambda m: self._luhn_rule(m))
        text = self._sub(_OTP, text, lambda m: "otp")
        if not marked:
            text = self._sub(
                _ENTROPY,
                text,
                lambda m: (
                    "entropy" if _tokenish(m.group(0)) and _entropy(m.group(0)) >= 3.5 else None
                ),
            )
        if not self.shared:
            text = self._sub(_IBAN, text, lambda m: "iban" if _iban(m.group(0)) else None)
            text = self._sub(_PNR, text, lambda m: "pnr")
            text = self._sub(
                _DOCUMENT,
                text,
                lambda m: "document" if any(c.isdigit() for c in m.group(1)) else None,
            )
            text = self._sub(
                _ACCOUNT,
                text,
                lambda m: "account" if 8 <= sum(c.isdigit() for c in m.group(1)) <= 17 else None,
            )
            text = self._sub(_EMAIL, text, lambda m: "email")
            text = self._sub(_PHONE, text, lambda m: "phone")
        return text

    def _luhn_rule(self, match: re.Match[str]) -> str | None:
        digits = re.sub(r"\D", "", match.group(0))
        if not (13 <= len(digits) <= 19 and _luhn(digits)):
            return None
        if self.shared:
            window = match.string[max(0, match.start() - 32) : match.end() + 32]
            if not _CARD_WORD.search(window):
                self.luhn_uncontexted += 1
                return None
        return "luhn"


def _url_rule(url: str, marked: bool) -> str | None:
    try:
        parts = urlsplit(url)
        params = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return None
    userinfo = parts.netloc.rpartition("@")[0]
    if ":" in userinfo:
        return "url_userinfo"
    for name, _ in params:
        if {s.lower() for s in _PARAM_SPLIT.split(name)} & _SECRET_PARAMS:
            return "url_secret"
    if not marked and any(_PATH_TOKEN.match(s) and _tokenish(s) for s in parts.path.split("/")):
        return "url_path"
    return None


@cache
def _column_marks(tool: str) -> frozenset[str]:
    return frozenset(c for t in tables_for(tool) for c in t.identifiers)


@cache
def _table(source: str) -> Table | None:
    domain = source.split(".", 1)[0]
    for tool in ("finance_records", "health_records", "health_profile"):
        for table in tables_for(tool):
            if table.source == source and table.domain == domain:
                return table
    return None


def _numeric(key: str, value: str, types: dict[str, str] | None = None) -> bool:
    if not _DECIMAL.match(value):
        return False
    if types is not None:
        return types.get(key, "").startswith(_NUMERIC_TYPES)
    return key in _NUMERIC_KEYS


def scan_result(tool: str, domain: str | None, result: Any) -> tuple[Any, Scan]:
    """Redact one tool result; returns the redacted copy and its scan record."""
    scan = Scan(shared=domain in SHARED_DOMAINS)
    marks = _column_marks(tool) if scan.shared else frozenset()

    def value(node: Any, *, marked: bool = False, data: bool = False) -> Any:
        """``data``: inside published values, where no key is server-generated."""
        if isinstance(node, str):
            return scan.text(node, marked=marked)
        if isinstance(node, list):
            return [value(x, data=data) for x in node]
        if isinstance(node, dict):
            if data:
                return {k: value(v, data=True) for k, v in node.items()}
            return obj(node)
        return node

    def published(mapping: dict, table: Table | None, *, json_paths: bool) -> dict:
        types = dict(table.types) if table and not json_paths else {}
        marked = (
            set(table.json_identifiers if json_paths else table.identifiers) if table else set()
        )
        out = {}
        for key, item in mapping.items():
            if isinstance(item, str) and types and _numeric(key, item, types):
                out[key] = item
            elif key == "value_text" and table and table.source.endswith(JSON_TABLE):
                origin = _table(str(mapping.get("source_table", "")))
                path_marked = origin is not None and mapping.get("path") in set(
                    origin.json_identifiers
                )
                out[key] = value(item, marked=scan.shared and path_marked, data=True)
            else:
                out[key] = value(item, marked=scan.shared and key in marked, data=True)
        return out

    def obj(node: dict) -> dict:
        records = tool.endswith("_records") and isinstance(node.get("kind"), str)
        table = record_kinds(domain or "", tool).get(node["kind"]) if records else None
        out = {}
        for key, item in node.items():
            if key in _SERVER_KEYS:
                out[key] = item
            elif isinstance(item, dict) and records and key == "fields":
                out[key] = published(item, table, json_paths=False)
            elif isinstance(item, dict) and records and key == "json_fields":
                out[key] = published(item, table, json_paths=True)
            elif isinstance(item, dict) and tool == "health_profile" and key == "fields":
                out[key] = published(item, _table("health.facts"), json_paths=True)
            elif isinstance(item, str) and _numeric(key, item):
                out[key] = item
            else:
                out[key] = value(item, marked=key in marks)
        return out

    return value(result), scan


def find_canary(result: Any, canaries: tuple[str, ...]) -> bool:
    """Whether any string or key in ``result`` holds a canary, raw or after normalization."""
    if not canaries:
        return False
    stack = [result]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            folded = "".join(
                c
                for c in unicodedata.normalize("NFKC", node)
                if unicodedata.category(c) not in ("Cc", "Cf")
            )
            if any(c in node or c in folded for c in canaries):
                return True
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False
