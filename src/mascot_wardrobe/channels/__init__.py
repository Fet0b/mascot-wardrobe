"""Local adapters: no production channel is enabled."""

import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from PIL import Image


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Redirects are forbidden")


MAX_IMAGE_BYTES = 2 * 1024 * 1024
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\Z")


def _slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError("Invalid slug")
    return value


def _atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".avatar-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SandboxBridge:
    """Ephemeral HTTP bridge, exclusive to local sandbox simulation."""

    def __init__(self, catalog_dir: Path, state_dir: Path):
        self.catalog_dir = Path(catalog_dir).resolve()
        self.state_dir = Path(state_dir).resolve()
        self._lock = threading.Lock()

    def _photo(self, slug):
        path = (self.catalog_dir / (_slug(slug) + ".jpg")).resolve()
        if path.parent != self.catalog_dir:
            raise ValueError("Image outside the approved directory")
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8"):
            raise ValueError("JPEG required, maximum 2 MiB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise ValueError("JPEG required")
                image.verify()
        except (OSError, SyntaxError) as error:
            raise ValueError("Invalid JPEG") from error
        return data

    def __enter__(self):
        if self.state_dir.is_relative_to(self.catalog_dir) or self.catalog_dir.is_relative_to(self.state_dir):
            raise ValueError("State and catalog must be separate directories")
        if (self.state_dir / "current.jpg").is_symlink():
            raise ValueError("Symbolic-link state is forbidden")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.handle_request("GET")

            def do_POST(self):
                self.handle_request("POST")

            def handle_request(self, method):
                route = urlsplit(self.path)
                if route.path == "/health" and method == "GET":
                    return self.reply(200, {"status": "connected", "sandbox": True})
                if route.path != "/profile-picture":
                    return self.reply(404, {"error": "Unknown route"})
                if "Origin" in self.headers or self.headers.get("X-Mascot-Wardrobe-Sandbox") != "1":
                    return self.reply(403, {"error": "Local sandbox header required; Origin is forbidden"})
                try:
                    if method == "GET":
                        query = parse_qs(route.query, keep_blank_values=True)
                        if set(query) != {"slug"} or len(query["slug"]) != 1:
                            raise ValueError("Only slug is accepted")
                        slug = query["slug"][0]
                    else:
                        if route.query:
                            raise ValueError("Query string is forbidden on POST")
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= 1024:
                            raise ValueError("Invalid or oversized body")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict) or set(payload) != {"slug"}:
                            raise ValueError("Only slug is accepted; target is always self")
                        slug = payload["slug"]
                    with bridge._lock:
                        source = bridge._photo(slug)
                        current = bridge.state_dir / "current.jpg"
                        if current.is_symlink():
                            raise ValueError("Symbolic-link state is forbidden")
                        if current.exists() and current.stat().st_size > MAX_IMAGE_BYTES:
                            raise ValueError("Oversized state file")
                        observed = current.read_bytes() if current.exists() else None
                        if method == "POST":
                            if observed != source:
                                _atomic(current, source)
                            return self.reply(
                                200,
                                {
                                    "success": True,
                                    "target": "self",
                                    "slug": slug,
                                    "sandbox": True,
                                    "verified": False,
                                    "sourceSha256": hashlib.sha256(source).hexdigest(),
                                },
                            )
                        return self.reply(
                            200,
                            {
                                "target": "self",
                                "slug": slug,
                                "sandbox": True,
                                "matches": observed == source,
                                "imageBase64": base64.b64encode(observed).decode() if observed else None,
                                "remoteSha256": hashlib.sha256(observed).hexdigest() if observed else None,
                                "verification": "sandbox-exact-source-bytes",
                            },
                        )
                except (ValueError, OSError) as error:
                    self.reply(400, {"error": str(error)})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


class WhatsAppBridgeChannel:
    """Legacy-style contract limited to local sandbox bridges."""

    def __init__(self, base_url, timeout=5):
        self.base_url = self._validate_url(base_url)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("A positive finite timeout is required")
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self.current_sha256 = None

    @staticmethod
    def _validate_url(base_url):
        if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url):
            raise ValueError("Invalid local URL")
        parsed = urlsplit(base_url)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except ValueError as error:
            raise ValueError("A literal loopback IP is required; no DNS") from error
        if (
            parsed.scheme != "http"
            or not address.is_loopback
            or not port
            or port == 3000
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in base_url
            or "#" in base_url
        ):
            raise ValueError("Only sandbox HTTP on loopback is allowed; port 3000 is forbidden")
        return base_url.rstrip("/")

    def _request(self, path, method="GET", payload=None):
        self._validate_url(self.base_url)
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"X-Mascot-Wardrobe-Sandbox": "1", "Content-Type": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raw = response.read(3 * 1024 * 1024 + 1)
        except Exception as error:
            if hasattr(error, "close"):
                error.close()
            raise RuntimeError("Sandbox bridge HTTP failure") from error
        if len(raw) > 3 * 1024 * 1024:
            raise RuntimeError("Oversized response")
        try:
            result = json.loads(raw)
        except ValueError as error:
            raise RuntimeError("Invalid JSON from the bridge") from error
        if not isinstance(result, dict):
            raise RuntimeError("Invalid response")
        return result

    @staticmethod
    def _validate_observed(observed, slug):
        if observed.get("slug", slug) != slug:
            raise RuntimeError("Readback slug mismatch")
        if (
            observed.get("target") != "self"
            or observed.get("sandbox") is not True
            or type(observed.get("matches")) is not bool
        ):
            raise RuntimeError("Readback must declare self, sandbox and a boolean matches")
        image = observed.get("imageBase64")
        digest = observed.get("remoteSha256")
        if image is None and digest is None and observed["matches"] is False:
            return
        try:
            if not isinstance(image, str) or len(image) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                raise ValueError("Image missing or oversized")
            data = base64.b64decode(image, validate=True)
            if not data or len(data) > MAX_IMAGE_BYTES or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("Inconsistent hash")
        except (ValueError, TypeError) as error:
            raise RuntimeError("Inconsistent readback and hash") from error

    def sync(self, slug: str) -> dict:
        _slug(slug)
        self.current_sha256 = None
        health = self._request("/health")
        if health.get("sandbox") is not True or health.get("status") != "connected":
            raise RuntimeError("A connected sandbox bridge is required")
        path = "/profile-picture?" + urlencode({"slug": slug})
        observed = self._request(path)
        self._validate_observed(observed, slug)
        action = "unchanged"
        if not observed.get("matches"):
            ack = self._request("/profile-picture", "POST", {"slug": slug})
            if (
                ack.get("success") is not True
                or ack.get("target") != "self"
                or ack.get("sandbox") is not True
            ):
                raise RuntimeError("Upload not acknowledged for self")
            action = "updated"
            observed = self._request(path)
            self._validate_observed(observed, slug)
        if observed.get("matches") is not True or observed.get("target") != "self":
            raise RuntimeError("Follow-up readback does not confirm self")
        self.current_sha256 = observed.get("remoteSha256")
        return {
            "slug": slug,
            "action": action,
            "verified": True,
            "target": "self",
            "sandbox": True,
            "remote_sha256": self.current_sha256,
            "verification": observed.get("verification"),
        }


class FileExportChannel:
    """File export; it does not represent remote synchronization."""

    CHANNELS = frozenset({"whatsapp", "telegram", "discord", "hermes-cli"})

    def __init__(self, sandbox_dir: Path, channel: str):
        if channel not in self.CHANNELS:
            raise ValueError("Invalid export channel")
        self.sandbox_dir = Path(sandbox_dir).resolve()
        self.channel = channel

    def export(self, slug: str, portrait: Path, atlas: Path, manifest: Path) -> dict:
        _slug(slug)
        destination = self.sandbox_dir / self.channel / slug
        if destination.resolve() != destination:
            raise ValueError("Symbolic-link output is forbidden")
        sources = [("portrait", Path(portrait)), ("atlas", Path(atlas)), ("manifest", Path(manifest))]
        if len({source.name for _, source in sources}) != 3:
            raise ValueError("File names must be distinct")
        prepared = []
        for kind, source in sources:
            target = destination / source.name
            if target.is_symlink() or not target.resolve().is_relative_to(self.sandbox_dir):
                raise ValueError("File outside the sandbox output")
            prepared.append((kind, target, source.read_bytes()))
        destination.mkdir(parents=True, exist_ok=True)
        changed = False
        paths = {}
        for kind, target, data in prepared:
            if not target.exists() or target.read_bytes() != data:
                _atomic(target, data)
                changed = True
            paths[kind] = str(target)
        return {
            "slug": slug,
            "channel": self.channel,
            "sandbox": True,
            "action": "exported" if changed else "unchanged",
            "remote_sync": False,
            "verified": False,
            "files": paths,
        }
