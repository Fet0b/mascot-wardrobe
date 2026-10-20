"""Telegram bot profile adapter tests: fake transport and fake credentials only; no network."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mascot_wardrobe.channels.live import TelegramBotProfileAdapter

FAKE_TOKEN = "123456:FAKE-TEST-TOKEN"


def jpeg(color="red", size=(64, 64)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "JPEG")
    return stream.getvalue()


class FakeTransport:
    def __init__(self, me=None, fail_on=None):
        self.calls = []
        self.me = me or {"id": 42, "is_bot": True, "username": "wardrobe_bot"}
        self.fail_on = fail_on

    def __call__(self, token, method, fields, files):
        self.calls.append((token, method, dict(fields), dict(files)))
        if method == self.fail_on:
            return {"ok": False, "description": "Bad Request"}
        if method == "getMe":
            return {"ok": True, "result": self.me}
        return {"ok": True, "result": True}


class TelegramAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.photo = self.root / "sunny.jpg"
        self.photo.write_bytes(jpeg())

    def adapter(self, transport, **kwargs):
        return TelegramBotProfileAdapter(lambda: FAKE_TOKEN, transport=transport, **kwargs)

    def test_sets_static_profile_photo_after_identity_check_and_reports_unverified(self):
        transport = FakeTransport()
        result = self.adapter(transport, expected_bot_id=42).apply(self.photo)
        self.assertEqual([c[1] for c in transport.calls], ["getMe", "setMyProfilePhoto"])
        _, _, fields, files = transport.calls[1]
        self.assertEqual(json.loads(fields["photo"]), {"type": "static", "photo": "attach://photo"})
        self.assertEqual(files["photo"][1], self.photo.read_bytes())
        self.assertEqual(result["channel"], "telegram-bot")
        self.assertEqual(result["target"], "bot-self")
        self.assertTrue(result["acknowledged"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["verification"], "unverified-ack-only")
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))

    def test_identity_mismatch_stops_before_any_photo_change(self):
        transport = FakeTransport(me={"id": 7, "is_bot": True})
        with self.assertRaisesRegex(RuntimeError, "Unexpected bot identity"):
            self.adapter(transport, expected_bot_id=42).apply(self.photo)
        self.assertEqual([c[1] for c in transport.calls], ["getMe"])

    def test_api_refusal_raises_without_leaking_the_token(self):
        transport = FakeTransport(fail_on="setMyProfilePhoto")
        with self.assertRaises(RuntimeError) as caught:
            self.adapter(transport).apply(self.photo)
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        self.assertIn("setMyProfilePhoto", str(caught.exception))

    def test_non_bot_identity_and_bad_images_are_refused_before_network(self):
        transport = FakeTransport(me={"id": 1, "is_bot": False})
        with self.assertRaisesRegex(RuntimeError, "bot"):
            self.adapter(transport).apply(self.photo)
        for name, data in (
            ("rect.jpg", jpeg(size=(64, 32))),
            ("fake.jpg", b"not an image"),
            ("big.jpg", b"\xff\xd8" + b"0" * (2 * 1024 * 1024)),
        ):
            path = self.root / name
            path.write_bytes(data)
            fresh = FakeTransport()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.adapter(fresh).apply(path)
            self.assertEqual(fresh.calls, [])


class _Server:
    """Local stand-in for the Bot API; records requests, never leaves loopback."""

    def __init__(self, behavior):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, self.headers.get("Content-Type"), body))
                behavior(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _json_reply(handler, payload, status=200):
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class TelegramTransportTests(unittest.TestCase):
    def transport(self, server, **kwargs):
        from mascot_wardrobe.channels.live import telegram_transport

        return telegram_transport(api_base=server.url, **kwargs)

    def test_posts_multipart_to_the_token_path_and_parses_json(self):
        with _Server(lambda h: _json_reply(h, {"ok": True, "result": True})) as server:
            result = self.transport(server)(
                FAKE_TOKEN, "setMyProfilePhoto", {"photo": "x"}, {"photo": ("p.jpg", b"\xff\xd8data")}
            )
        path, content_type, body = server.requests[0]
        self.assertEqual(path, f"/bot{FAKE_TOKEN}/setMyProfilePhoto")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="photo"; filename="p.jpg"', body)
        self.assertIn(b"\xff\xd8data", body)
        self.assertEqual(result, {"ok": True, "result": True})

    def test_redirect_oversize_and_http_errors_fail_closed_without_the_token(self):
        def redirect(h):
            h.send_response(302)
            h.send_header("Location", "http://127.0.0.1:1/")
            h.end_headers()

        def oversize(h):
            data = b" " * (1024 * 1024 + 10)
            h.send_response(200)
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)

        def failure(h):
            _json_reply(h, {"ok": False, "description": "nope"}, status=401)

        for behavior in (redirect, oversize, failure):
            with self.subTest(behavior=behavior.__name__), _Server(behavior) as server:
                with self.assertRaises(RuntimeError) as caught:
                    self.transport(server)(FAKE_TOKEN, "getMe", {}, {})
                self.assertNotIn(FAKE_TOKEN, str(caught.exception))
                self.assertNotIn(FAKE_TOKEN, repr(caught.exception.__cause__))

    def test_only_the_official_https_host_or_loopback_is_accepted(self):
        from mascot_wardrobe.channels.live import telegram_transport

        telegram_transport()
        for base in (
            "https://evil.example",
            "http://api.telegram.org",
            "http://10." + "0.0.5:80",
            "https://api.telegram.org/x",
        ):
            with self.subTest(base=base), self.assertRaises(ValueError):
                telegram_transport(api_base=base)

    def test_environment_token_provider_never_echoes_value(self):
        from mascot_wardrobe.channels.live import environment_token_provider

        provider = environment_token_provider("MW_FAKE_TOKEN", environ={"MW_FAKE_TOKEN": FAKE_TOKEN})
        self.assertEqual(provider(), FAKE_TOKEN)
        for environ in ({}, {"MW_FAKE_TOKEN": "  "}, {"MW_FAKE_TOKEN": "bad token\n"}):
            with self.subTest(environ=environ), self.assertRaises(RuntimeError) as caught:
                environment_token_provider("MW_FAKE_TOKEN", environ=environ)()
            self.assertNotIn("bad token", str(caught.exception))
        with self.assertRaises(ValueError):
            environment_token_provider("not valid")


class TelegramHardeningTests(unittest.TestCase):
    def test_loopback_test_urls_need_literal_ip_valid_port_and_nothing_else(self):
        from mascot_wardrobe.channels.live import telegram_transport

        telegram_transport(api_base="http://127.0.0.1:8081")
        telegram_transport(api_base="http://[::1]:8081/")
        for base in (
            "http://user" + ":pass" + "@127.0.0.1:8081",
            "http://127.0.0.1:8081#frag",
            "http://127.0.0.1:8081?x=1",
            "http://127.0.0.1",
            "http://127.0.0.1:99999",
            "http://127.0.0.1:0",
            "http://127.0.0.1:abc",
            "http://127.0.0.1:8081/path",
            "http://localhost:8081",
            "https://127.0.0.1:8081",
            "https://api.telegram.org/",
            "https://api.telegram.org:443",
        ):
            with self.subTest(base=base), self.assertRaises(ValueError):
                telegram_transport(api_base=base)

    def test_photo_part_is_image_jpeg(self):
        with _Server(lambda h: _json_reply(h, {"ok": True, "result": True})) as server:
            from mascot_wardrobe.channels.live import telegram_transport

            telegram_transport(api_base=server.url)(
                FAKE_TOKEN, "setMyProfilePhoto", {}, {"photo": ("profile.jpg", jpeg())}
            )
        self.assertIn(b"Content-Type: image/jpeg", server.requests[0][2])

    def test_set_photo_result_must_be_exactly_true(self):
        for result in (False, None, "true", 1, {"ok": True}):
            with self.subTest(result=result):
                transport = FakeTransport()
                original = transport.__call__

                def patched(token, method, fields, files, original=original, result=result):
                    response = original(token, method, fields, files)
                    if method == "setMyProfilePhoto":
                        response["result"] = result
                    return response

                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                photo = Path(tmp.name) / "p.jpg"
                photo.write_bytes(jpeg())
                adapter = TelegramBotProfileAdapter(lambda: FAKE_TOKEN, transport=patched)
                with self.assertRaisesRegex(RuntimeError, "not accepted"):
                    adapter.apply(photo)


class TelegramRedactionTests(unittest.TestCase):
    def test_no_exception_context_or_cause_retains_the_token_bearing_url(self):
        def failure(h):
            _json_reply(h, {"ok": False}, status=401)

        with _Server(failure) as server:
            from mascot_wardrobe.channels.live import telegram_transport

            with self.assertRaises(RuntimeError) as caught:
                telegram_transport(api_base=server.url)(FAKE_TOKEN, "getMe", {}, {})
        error = caught.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        import traceback

        self.assertNotIn(FAKE_TOKEN, "".join(traceback.format_exception(error)))
