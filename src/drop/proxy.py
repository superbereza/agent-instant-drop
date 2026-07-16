"""Basic-auth reverse proxy for drop apps.

V1 sync HTTP req/response only — WebSocket / Upgrade requests are
rejected with 501. Includes:
  - SSRF guard: path must start with "/"
  - Redirect pass-through (no following on the server side)
  - Optional --rewrite-host body rewrite for SPAs with hardcoded
    http://localhost:<port> in their JS bundle
"""

import argparse
import base64
import binascii
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, storage
from .auth import RateLimiter, parse_basic_auth, verify_password
from .config import AUTH_REALM


_HOP_BY_HOP_RESPONSE = frozenset({
    "transfer-encoding", "connection", "server", "date",
})

# When we rewrite the body, we also strip the content-encoding (we forced
# identity upstream) and content-length (recomputed).
_HOP_BY_HOP_RESPONSE_REWRITE = _HOP_BY_HOP_RESPONSE | {"content-encoding", "content-length"}

_HOP_BY_HOP_REQUEST = frozenset({
    "host", "authorization", "content-length", "connection",
})

# Only rewrite text MIME types. JSON intentionally excluded.
_REWRITE_TYPES = ("text/html", "text/javascript", "application/javascript", "text/css")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Re-raise 30x as HTTPError so the proxy can forward Location to the client."""

    def http_error_301(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


_opener = urllib.request.build_opener(_NoRedirect)


class ProxyHandler(BaseHTTPRequestHandler):
    APP_PORT: int
    AUTH: dict  # {"scheme": "basic", "user": str, "password_hash": str}
    REWRITE_HOST: bool = False
    REWRITE_NEEDLE: bytes = b""
    # Throttle failed basic-auth attempts (per client IP). Credentials are
    # high-entropy, but this stops unbounded online guessing all the same.
    _LIMITER = RateLimiter(max_attempts=config.RL_PER_IP_MAX, window_sec=config.RL_WINDOW_SEC)

    def _check_auth(self) -> bool:
        creds = parse_basic_auth(self.headers.get("Authorization", ""))
        if creds is None:
            return False
        user, password = creds
        return user == self.AUTH["user"] and verify_password(password, self.AUTH["password_hash"])

    def _reject_upgrade(self) -> bool:
        connection = (self.headers.get("Connection", "") or "").lower()
        has_upgrade_header = self.headers.get("Upgrade") is not None
        if "upgrade" in connection or has_upgrade_header:
            self.send_response(501)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"WebSocket/Upgrade not supported by drop V1 proxy.\n"
            )
            return True
        return False

    def _request_origin(self) -> bytes:
        """Public origin (scheme://host) the client used. cloudflared injects
        X-Forwarded-*; fall back to Host header (assume https)."""
        proto = self.headers.get("X-Forwarded-Proto", "https")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        return f"{proto}://{host}".encode("utf-8")

    def _proxy(self, method: str) -> None:
        if self._reject_upgrade():
            return
        # SSRF guard: a crafted line like "GET @evil.com/foo HTTP/1.1" sets
        # self.path = "@evil.com/foo"; urllib then parses the constructed
        # URL with "127.0.0.1:<port>" as userinfo and connects to evil.com.
        if not self.path.startswith("/"):
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bad request: path must be absolute.\n")
            return
        if not self._check_auth():
            # Count only genuine failed attempts (creds supplied but wrong), so
            # the credential-less first request browsers send doesn't burn the
            # budget.
            had_creds = self.headers.get("Authorization", "").startswith("Basic ")
            client_ip = self.client_address[0] if self.client_address else "0.0.0.0"
            if had_creds and not self._LIMITER.check_and_record(client_ip, "proxy"):
                self.send_response(429)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Too many attempts; try again in a minute.\n")
                return
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Basic realm="{AUTH_REALM}"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authentication required.\n")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        url = f"http://127.0.0.1:{self.APP_PORT}{self.path}"
        req = urllib.request.Request(url, data=body, method=method)
        for h, v in self.headers.items():
            if h.lower() not in _HOP_BY_HOP_REQUEST:
                req.add_header(h, v)
        if self.REWRITE_HOST:
            req.add_header("Accept-Encoding", "identity")

        try:
            with _opener.open(req, timeout=30) as resp:
                self._forward_response(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as e:
            self._forward_response(e.code, e.headers, e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"proxy error: {e}\n".encode())

    def _forward_response(self, status: int, headers, body: bytes) -> None:
        rewrite = False
        if self.REWRITE_HOST and body:
            ctype = (headers.get("Content-Type") or "").lower()
            if any(ctype.startswith(t) for t in _REWRITE_TYPES) and self.REWRITE_NEEDLE in body:
                body = body.replace(self.REWRITE_NEEDLE, self._request_origin())
                rewrite = True

        self.send_response(status)
        skip = _HOP_BY_HOP_RESPONSE_REWRITE if rewrite else _HOP_BY_HOP_RESPONSE
        for h, v in headers.items():
            if h.lower() not in skip:
                self.send_header(h, v)
        if rewrite:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None: self._proxy("GET")
    def do_POST(self) -> None: self._proxy("POST")
    def do_PUT(self) -> None: self._proxy("PUT")
    def do_DELETE(self) -> None: self._proxy("DELETE")
    def do_PATCH(self) -> None: self._proxy("PATCH")
    def do_HEAD(self) -> None: self._proxy("HEAD")
    def do_OPTIONS(self) -> None: self._proxy("OPTIONS")

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-id", required=True)
    ap.add_argument("--proxy-port", type=int, required=True)
    ap.add_argument("--app-port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    page = storage.get_page(args.page_id)
    if page is None or page.auth is None:
        print(f"error: no auth config for page_id={args.page_id}", file=sys.stderr)
        sys.exit(1)

    ProxyHandler.APP_PORT = args.app_port
    ProxyHandler.AUTH = {
        "scheme": page.auth.scheme,
        "user": page.auth.user,
        "password_hash": page.auth.password_hash,
    }
    ProxyHandler.REWRITE_HOST = bool(getattr(page, "rewrite_host", False))
    ProxyHandler.REWRITE_NEEDLE = f"http://localhost:{args.app_port}".encode("utf-8")

    server = ThreadingHTTPServer((args.bind, args.proxy_port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
