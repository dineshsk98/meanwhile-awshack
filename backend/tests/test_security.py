"""Signed media links, the media gate (directly and through the reverse proxy's auth check), the client address
behind the proxy, and the limit on wrong credentials."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from local.devserver import build_app
from meanwhile import config
from meanwhile.api import auth
from meanwhile.media_links import MediaLinks, secret_from

FAKE_KEY = "test-key-not-real"
KEY = {"X-Meanwhile-Key": FAKE_KEY}
PATH = "/media/thumbs/desk/20261013T100000000Z.jpg"


class Clock:
    """A clock the test moves by hand."""
    def __init__(self, at: float = 1_000_000.0):
        """Start at `at` seconds."""
        self.at = at

    def __call__(self) -> float:
        """The current time."""
        return self.at


def links(clock: Clock, ttl_s: float = 3600) -> MediaLinks:
    """Media links signed with a test secret on the given clock."""
    return MediaLinks(secret_from("a-test-key-for-signing"), ttl_s, clock)


def parts(url: str) -> tuple[str, str]:
    """A signed link split into its path and query string."""
    path, _, query = url.partition("?")
    return path, query


def test_a_signed_link_works_for_its_path_until_it_expires():
    """A link is valid for its own path for at least the TTL and at most 1.5 x TTL, then refused."""
    clock = Clock()
    signer = links(clock)
    path, query = parts(signer.sign(PATH))
    assert path == PATH and signer.valid(path, query)
    clock.at += 3600
    assert signer.valid(path, query)
    clock.at += 1801
    assert not signer.valid(path, query)


def test_tampered_other_path_and_missing_signatures_are_refused():
    """A changed signature or expiry, another path, a non-media path, or a link from another key is refused."""
    clock = Clock()
    signer = links(clock)
    path, query = parts(signer.sign(PATH))
    exp, sig = (p.split("=", 1)[1] for p in query.split("&"))
    assert not signer.valid(path, f"exp={exp}&sig={sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}")
    assert not signer.valid(path, f"exp={int(exp) + 3600}&sig={sig}")
    assert not signer.valid("/media/thumbs/desk/other.jpg", query)
    assert not signer.valid("/api/cameras", query)
    assert not signer.valid(path, "") and not signer.valid(path, f"exp={exp}") and not signer.valid(path, "exp=x&sig=y")
    other = MediaLinks(secret_from("another-key-for-signing"), 3600, clock)
    assert not other.valid(path, query)


def test_the_same_path_keeps_one_link_within_half_the_ttl():
    """Links stay the same within each half-TTL window (the clock starts on a window boundary), so images can be cached."""
    clock = Clock(1_800_000.0)
    signer = links(clock)
    first = signer.sign(PATH)
    clock.at += 1000
    assert signer.sign(PATH) == first
    clock.at += 1000
    assert signer.sign(PATH) != first


@pytest.fixture
def client(store):
    """A core with a fake key and one camera thumbnail on disk."""
    store.write_media(PATH.removeprefix("/media/"), b"jpeg bytes")
    return TestClient(build_app(store, api_key=FAKE_KEY))


def test_media_needs_a_signed_link_or_credentials(client, store):
    """A signed link loads without the key; the bare path, another path's link and an expired link are refused."""
    signed = store.media_url(PATH.removeprefix("/media/"))
    assert client.get(signed).content == b"jpeg bytes"
    assert client.get(PATH).status_code == 401
    assert client.get(PATH, headers=KEY).status_code == 200
    other = store.media_url("thumbs/desk/other.jpg")
    assert client.get(PATH + "?" + parts(other)[1]).status_code == 401
    store.links.clock = lambda: 10 ** 12
    assert client.get(signed).status_code == 401


def test_the_proxy_auth_check_accepts_a_signed_media_uri(client, store):
    """Caddy's forward_auth names the original request in X-Forwarded-Uri: a signed media link passes, the bare path,
    a link for another path or a signed link presented as a non-media path doesn't."""
    signed = store.media_url(PATH.removeprefix("/media/"))
    check = lambda uri: client.get("/api/auth/check", headers={"X-Forwarded-Uri": uri}).status_code
    assert check(signed) == 204
    assert check(PATH) == 401
    assert check("/media/thumbs/desk/other.jpg?" + parts(signed)[1]) == 401
    assert check("/api/cameras?" + parts(signed)[1]) == 401
    assert client.get("/api/auth/check", headers=KEY).status_code == 204


def test_the_client_address_comes_from_the_proxy_only_on_this_machine():
    """Behind Caddy (peer 127.0.0.1) the last X-Forwarded-For address is the client; from anywhere else the header
    is ignored."""
    forwarded = [(b"x-forwarded-for", b"198.51.100.7, 203.0.113.9")]
    assert auth.client_address({"client": ("127.0.0.1", 5000), "headers": forwarded}) == "203.0.113.9"
    assert auth.client_address({"client": ("192.0.2.4", 5000), "headers": forwarded}) == "192.0.2.4"
    assert auth.client_address({"client": ("127.0.0.1", 5000), "headers": []}) == "127.0.0.1"


def test_different_wrong_credentials_block_a_client_for_the_window():
    """At `tries` different wrong credentials a client is blocked until the oldest leaves the window; one stale
    token repeated counts once; requests without credentials count for nothing; clients are counted apart."""
    clock = Clock()
    failures = auth.FailedAuth(3, 60, clock)
    for _ in range(10):
        failures.failed("a", ["stale-token"])
        failures.failed("a", [])
    assert failures.blocked_s("a") == 0
    failures.failed("a", ["guess-1"])
    clock.at += 10
    failures.failed("a", ["guess-2"])
    assert failures.blocked_s("a") == pytest.approx(50) and failures.blocked_s("b") == 0
    clock.at += 51
    assert failures.blocked_s("a") == 0


def test_a_blocked_client_gets_429_even_with_the_key(store, monkeypatch):
    """Through the gate: after too many different wrong keys from one address, that address gets 429 with
    Retry-After (the key included) while another address still gets in."""
    monkeypatch.setattr(config, "AUTH_FAIL_TRIES", 3)
    app = build_app(store, api_key=FAKE_KEY)
    attacker = TestClient(app, client=("192.0.2.10", 5000))
    for n in range(3):
        assert attacker.get("/api/cameras", headers={"X-Meanwhile-Key": f"guess-{n}"}).status_code == 401
    blocked = attacker.get("/api/cameras", headers=KEY)
    assert blocked.status_code == 429 and blocked.json()["error"] == "too_many_tries"
    assert int(blocked.headers["retry-after"]) > 0
    assert TestClient(app, client=("192.0.2.11", 5000)).get("/api/cameras", headers=KEY).status_code == 200


def test_proxied_clients_are_counted_by_their_forwarded_address(store, monkeypatch):
    """Behind the proxy every request comes from 127.0.0.1, so the gate counts the forwarded address instead."""
    monkeypatch.setattr(config, "AUTH_FAIL_TRIES", 2)
    proxy = TestClient(build_app(store, api_key=FAKE_KEY), client=("127.0.0.1", 5000))
    for n in range(2):
        proxy.get("/api/auth/check", headers={"X-Meanwhile-Key": f"guess-{n}", "X-Forwarded-For": "198.51.100.7"})
    assert proxy.get("/api/auth/check", headers={**KEY, "X-Forwarded-For": "198.51.100.7"}).status_code == 429
    assert proxy.get("/api/auth/check", headers={**KEY, "X-Forwarded-For": "198.51.100.8"}).status_code == 204
