"""Local channel tests; no external service is contacted."""

import base64
import hashlib
import io
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from mascot_wardrobe.channels import SandboxBridge


def jpeg(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(stream, "JPEG")
    return stream.getvalue()


def request(url, method="GET", payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = (
        {"X-Mascot-Wardrobe-Sandbox": "1", "Content-Type": "application/json"} if headers is None else headers
    )
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=2) as response:
            return json.load(response)
    except HTTPError as error:
        error.close()
        raise


@contextmanager
def endpoint(mode, photo):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            events.append(("POST", self.path))
            length = int(self.headers["Content-Length"])
            events.append(("payload", json.loads(self.rfile.read(length))))
            payload = {"success": True, "target": "self", "sandbox": True, "verified": False}
            if mode == "ack-target":
                payload["target"] = "other"
            self.reply(payload)

        def do_GET(self):
            events.append(("GET", self.path))
            if self.path == "/health":
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:3000/profile-picture")
                    self.end_headers()
                    return
                return self.reply({"status": "connected", "sandbox": mode != "production"})
            uploaded = any(item[0] == "POST" for item in events)
            payload = {
                "target": "self",
                "sandbox": True,
                "matches": uploaded,
                "slug": "sunny",
                "remoteSha256": hashlib.sha256(photo).hexdigest(),
                "imageBase64": base64.b64encode(photo).decode(),
            }
            if mode == "target" or (mode == "readback-target" and uploaded):
                payload["target"] = "other"
            if mode == "slug":
                payload["slug"] = "different"
            if mode == "bad-base64":
                payload["imageBase64"] = "not base64!"
            if mode == "readback-hash" and uploaded:
                payload["remoteSha256"] = "0" * 64
            if mode == "profile-production":
                payload["sandbox"] = False
            if mode == "ack-only":
                payload["matches"] = False
            if mode == "hash":
                payload["remoteSha256"] = "0" * 64
            if mode == "nonboolean":
                payload["matches"] = "yes"
            self.reply(payload)

        def reply(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        self.photo = jpeg()
        (self.catalog / "sunny.jpg").write_bytes(self.photo)
        self.state = self.root / "state"

    def test_real_http_upload_readback_and_idempotence(self):
        with SandboxBridge(self.catalog, self.state) as bridge:
            self.assertNotEqual(bridge.port, 3000)
            self.assertEqual(request(bridge.base_url + "/health"), {"status": "connected", "sandbox": True})
            url = bridge.base_url + "/profile-picture"
            before = request(url + "?slug=sunny")
            self.assertFalse(before["matches"])
            ack = request(url, "POST", {"slug": "sunny"})
            self.assertTrue(ack["success"])
            self.assertFalse(ack["verified"])
            observed = request(url + "?slug=sunny")
            self.assertTrue(observed["matches"])
            self.assertEqual(observed["target"], "self")
            self.assertTrue(observed["sandbox"])
            self.assertEqual(base64.b64decode(observed["imageBase64"]), self.photo)
            self.assertEqual(observed["remoteSha256"], hashlib.sha256(self.photo).hexdigest())
            current = self.state / "current.jpg"
            stamp = current.stat().st_mtime_ns
            request(url, "POST", {"slug": "sunny"})
            self.assertEqual(current.stat().st_mtime_ns, stamp)
            self.assertNotEqual(current.resolve(), (self.catalog / "sunny.jpg").resolve())

    def test_boundary_rejects_unsafe_files_requests_and_state(self):
        with SandboxBridge(self.catalog, self.state) as bridge:
            url = bridge.base_url + "/profile-picture"
            for payload in (
                {"slug": "../sunny"},
                {"slug": "sunny", "target": "other"},
                {"slug": "sunny", "jid": "123"},
                {"slug": "sunny", "path": "/x"},
            ):
                with self.subTest(payload=payload), self.assertRaises(HTTPError):
                    request(url, "POST", payload)
            for headers in ({}, {"X-Mascot-Wardrobe-Sandbox": "1", "Origin": "http://localhost"}):
                with self.assertRaises(HTTPError) as raised:
                    request(url + "?slug=sunny", headers=headers)
                self.assertEqual(raised.exception.code, 403)
            outside = self.root / "outside.jpg"
            outside.write_bytes(self.photo)
            (self.catalog / "escape.jpg").symlink_to(outside)
            (self.catalog / "huge.jpg").write_bytes(b"\xff\xd8" + b"x" * (2 * 1024 * 1024))
            (self.catalog / "fake.jpg").write_bytes(b"\xff\xd8not-a-jpeg")
            for slug in ("escape", "huge", "fake", "unknown"):
                with self.subTest(slug=slug), self.assertRaises(HTTPError):
                    request(url, "POST", {"slug": slug})
            for query in ("slug=sunny&target=self", "slug=sunny&slug=sunny", "slug=../sunny"):
                with self.assertRaises(HTTPError):
                    request(url + "?" + query)
            self.assertFalse((self.state / "current.jpg").exists())
        with self.assertRaises(ValueError):
            with SandboxBridge(self.catalog, self.catalog):
                pass
        outside_state = self.root / "linked-state"
        outside_state.mkdir()
        sentinel = self.root / "sentinel.jpg"
        sentinel.write_bytes(b"untouched")
        (outside_state / "current.jpg").symlink_to(sentinel)
        with self.assertRaises(ValueError):
            with SandboxBridge(self.catalog, outside_state):
                pass
        self.assertEqual(sentinel.read_bytes(), b"untouched")

    def test_channel_sync_checks_health_and_readback_repeated_noop(self):
        from mascot_wardrobe.channels import WhatsAppBridgeChannel

        with SandboxBridge(self.catalog, self.state) as bridge:
            channel = WhatsAppBridgeChannel(bridge.base_url, timeout=2)
            first = channel.sync("sunny")
            self.assertEqual(first["action"], "updated")
            self.assertTrue(first["verified"])
            self.assertEqual(first["target"], "self")
            self.assertTrue(first["sandbox"])
            self.assertEqual(first["remote_sha256"], hashlib.sha256(self.photo).hexdigest())
            stamp = (self.state / "current.jpg").stat().st_mtime_ns
            second = channel.sync("sunny")
            self.assertEqual(second["action"], "unchanged")
            self.assertEqual((self.state / "current.jpg").stat().st_mtime_ns, stamp)

    def test_channel_rejects_unsafe_urls_and_timeouts_before_network(self):
        from mascot_wardrobe.channels import WhatsAppBridgeChannel

        urls = (
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://[::1]:3000",
            "http://127.1:3000",
            "http://2130706433:3000",
            "https://example.org:9999",
            "http://0.0.0.0:8888",
            "http://localhost:8000",
            "file:///etc/passwd",
            "http://user:secret@127.0.0.1:8000",  # pragma: allowlist secret
            "http://127.0.0.1:8000/path",
            "http://127.0.0.1:8000?x=1",
            "http://127.0.0.1:8000#x",
        )
        with patch(
            "socket.socket.connect", side_effect=AssertionError("Network access is forbidden")
        ) as connect:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    WhatsAppBridgeChannel(url)
            connect.assert_not_called()
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                WhatsAppBridgeChannel("http://127.0.0.1:8888", timeout=timeout)

    def test_channel_fails_closed_on_health_target_ack_readback_and_hash(self):
        from mascot_wardrobe.channels import WhatsAppBridgeChannel

        for mode in (
            "production",
            "target",
            "profile-production",
            "ack-target",
            "ack-only",
            "hash",
            "nonboolean",
            "redirect",
            "slug",
            "readback-target",
            "readback-hash",
            "bad-base64",
        ):
            with self.subTest(mode=mode), endpoint(mode, self.photo) as (url, events):
                channel = WhatsAppBridgeChannel(url, timeout=2)
                with self.assertRaises(RuntimeError):
                    channel.sync("sunny")
                self.assertIsNone(channel.current_sha256)
                if mode in (
                    "production",
                    "target",
                    "profile-production",
                    "nonboolean",
                    "redirect",
                    "hash",
                    "slug",
                    "bad-base64",
                ):
                    self.assertFalse(any(event[0] == "POST" for event in events))
        with endpoint("ok", self.photo) as (url, events):
            result = WhatsAppBridgeChannel(url).sync("sunny")
            self.assertTrue(result["verified"])
            self.assertEqual([event[0] for event in events], ["GET", "GET", "POST", "payload", "GET"])
            self.assertIn(("payload", {"slug": "sunny"}), events)

    def test_file_exporters_copy_assets_without_claiming_remote_sync(self):
        from mascot_wardrobe.channels import FileExportChannel

        portrait = self.catalog / "sunny.jpg"
        atlas = self.root / "atlas.png"
        atlas.write_bytes(b"local atlas fixture")
        manifest = self.root / "manifest.json"
        manifest.write_text('{"portrait": "sunny.jpg", "atlas": "atlas.png"}')
        for name in ("whatsapp", "telegram", "discord", "hermes-cli"):
            with self.subTest(channel=name):
                exporter = FileExportChannel(self.root / "sandbox-output", name)
                result = exporter.export("sunny", portrait, atlas, manifest)
                self.assertEqual(result["action"], "exported")
                self.assertFalse(result["verified"])
                self.assertFalse(result["remote_sync"])
                self.assertTrue(result["sandbox"])
                destination = self.root / "sandbox-output" / name / "sunny"
                for source in (portrait, atlas, manifest):
                    self.assertEqual((destination / source.name).read_bytes(), source.read_bytes())
                stamp = (destination / portrait.name).stat().st_mtime_ns
                repeated = exporter.export("sunny", portrait, atlas, manifest)
                self.assertEqual(repeated["action"], "unchanged")
                self.assertEqual((destination / portrait.name).stat().st_mtime_ns, stamp)

    def test_export_is_confined_and_validates_all_inputs_before_writing(self):
        from mascot_wardrobe.channels import FileExportChannel

        with self.assertRaises(ValueError):
            FileExportChannel(self.root / "out", "../other")
        out = self.root / "out"
        exporter = FileExportChannel(out, "discord")
        with self.assertRaises((ValueError, FileNotFoundError)):
            exporter.export(
                "sunny", self.catalog / "sunny.jpg", self.root / "missing", self.root / "missing2"
            )
        self.assertFalse(out.exists())
        outside = self.root / "outside-output"
        outside.mkdir()
        out.mkdir()
        (out / "discord").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            exporter.export(
                "sunny", self.catalog / "sunny.jpg", self.catalog / "sunny.jpg", self.catalog / "sunny.jpg"
            )
        self.assertEqual(list(outside.iterdir()), [])
        (out / "discord").unlink()
        destination = out / "discord" / "sunny"
        destination.mkdir(parents=True)
        sentinel = self.root / "do-not-change"
        sentinel.write_bytes(b"original")
        (destination / "sunny.jpg").symlink_to(sentinel)
        atlas = self.root / "atlas.png"
        manifest = self.root / "manifest.json"
        atlas.write_bytes(b"atlas")
        manifest.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            exporter.export("sunny", self.catalog / "sunny.jpg", atlas, manifest)
        self.assertEqual(sentinel.read_bytes(), b"original")
        self.assertFalse((destination / "atlas.png").exists())
        with self.assertRaises(ValueError):
            exporter.export("../escape", self.catalog / "sunny.jpg", atlas, manifest)
        with self.assertRaises(ValueError):
            exporter.export("collision", atlas, atlas, manifest)
