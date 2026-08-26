#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Read-only HTTPS readiness probe for a Tailscale Serve route.

The launcher uses this module only through an explicitly selected probe
executor.  The transport is injectable so the result classification can be
tested without contacting a tailnet or changing Serve state.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

_MAX_RESPONSE_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_RETRIES = 2
_DEFAULT_RETRY_DELAY_SECONDS = 1.0
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_ATTEMPTS = 4
_MAX_RETRY_DELAY_SECONDS = 5.0
_IDENTITY_TIMEOUT_SECONDS = 5.0


class ProbeOutcome(StrEnum):
    """Sanitized outcome classes surfaced to an operator."""

    OK = "ok"
    CERT_INVALID = "cert-invalid"
    ROUTE_404 = "route-404"
    TIMEOUT = "timeout"
    BODY_INVALID = "body-invalid"
    HTTP_ERROR = "http-error"
    CONNECTION_ERROR = "connection-error"
    INVALID_URL = "invalid-url"


@dataclass(frozen=True)
class ProbeResponse:
    """The bounded response fields needed for readiness validation."""

    status: int
    body: bytes


@dataclass(frozen=True)
class ProbeResult:
    """A probe outcome with no response body or exception text retained."""

    outcome: ProbeOutcome
    attempts: int
    status_code: int | None = None


class Transport(Protocol):
    """HTTPS GET seam used by :func:`probe_url` and its tests."""

    def __call__(self, url: str, timeout: float) -> ProbeResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the probe bound to the exact Serve health route."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _strict_https_get(url: str, timeout: float) -> ProbeResponse:
    """Perform one GET with strict hostname and certificate verification.

    Ambient HTTP proxy variables are deliberately ignored.  The selected
    executor is responsible for running this function in an appropriate
    (normally off-host) probe context; a local self-request can be intercepted
    by another listener before it reaches Tailscale Serve.
    """

    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return ProbeResponse(
                status=int(response.getcode()),
                body=response.read(_MAX_RESPONSE_BYTES),
            )
    except urllib.error.HTTPError as exc:
        # urllib raises for non-2xx responses.  The status alone is enough to
        # distinguish a route miss without retaining potentially sensitive body
        # text from an intermediary.
        return ProbeResponse(status=int(exc.code), body=b"")


def _is_timeout_error(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    if isinstance(error, urllib.error.URLError):
        return _is_timeout_error(error.reason)
    return False


def _is_certificate_error(error: BaseException) -> bool:
    if isinstance(error, ssl.SSLCertVerificationError):
        return True
    if isinstance(error, urllib.error.URLError):
        return _is_certificate_error(error.reason)
    return False


def _validated_url(url: str) -> bool:
    """Accept only an HTTPS URL with a DNS host and a path.

    The health URL is assembled from trusted launcher values.  Keeping this
    validation in the probe prevents an accidentally malformed or credentialed
    URL from reaching the transport seam.
    """

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _port = parsed.port  # Force malformed explicit ports to fail closed.
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith("/")
    )


def _validate_probe_settings(timeout: float, attempts: int, retry_delay: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or isinstance(retry_delay, bool)
        or not isinstance(retry_delay, (int, float))
        or not math.isfinite(timeout)
        or not math.isfinite(retry_delay)
        or not 0 < timeout <= _MAX_TIMEOUT_SECONDS
        or not 1 <= attempts <= _MAX_ATTEMPTS
        or not 0 <= retry_delay <= _MAX_RETRY_DELAY_SECONDS
    ):
        raise ValueError(
            "bounded probe policy requires timeout in (0, 30], attempts in [1, 4], "
            "and retry_delay in [0, 5]"
        )


def _tailscale_self_dns_name() -> str | None:
    """Return this executor's Tailscale DNS identity without exposing status data."""

    try:
        completed = subprocess.run(
            ["tailscale", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_IDENTITY_TIMEOUT_SECONDS,
        )
        payload = json.loads(completed.stdout)
        dns_name = payload.get("Self", {}).get("DNSName")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(dns_name, str) or not dns_name.strip():
        return None
    return dns_name.strip().rstrip(".").lower()


def _retryable(outcome: ProbeOutcome, status_code: int | None) -> bool:
    return outcome in {
        ProbeOutcome.TIMEOUT,
        ProbeOutcome.CONNECTION_ERROR,
    } or (outcome is ProbeOutcome.HTTP_ERROR and status_code is not None and status_code >= 500)


def _body_is_ready(body: bytes) -> bool:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def probe_url(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    attempts: int = _DEFAULT_RETRIES + 1,
    retry_delay: float = _DEFAULT_RETRY_DELAY_SECONDS,
    transport: Transport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> ProbeResult:
    """Probe ``url`` with bounded retries and classify the first terminal result.

    Certificate and route failures are terminal immediately: retrying them
    cannot repair a bad certificate or a missing Serve path.  Timeouts,
    connection failures, and upstream 5xx responses get a fixed delay and a
    fixed number of additional attempts, which keeps the launcher deterministic.
    """

    if not _validated_url(url):
        return ProbeResult(ProbeOutcome.INVALID_URL, attempts=0)
    _validate_probe_settings(timeout, attempts, retry_delay)

    request = transport or _strict_https_get
    for attempt in range(1, attempts + 1):
        try:
            response = request(url, timeout)
        except Exception as error:  # noqa: BLE001 - classify transport boundaries only
            if _is_certificate_error(error):
                result = ProbeResult(ProbeOutcome.CERT_INVALID, attempt)
            elif _is_timeout_error(error):
                result = ProbeResult(ProbeOutcome.TIMEOUT, attempt)
            else:
                result = ProbeResult(ProbeOutcome.CONNECTION_ERROR, attempt)
            if not _retryable(result.outcome, result.status_code) or attempt == attempts:
                return result
        else:
            status_code = response.status
            if status_code == 404:
                return ProbeResult(ProbeOutcome.ROUTE_404, attempt, status_code)
            if status_code != 200:
                result = ProbeResult(ProbeOutcome.HTTP_ERROR, attempt, status_code)
                if not _retryable(result.outcome, result.status_code) or attempt == attempts:
                    return result
            elif not _body_is_ready(response.body):
                return ProbeResult(ProbeOutcome.BODY_INVALID, attempt, status_code)
            else:
                return ProbeResult(ProbeOutcome.OK, attempt, status_code)

        if retry_delay:
            sleeper(retry_delay)

    # The loop always returns from a terminal outcome.  Keep a defensive result
    # for static analyzers and future changes to the retry policy.
    return ProbeResult(ProbeOutcome.CONNECTION_ERROR, attempts)


def _safe_target(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "<invalid-host>"
        path = parsed.path or "/"
        return f"{host}{path}"
    except ValueError:
        return "<invalid-url>"


def format_failure(result: ProbeResult, url: str) -> str:
    """Return an actionable but content-blind operator message."""

    target = _safe_target(url)
    if result.outcome is ProbeOutcome.CERT_INVALID:
        return (
            f"Tailscale Serve data-plane cert-invalid for https://{target}: "
            "strict TLS rejected the certificate (hostname mismatch, untrusted "
            "chain, or expiry). Verify the Serve certificate from an off-host "
            "tailnet client; no certificate or mapping mutation was attempted."
        )
    if result.outcome is ProbeOutcome.ROUTE_404:
        return (
            f"Tailscale Serve mapping-ok-but-route-404 for https://{target}: "
            "the HTTPS listener returned 404. Recheck the exact path mapping "
            "and proxy target; the probe made no Serve changes."
        )
    if result.outcome is ProbeOutcome.TIMEOUT:
        return (
            f"Tailscale Serve mapping-ok-but-timeout for https://{target} after "
            f"{result.attempts} attempt(s): the selected probe context could not "
            "complete HTTPS. Check off-host tailnet reachability and API startup."
        )
    if result.outcome is ProbeOutcome.BODY_INVALID:
        return (
            f"Tailscale Serve data-plane body-invalid for https://{target}: "
            f"expected HTTP 200 with top-level JSON status=ok, got HTTP "
            f"{result.status_code}. The response body was not recorded."
        )
    if result.outcome is ProbeOutcome.HTTP_ERROR:
        return (
            f"Tailscale Serve data-plane http-error for https://{target}: "
            f"expected HTTP 200, got HTTP {result.status_code}. Check API "
            "readiness and proxy target; the probe is read-only."
        )
    if result.outcome is ProbeOutcome.CONNECTION_ERROR:
        return (
            f"Tailscale Serve mapping-ok-but-connection-error for https://{target} "
            f"after {result.attempts} attempt(s): the selected probe context could "
            "not establish HTTPS. Check off-host tailnet reachability."
        )
    return f"Tailscale Serve probe failed ({result.outcome}) for https://{target}."


_EXIT_CODES = {
    ProbeOutcome.OK: 0,
    ProbeOutcome.CERT_INVALID: 20,
    ProbeOutcome.ROUTE_404: 21,
    ProbeOutcome.TIMEOUT: 22,
    ProbeOutcome.BODY_INVALID: 23,
    ProbeOutcome.HTTP_ERROR: 24,
    ProbeOutcome.CONNECTION_ERROR: 25,
    ProbeOutcome.INVALID_URL: 26,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Expected public HTTPS health URL")
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--retries", type=int, default=_DEFAULT_RETRIES)
    parser.add_argument("--retry-delay", type=float, default=_DEFAULT_RETRY_DELAY_SECONDS)
    args = parser.parse_args(argv)

    try:
        _validate_probe_settings(args.timeout, args.retries + 1, args.retry_delay)
    except ValueError as error:
        print(f"ERROR: invalid Tailscale Serve probe settings: {error}", file=sys.stderr)
        return 2

    if not _validated_url(args.url):
        result = ProbeResult(ProbeOutcome.INVALID_URL, attempts=0)
    else:
        target_hostname = urlsplit(args.url).hostname
        source_hostname = _tailscale_self_dns_name()
        if not source_hostname:
            print(
                "ERROR: Tailscale Serve probe identity-unavailable: the executor could not "
                "derive its own Tailscale DNS identity; refusing unverified probe evidence.",
                file=sys.stderr,
            )
            return 27
        if source_hostname == (target_hostname or "").rstrip(".").lower():
            print(
                "ERROR: Tailscale Serve probe identity-same-host: the executor is the target "
                "host, so this request cannot prove the external Serve route.",
                file=sys.stderr,
            )
            return 27
        print("TAILSCALE_SERVE_PROBE_IDENTITY=verified-distinct")
        print("Tailscale Serve probe executor identity: verified distinct from target")
        try:
            result = probe_url(
                args.url,
                timeout=args.timeout,
                attempts=args.retries + 1,
                retry_delay=args.retry_delay,
            )
        except ValueError as error:
            print(f"ERROR: invalid Tailscale Serve probe settings: {error}", file=sys.stderr)
            return 2

    if result.outcome is ProbeOutcome.OK:
        print(f"Tailscale Serve data-plane: ready (https://{_safe_target(args.url)})")
        return 0
    print(f"ERROR: {format_failure(result, args.url)}", file=sys.stderr)
    return _EXIT_CODES[result.outcome]


if __name__ == "__main__":
    raise SystemExit(main())
