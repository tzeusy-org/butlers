#!/usr/bin/env python3
"""Republish a connector's loopback ``/metrics`` on the pod network, and nothing else.

Connector health servers bind ``127.0.0.1`` and also serve ``/health`` and
mutating routes such as ``POST /reload``, so binding them wider would expose
more than metrics. The Helm chart runs this as a sidecar in each connector pod:
it answers ``GET /metrics`` by fetching the connector's own loopback endpoint
and refuses every other path and method, so a cluster scraper can read metrics
without reaching the rest of the health server.

Stdlib only: the sidecar runs under the image's system interpreter, without
the project virtualenv.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A scrape is a few hundred series; refuse to buffer anything pathological.
MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
# Seconds a scraper connection may sit idle before the proxy drops it.
CLIENT_TIMEOUT_S = 10


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None  # Relay nothing but the upstream's own /metrics.


# No environment proxies (HTTP_PROXY would divert the loopback fetch) and no
# redirects: a 3xx surfaces as an HTTPError and becomes a 502.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def make_handler(upstream_url: str, timeout_s: float) -> type[BaseHTTPRequestHandler]:
    class MetricsProxyHandler(BaseHTTPRequestHandler):
        server_version = "connector-metrics-proxy"
        sys_version = ""
        timeout = CLIENT_TIMEOUT_S

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            if self.path.split("?", 1)[0] != "/metrics":
                self._reply(HTTPStatus.NOT_FOUND, b"not found\n")
                return
            try:
                with _OPENER.open(upstream_url, timeout=timeout_s) as upstream:
                    body = upstream.read(MAX_BODY_BYTES + 1)
                    content_type = upstream.headers.get("Content-Type", DEFAULT_CONTENT_TYPE)
            except (urllib.error.URLError, OSError) as exc:
                # The connector is down or still starting; the scraper records `up == 0`.
                self._reply(HTTPStatus.BAD_GATEWAY, f"upstream unavailable: {exc}\n".encode())
                return
            if len(body) > MAX_BODY_BYTES:
                self._reply(HTTPStatus.BAD_GATEWAY, b"upstream response too large\n")
                return
            self._reply(HTTPStatus.OK, body, content_type)

        def _refuse_method(self) -> None:
            self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b"method not allowed\n", allow="GET")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _refuse_method  # noqa: N815

        def _reply(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str = "text/plain; charset=utf-8",
            allow: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if allow is not None:
                self.send_header("Allow", allow)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Scrapes arrive every interval; only failures are worth a log line.
            return

        def log_error(self, format: str, *args: object) -> None:  # noqa: A002
            sys.stderr.write(f"connector-metrics-proxy: {format % args}\n")

    return MetricsProxyHandler


def build_server(
    listen_host: str, listen_port: int, upstream_port: int, timeout_s: float
) -> ThreadingHTTPServer:
    upstream_url = f"http://127.0.0.1:{upstream_port}/metrics"
    server = ThreadingHTTPServer((listen_host, listen_port), make_handler(upstream_url, timeout_s))
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=9464)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    server = build_server(args.listen_host, args.listen_port, args.upstream_port, args.timeout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
