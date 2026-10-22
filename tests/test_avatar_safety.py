"""Own-account safety using an in-process fake bridge; no sockets or live runtime."""

import base64
import fcntl
import hashlib
import io
import json
from datetime import UTC, datetime
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from mascot_wardrobe.channels.live import HermesWhatsAppProfileAdapter
from tests.test_live_whatsapp import jpeg

NOW = datetime(2026, 10, 22, tzinfo=UTC)


class MemoryBridge:
    def __init__(self, *, match_after=1):
        self.events = []
        self.old = jpeg("blue")
        self.desired = jpeg("red")
        self.current = self.old
        self.posts = 0
        self.pending = False
        self.slug = "sunny"
        self.polls = 0
        self.match_after = match_after
        self.on_post = lambda: None
        self.target = "self"
        self.failure = False

    def open(self, request, timeout):
        if self.failure:
            raise OSError("private bridge response must never escape")
        method = request.get_method()
        self.events.append(method)
        assert request.get_header("X-example-avatar") == "1"
        if method == "POST":
            assert urlsplit(request.full_url).path == "/profile-picture"
            assert json.loads(request.data) == {"slug": self.slug}
            self.on_post()
            self.posts += 1
            self.pending = True
            result = {"success": True, "target": self.target}
        else:
            assert urlsplit(request.full_url).path == "/profile-picture"
            assert parse_qs(urlsplit(request.full_url).query) == {"slug": [self.slug]}
            if self.pending:
                self.polls += 1
                if self.polls >= self.match_after:
                    self.current = self.desired
                    self.pending = False
            result = {
                "target": self.target,
                "slug": self.slug,
                "matches": self.current == self.desired,
                "imageBase64": base64.b64encode(self.current).decode(),
                "remoteSha256": hashlib.sha256(self.current).hexdigest(),
            }
        return io.BytesIO(json.dumps(result).encode())


@pytest.fixture
def setup_sync(tmp_path):
    bridge = MemoryBridge()
    state = tmp_path / "state"
    adapter = HermesWhatsAppProfileAdapter(
        "http://127.0.0.1:1",
        bridge_header="X-Example-Avatar: 1",
        opener=bridge,
        state_dir=state,
        pause=lambda seconds: None,
        now=lambda: NOW,
    )
    return bridge, state, adapter


def test_backup_is_observed_photo_before_post_and_is_preserved_once(setup_sync):
    bridge, state, adapter = setup_sync
    bridge.on_post = lambda: (
        pytest.fail("missing original backup")
        if (state / "whatsapp-profile-before-sync.jpg").read_bytes() != bridge.old
        else None
    )
    result = adapter.sync("sunny")
    assert bridge.events == ["GET", "POST", "GET"]
    assert result["verified"] is True
    saved = json.loads((state / "avatar-sync.json").read_text())
    assert saved["remote_sha256"] == hashlib.sha256(bridge.desired).hexdigest()
    assert saved["checked_at_utc"] == NOW.isoformat()
    bridge.current = jpeg("green")
    adapter.sync("sunny")
    assert (state / "whatsapp-profile-before-sync.jpg").read_bytes() == bridge.old
    adapter.sync("sunny")
    assert bridge.posts == 2
    assert set(p.name for p in state.iterdir()) == {
        "avatar-sync.lock",
        "avatar-sync.json",
        "whatsapp-profile-before-sync.jpg",
    }


def test_three_polls_allow_eventual_consistency(setup_sync):
    bridge, state, adapter = setup_sync
    bridge.match_after = 3
    assert adapter.sync("sunny")["verified"] is True
    assert bridge.events == ["GET", "POST", "GET", "GET", "GET"]


def test_failed_readback_and_failed_atomic_replace_preserve_last_good(setup_sync):
    bridge, state, adapter = setup_sync
    adapter.sync("sunny")
    last = (state / "avatar-sync.json").read_bytes()
    bridge.current, bridge.match_after, bridge.polls = bridge.old, 99, 0
    with pytest.raises(RuntimeError, match="not verified"):
        adapter.sync("sunny")
    assert (state / "avatar-sync.json").read_bytes() == last
    assert bridge.polls == 3  # exactly three POST readbacks
    bridge.match_after = 0
    with mock.patch("os.replace", side_effect=OSError("synthetic atomic failure")):
        with pytest.raises(OSError):
            adapter.sync("sunny")
    assert (state / "avatar-sync.json").read_bytes() == last
    assert not list(state.glob(".avatar-*"))


def test_lock_contention_stops_all_requests_and_nonself_never_accesses_files(setup_sync):
    bridge, state, adapter = setup_sync
    with pytest.raises(ValueError, match="self"):
        adapter.sync("sunny", target="other")
    assert not state.exists()
    state.mkdir()
    with (state / "avatar-sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises((BlockingIOError, RuntimeError)):
            adapter.sync("sunny")
    assert bridge.events == []


@pytest.mark.parametrize(
    "filename", ["avatar-sync.lock", "avatar-sync.json", "whatsapp-profile-before-sync.jpg"]
)
def test_unsafe_state_symlinks_fail_before_post(setup_sync, tmp_path, filename):
    bridge, state, adapter = setup_sync
    state.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    (state / filename).symlink_to(sentinel)
    with pytest.raises(ValueError):
        adapter.sync("sunny")
    assert bridge.posts == 0
    assert sentinel.read_bytes() == b"untouched"


def test_invalid_remote_target_and_transport_errors_do_not_leak_or_upload(setup_sync):
    bridge, state, adapter = setup_sync
    bridge.target = "other"
    with pytest.raises(RuntimeError):
        adapter.sync("sunny")
    bridge.failure = True
    with pytest.raises(RuntimeError) as caught:
        adapter.sync("sunny")
    assert "private bridge" not in str(caught.value)
    assert caught.value.__context__ is None
    assert bridge.posts == 0
    assert not (state / "avatar-sync.json").exists()


def test_preflight_refuses_missing_backup_material_before_any_profile_change(setup_sync):
    bridge, state, adapter = setup_sync
    original_open = bridge.open

    def no_photo(request, timeout):
        response = original_open(request, timeout)
        payload = json.loads(response.read())
        payload.update(imageBase64=None, remoteSha256=None, matches=False)
        return io.BytesIO(json.dumps(payload).encode())

    bridge.open = no_photo
    with pytest.raises(RuntimeError, match="back up"):
        adapter.preflight("sunny")
    assert bridge.posts == 0
    assert not state.exists()
