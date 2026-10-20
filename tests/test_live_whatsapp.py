"""Hermes WhatsApp bridge adapter tests: local fake bridge only; the real bridge is never called."""

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from mascot_wardrobe.channels.live import HermesWhatsAppProfileAdapter, publish_to_catalog

HEADER = "X-Example-Avatar: 1"


def jpeg(color="red", size=(64, 64)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "JPEG")
    return stream.getvalue()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src" / "sunny.jpg"
        self.source.parent.mkdir()
        self.source.write_bytes(jpeg())
        self.catalog = self.root / "approved"
        self.catalog.mkdir()

    def test_copies_validated_slug_atomically_and_is_idempotent(self):
        first = publish_to_catalog("sunny", self.source, self.catalog)
        target = self.catalog / "sunny.jpg"
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual(first["action"], "copied")
        stamp = target.stat().st_mtime_ns
        second = publish_to_catalog("sunny", self.source, self.catalog)
        self.assertEqual(second["action"], "unchanged")
        self.assertEqual(target.stat().st_mtime_ns, stamp)
        self.assertEqual(sorted(p.name for p in self.catalog.iterdir()), ["sunny.jpg"])

    def test_unsafe_slug_catalog_and_images_are_refused_without_writing(self):
        with self.assertRaisesRegex(ValueError, "Invalid slug"):
            publish_to_catalog("../x", self.source, self.catalog)
        with self.assertRaises(ValueError):
            publish_to_catalog("Sunny", self.source, self.catalog)
        link = self.root / "link"
        os.symlink(self.catalog, link)
        with self.assertRaises(ValueError):
            publish_to_catalog("sunny", self.source, link)
        with self.assertRaises(ValueError):
            publish_to_catalog("sunny", self.source, self.root / "missing")
        (self.catalog / "sunny.jpg").symlink_to(self.source)
        with self.assertRaises(ValueError):
            publish_to_catalog("sunny", self.source, self.catalog)
        (self.catalog / "sunny.jpg").unlink()
        bad = self.root / "bad.jpg"
        bad.write_bytes(b"not a jpeg")
        with self.assertRaises(ValueError):
            publish_to_catalog("sunny", bad, self.catalog)
        wide = self.root / "wide.jpg"
        wide.write_bytes(jpeg(size=(64, 32)))
        with self.assertRaises(ValueError):
            publish_to_catalog("sunny", wide, self.catalog)
        self.assertEqual(list(self.catalog.iterdir()), [])


@contextmanager
def fake_bridge(mode="ok", initially_matching=False):
    events = []
    state = {"posted": initially_matching}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status=200, raw=None):
            data = raw if raw is not None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            events.append(
                ("GET", self.path, self.headers.get("X-Example-Avatar"), self.headers.get("Origin"))
            )
            if mode == "http-error":
                return self.reply({}, status=500)
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/")
                self.end_headers()
                return
            if mode == "oversize":
                return self.reply(None, raw=b" " * (3 * 1024 * 1024 + 10))
            image = jpeg()
            payload = {
                "target": "other" if mode == "target" else "self",
                "slug": "sunny",
                "matches": state["posted"] and mode != "still-different",
                "imageBase64": base64.b64encode(image).decode(),
                "remoteSha256": hashlib.sha256(image).hexdigest(),
            }
            if mode == "no-readback":
                payload.pop("imageBase64")
                payload.pop("remoteSha256")
                payload.pop("matches")
            self.reply(payload)

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            events.append(
                ("POST", self.path, json.loads(self.rfile.read(length)), self.headers.get("Origin"))
            )
            state["posted"] = True
            ack = {"success": True, "target": "other" if mode == "ack-target" else "self"}
            self.reply(ack)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class BridgeAdapterTests(unittest.TestCase):
    def adapter(self, url, **kwargs):
        return HermesWhatsAppProfileAdapter(url, bridge_header=HEADER, **kwargs)

    def test_updates_only_after_mismatch_and_verifies_by_readback(self):
        with fake_bridge() as (url, events):
            result = self.adapter(url).sync("sunny")
        self.assertEqual([e[0] for e in events], ["GET", "POST", "GET"])
        self.assertEqual(events[1][2], {"slug": "sunny"})
        self.assertTrue(all(e[-1] is None for e in events))  # no Origin header
        self.assertEqual(events[0][2], "1")
        self.assertEqual(events[0][1], "/profile-picture?slug=sunny")
        self.assertEqual(result["action"], "updated")
        self.assertTrue(result["acknowledged"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["target"], "self")
        self.assertFalse(result["sandbox"])

    def test_matching_readback_is_unchanged_without_post(self):
        with fake_bridge(initially_matching=True) as (url, events):
            result = self.adapter(url).sync("sunny")
        self.assertEqual([e[0] for e in events], ["GET"])
        self.assertEqual(result["action"], "unchanged")
        self.assertTrue(result["verified"])

    def test_every_unsafe_bridge_behavior_fails_closed(self):
        for mode in (
            "target",
            "ack-target",
            "no-readback",
            "http-error",
            "redirect",
            "oversize",
            "still-different",
        ):
            with self.subTest(mode=mode), fake_bridge(mode) as (url, _events):
                with self.assertRaises(RuntimeError):
                    self.adapter(url, timeout=2).sync("sunny")

    def test_acknowledged_but_unmatched_readback_is_never_reported_verified(self):
        with (
            fake_bridge("still-different") as (url, events),
            self.assertRaisesRegex(RuntimeError, "not verified"),
        ):
            self.adapter(url).sync("sunny")
        self.assertIn("POST", [e[0] for e in events])

    def test_destinations_and_headers_are_validated_before_any_request(self):
        for url in (
            "http://localhost:3000",
            "http://192." + "0.2.10:3000",
            "https://127.0.0.1:3000",
            "http://127.0.0.1",
            "http://user:pw@127.0.0.1:3000",
            "http://127.0.0.1:3000/profile-picture",
            "http://127.0.0.1:3000?x=1",
            "http://0.0.0.0:3000",
            "http://[::ffff:" + "8.8." + "8.8]:3000",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                HermesWhatsAppProfileAdapter(url, bridge_header=HEADER)
        for header in (
            "",
            "NoColon",
            "Bad Name: 1",
            "X-A: line\nbreak",
            "Origin: http://x",
            "X-A: " + "v" * 300,
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                HermesWhatsAppProfileAdapter("http://127.0.0.1:3000", bridge_header=header)
        with self.assertRaisesRegex(ValueError, "Invalid slug"):
            HermesWhatsAppProfileAdapter("http://127.0.0.1:3000", bridge_header=HEADER).sync("../x")
        HermesWhatsAppProfileAdapter("http://127.0.0.1:3000", bridge_header=HEADER)
        HermesWhatsAppProfileAdapter("http://[::1]:3000", bridge_header=HEADER)
