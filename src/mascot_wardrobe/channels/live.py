"""Opt-in live profile adapters. Unlike the sandbox channels these can change real accounts.

Nothing here runs unless a caller constructs an adapter and calls it. Credentials are never
stored: they come from an injected provider or an environment variable.
"""

import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image

from mascot_wardrobe.channels import MAX_IMAGE_BYTES, _NoRedirect

TELEGRAM_API = "https://api.telegram.org"
MAX_API_RESPONSE = 1024 * 1024
TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{8,128}\Z")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}\Z")


def environment_token_provider(name, *, environ=None):
    """Return a callable reading a bot token from the environment at call time."""
    if not isinstance(name, str) or not ENV_NAME.fullmatch(name):
        raise ValueError("Invalid environment variable name")

    def provide():
        value = (os.environ if environ is None else environ).get(name, "").strip()
        if not TOKEN.fullmatch(value):
            raise RuntimeError(f"Environment variable {name} does not hold a usable bot token")
        return value

    return provide


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    for key, (filename, content) in files.items():
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        )
        parts.append(head.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


def _validate_loopback_url(url, message):
    """Accept only plain HTTP on a literal loopback IP with an explicit valid port and no extras."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "?" in url or "#" in url:
        raise ValueError(message)
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError:
        raise ValueError(message) from None
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or not port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
    ):
        raise ValueError(message)
    return url.rstrip("/")


def _parse_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def telegram_transport(*, api_base=TELEGRAM_API, timeout=10, opener=None):
    """Build the HTTP transport: official host only (or loopback for tests), no redirects/proxies."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("A positive finite timeout is required")
    if api_base != TELEGRAM_API:
        _validate_loopback_url(
            api_base, "Only https://api.telegram.org (or a loopback test server) is allowed"
        )
    base = api_base.rstrip("/")
    opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    def call(token, method, fields, files):
        boundary, body = _multipart(fields, files)
        request = Request(
            f"{base}/bot{token}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        failure = None
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_API_RESPONSE + 1)
        except Exception as error:
            if hasattr(error, "close"):
                error.close()
            status = getattr(error, "code", None)
            failure = f"HTTP {status}" if status else type(error).__name__
        if failure:  # raised outside the except block so no URL-bearing context is chained
            raise RuntimeError(f"Telegram {method} request failed ({failure})")
        if len(raw) > MAX_API_RESPONSE:
            raise RuntimeError(f"Telegram {method} response too large")
        result = _parse_json(raw)
        if result is None:
            raise RuntimeError(f"Telegram {method} returned invalid JSON")
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram {method} returned an invalid response")
        return result

    return call


def validate_profile_jpeg(data):
    """Return the bytes when they are a square JPEG within the size limit, else raise."""
    if not isinstance(data, bytes) or not data.startswith(b"\xff\xd8") or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("JPEG required, maximum 2 MiB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG":
                raise ValueError("JPEG required")
            image.load()
            if image.width != image.height:
                raise ValueError("Square profile image required")
    except (OSError, SyntaxError) as error:
        raise ValueError("Invalid JPEG") from error
    return data


class TelegramBotProfileAdapter:
    """Sets the profile photo of the bot itself (Bot API setMyProfilePhoto), never a person."""

    def __init__(self, token_provider, *, transport=None, expected_bot_id=None):
        self._token_provider = token_provider
        self._transport = transport or telegram_transport()
        self.expected_bot_id = expected_bot_id

    def _call(self, token, method, fields, files):
        response = self._transport(token, method, fields, files)
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError(f"Telegram {method} was not accepted")
        return response.get("result")

    def apply(self, jpg_path):
        data = validate_profile_jpeg(Path(jpg_path).read_bytes())
        token = self._token_provider()
        me = self._call(token, "getMe", {}, {})
        if not isinstance(me, dict) or me.get("is_bot") is not True:
            raise RuntimeError("Telegram identity is not a bot account")
        if self.expected_bot_id is not None and me.get("id") != self.expected_bot_id:
            raise RuntimeError("Unexpected bot identity")
        fields = {"photo": json.dumps({"type": "static", "photo": "attach://photo"})}
        if self._call(token, "setMyProfilePhoto", fields, {"photo": ("profile.jpg", data)}) is not True:
            raise RuntimeError("Telegram setMyProfilePhoto was not accepted")
        return {
            "channel": "telegram-bot",
            "target": "bot-self",
            "acknowledged": True,
            "verified": False,
            "verification": "unverified-ack-only",
        }


def publish_to_catalog(slug, jpg_path, catalog_dir):
    """Copy a validated square JPEG to ``<catalog_dir>/<slug>.jpg`` for a bridge that reads it by slug."""
    from mascot_wardrobe.channels import _atomic, _slug

    _slug(slug)
    catalog = Path(catalog_dir)
    if catalog.is_symlink() or not catalog.is_dir() or catalog.resolve() != catalog.absolute():
        raise ValueError("The approved photo directory must be an existing real directory")
    catalog = catalog.resolve()
    source = Path(jpg_path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Missing or unsafe source image")
    with source.open("rb") as stream:
        data = validate_profile_jpeg(stream.read(MAX_IMAGE_BYTES + 1))
    target = catalog / (slug + ".jpg")
    if target.is_symlink() or target.resolve().parent != catalog:
        raise ValueError("Unsafe catalog target")
    if target.is_file() and target.stat().st_size <= MAX_IMAGE_BYTES and target.read_bytes() == data:
        return {"slug": slug, "action": "unchanged", "path": str(target)}
    if target.exists() and not target.is_file():
        raise ValueError("Unsafe catalog target")
    _atomic(target, data)
    return {"slug": slug, "action": "copied", "path": str(target)}


MAX_BRIDGE_RESPONSE = 3 * 1024 * 1024
HEADER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}\Z")
HEADER_VALUE = re.compile(r"[\x21-\x7e]{1,128}\Z")
FORBIDDEN_HEADERS = frozenset({"origin", "host", "content-length", "content-type", "cookie", "authorization"})


class HermesWhatsAppProfileAdapter:
    """Updates the connected WhatsApp account itself through the local Hermes/Baileys bridge.

    The bridge listens on loopback only, so this adapter must run on the Hermes host (or against
    an explicitly configured loopback endpoint such as a tunnel you opened yourself). It sends
    only a slug; the bridge reads ``<slug>.jpg`` from its own approved photo directory.
    """

    def __init__(self, base_url, *, bridge_header, timeout=10, opener=None):
        self.base_url = self._validate_url(base_url)
        self._header = self._validate_header(bridge_header)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("A positive finite timeout is required")
        self.timeout = timeout
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _validate_url(base_url):
        return _validate_loopback_url(
            base_url, "The bridge must be plain HTTP on a literal loopback IP with an explicit port"
        )

    @staticmethod
    def _validate_header(value):
        if not isinstance(value, str) or ":" not in value:
            raise ValueError("Bridge header must look like 'Name: value'")
        name, _, content = value.partition(":")
        name, content = name.strip(), content.strip()
        if (
            not HEADER_NAME.fullmatch(name)
            or name.lower() in FORBIDDEN_HEADERS
            or not HEADER_VALUE.fullmatch(content)
        ):
            raise ValueError("Invalid bridge header")
        return name, content

    def _request(self, method, path, payload=None):
        headers = {self._header[0]: self._header[1]}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, method=method, headers=headers)
        failure = None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BRIDGE_RESPONSE + 1)
        except Exception as error:
            if hasattr(error, "close"):
                error.close()
            status = getattr(error, "code", None)
            failure = f"HTTP {status}" if status else type(error).__name__
        if failure:
            raise RuntimeError(f"Bridge {method} failed ({failure})")
        if len(raw) > MAX_BRIDGE_RESPONSE:
            raise RuntimeError("Oversized bridge response")
        result = _parse_json(raw)
        if result is None:
            raise RuntimeError("Invalid JSON from the bridge")
        if not isinstance(result, dict):
            raise RuntimeError("Invalid bridge response")
        return result

    @staticmethod
    def _validate_readback(observed, slug):
        if observed.get("target") != "self":
            raise RuntimeError("Readback does not confirm the connected account itself")
        if observed.get("slug", slug) != slug:
            raise RuntimeError("Readback slug mismatch")
        if type(observed.get("matches")) is not bool:
            raise RuntimeError("Readback lacks a boolean comparison result")
        image, digest = observed.get("imageBase64"), observed.get("remoteSha256")
        if observed["matches"] or image is not None or digest is not None:
            try:
                if not isinstance(image, str) or len(image) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                    raise ValueError("missing or oversized image")
                data = base64.b64decode(image, validate=True)
                if not data or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("inconsistent hash")
            except (ValueError, TypeError):
                raise RuntimeError("Inconsistent readback image and hash") from None
        return observed["matches"], digest

    def sync(self, slug):
        from mascot_wardrobe.channels import _slug

        _slug(slug)
        path = "/profile-picture?" + urlencode({"slug": slug})
        matches, digest = self._validate_readback(self._request("GET", path), slug)
        action = "unchanged"
        if not matches:
            ack = self._request("POST", "/profile-picture", {"slug": slug})
            if ack.get("success") is not True or ack.get("target") != "self":
                raise RuntimeError("Upload not acknowledged for the connected account itself")
            action = "updated"
            matches, digest = self._validate_readback(self._request("GET", path), slug)
            if not matches:
                raise RuntimeError("Upload acknowledged but readback is not verified")
        return {
            "channel": "whatsapp-hermes-bridge",
            "slug": slug,
            "action": action,
            "acknowledged": True,
            "verified": True,
            "target": "self",
            "sandbox": False,
            "remote_sha256": digest,
            "verification": "bridge-readback-match",
        }
