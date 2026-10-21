"""`publish` CLI: dry-run default, explicit --apply, fake transports only."""

import base64
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from mascot_wardrobe import application
from mascot_wardrobe.interfaces import cli
from tests.imaging import write_body_png

FAKE_TOKEN = "123456:FAKE-TEST-TOKEN"
HEADER = "X-Example-Avatar: 1"


class SlugBridge:
    """Slug-aware loopback bridge that reads <slug>.jpg from its approved directory."""

    def __init__(self, approved):
        self.approved, self.posts, bridge = approved, [], self
        self.current = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                slug = parse_qs(urlsplit(self.path).query)["slug"][0]
                image = (bridge.approved / f"{slug}.jpg").read_bytes()
                self.reply(
                    {
                        "target": "self",
                        "slug": slug,
                        "matches": bridge.current.get(slug) == image,
                        "imageBase64": base64.b64encode(image).decode(),
                        "remoteSha256": hashlib.sha256(image).hexdigest(),
                    }
                )

            def do_POST(self):
                slug = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["slug"]
                bridge.posts.append(slug)
                bridge.current[slug] = (bridge.approved / f"{slug}.jpg").read_bytes()
                self.reply({"success": True, "target": "self"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class PublishCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "mascot.png"
        write_body_png(source, "#666666")
        self.manifest = application.prepare_wardrobe(source, "pet", "BR", self.root / "catalog")
        self.approved = self.root / "approved"
        self.approved.mkdir()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["publish", "--manifest", str(self.manifest), "--country", "BR", *map(str, args)])
        return code, out.getvalue(), err.getvalue()

    def whatsapp_args(self, url):
        return (
            "--whatsapp-catalog-dir",
            self.approved,
            "--whatsapp-bridge-url",
            url,
            "--whatsapp-bridge-header",
            HEADER,
        )

    def test_default_is_a_dry_run_with_atlas_and_no_side_effects(self):
        code, out, _ = self.run_cli(
            "--temperature",
            25,
            *self.whatsapp_args("http://127.0.0.1:1"),
            "--telegram",
            "--telegram-expected-bot-id",
            42,
        )
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "dry-run")
        self.assertTrue(result["atlas"].endswith("-atlas.png"))
        self.assertEqual(result["channels"]["whatsapp"]["status"], "planned")
        self.assertEqual(result["channels"]["telegram"]["status"], "planned")
        self.assertEqual(list(self.approved.iterdir()), [])

    def test_apply_needs_a_channel_and_complete_whatsapp_settings(self):
        self.assertEqual(self.run_cli("--temperature", 25, "--apply")[0], 2)
        code, _, err = self.run_cli("--temperature", 25, "--whatsapp-catalog-dir", self.approved)
        self.assertEqual(code, 2)
        self.assertIn("whatsapp", err)
        code, _, err = self.run_cli("--temperature", 25, "--telegram")
        self.assertEqual(code, 2)
        self.assertIn("expected-bot-id", err)

    def test_apply_whatsapp_through_a_local_bridge_verifies_and_repeats_unchanged(self):
        with SlugBridge(self.approved) as bridge:
            args = ("--temperature", 25, "--apply", *self.whatsapp_args(bridge.url))
            code, out, _ = self.run_cli(*args)
            first = json.loads(out)
            code2, out2, _ = self.run_cli(*args)
        self.assertEqual((code, code2), (0, 0))
        self.assertTrue(first["channels"]["whatsapp"]["verified"])
        self.assertEqual(first["channels"]["whatsapp"]["action"], "updated")
        self.assertEqual(json.loads(out2)["channels"]["whatsapp"]["action"], "unchanged")
        self.assertEqual(len(bridge.posts), 1)

    def test_apply_telegram_uses_env_token_and_reports_unverified_with_redaction(self):
        calls = []

        def transport(token, method, fields, files):
            calls.append((token, method))
            return {"ok": True, "result": {"id": 42, "is_bot": True} if method == "getMe" else True}

        env = {"MW_TEST_BOT_TOKEN": FAKE_TOKEN}
        args = (
            "--temperature",
            25,
            "--apply",
            "--telegram",
            "--telegram-expected-bot-id",
            42,
            "--telegram-token-env",
            "MW_TEST_BOT_TOKEN",
            "--telegram-state-dir",
            self.root / "state",
        )
        with (
            mock.patch.dict("os.environ", env),
            mock.patch("mascot_wardrobe.channels.live.telegram_transport", return_value=transport),
        ):
            code, out, err = self.run_cli(*args)
        self.assertEqual(code, 0, err)
        self.assertEqual([c[1] for c in calls], ["getMe", "setMyProfilePhoto"])
        self.assertFalse(json.loads(out)["channels"]["telegram"]["verified"])
        self.assertNotIn(FAKE_TOKEN, out + err)

    def test_missing_credentials_fail_the_run_without_traceback_or_secrets(self):
        args = (
            "--temperature",
            25,
            "--apply",
            "--telegram",
            "--telegram-expected-bot-id",
            42,
            "--telegram-token-env",
            "MW_ABSENT_TOKEN",
            "--telegram-state-dir",
            self.root / "state",
        )
        code, out, err = self.run_cli(*args)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["channels"]["telegram"]["status"], "failed")
        self.assertNotIn("Traceback", err)
