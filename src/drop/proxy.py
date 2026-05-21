"""Basic-auth reverse proxy for drop apps.

Runs as a subprocess in front of a user app, terminating HTTP basic auth
and forwarding to 127.0.0.1:<app_port>. V1 = sync HTTP request/response
only; WebSocket / Upgrade requests are rejected with 501.
"""

import argparse
import base64
import binascii
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import storage
from .utils import AUTH_REALM, verify_password


# Headers we never copy from upstream — BaseHTTPRequestHandler.send_response()
# already sets Server/Date, and Connection/Transfer-Encoding are hop-by-hop.
_HOP_BY_HOP_RESPONSE = frozenset({"transfer-encoding", "connection", "server", "date"})

# Same as _HOP_BY_HOP_RESPONSE but also drops Content-Encoding and
# Content-Length when we rewrite the body (we forced identity upstream and
# the rewritten body has a new length).
_HOP_BY_HOP_RESPONSE_REWRITE = _HOP_BY_HOP_RESPONSE | {"content-encoding", "content-length"}

# Headers we never forward client→upstream — Host is set by urllib from the
# upstream URL, Authorization is the proxy's own credentials, Content-Length
# is recomputed by urllib, Connection is hop-by-hop.
_HOP_BY_HOP_REQUEST = frozenset({"host", "authorization", "content-length", "connection"})

# Content-Types we apply substring rewrite to. Anything binary/streaming is
# skipped (would corrupt). JSON intentionally NOT included: a legitimate
# string value of "http://localhost:N" in a payload would get mutated.
_REWRITE_TYPES = ("text/html", "text/javascript", "application/javascript", "text/css")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Re-raise 30x as HTTPError so the reverse-proxy forwards Location header."""

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
    REWRITE_NEEDLE: bytes = b""  # computed from APP_PORT at startup

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        user, sep, pw = decoded.partition(":")
        if not sep:
            return False
        return user == self.AUTH["user"] and verify_password(pw, self.AUTH["password_hash"])

    def _reject_upgrade(self) -> bool:
        connection = (self.headers.get("Connection", "") or "").lower()
        if "upgrade" in connection:
            self.send_response(501)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"WebSocket/Upgrade not supported by drop V1 proxy.\n"
                b"App can still be reached locally on its own port.\n"
            )
            return True
        return False

    def _proxy(self, method: str) -> None:
        if self._reject_upgrade():
            return
        # Path must be absolute (start with "/"). Otherwise a crafted request
        # line like "GET @evil.com/foo HTTP/1.1" would make urllib treat
        # "127.0.0.1:<port>" as userinfo and connect to evil.com instead.
        if not self.path.startswith("/"):
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bad request: path must be absolute.\n")
            return
        if not self._check_auth():
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
        # If we plan to rewrite the body, force upstream to send uncompressed
        # so the needle is actually present.
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

    def _request_origin(self) -> bytes:
        """Return the public origin (scheme://host) the client used.

        cloudflared injects X-Forwarded-Proto and X-Forwarded-Host. Fall back
        to the Host header (and assume https since --auth flow requires
        a tunnel; otherwise rewrite is probably not running)."""
        proto = self.headers.get("X-Forwarded-Proto", "https")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        return f"{proto}://{host}".encode("utf-8")

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
            # We changed body length and stripped Content-Encoding; set new length.
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
        pass  # silence stderr access log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-id", required=True)
    ap.add_argument("--proxy-port", type=int, required=True)
    ap.add_argument("--app-port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    page = storage.get_page(args.page_id)
    if not page or not page.get("auth"):
        print(f"error: no auth config for page_id={args.page_id}", file=sys.stderr)
        sys.exit(1)

    ProxyHandler.APP_PORT = args.app_port
    ProxyHandler.AUTH = page["auth"]
    ProxyHandler.REWRITE_HOST = bool(page.get("rewrite_host"))
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
