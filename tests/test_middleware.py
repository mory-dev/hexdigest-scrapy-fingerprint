from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from scrapy import Request
from scrapy.http import Response
from scrapy.settings import Settings

from hexdigest_scrapy_fingerprint import AdaptiveProxyMiddleware, registrable_domain


class Stats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1, start=0, spider=None):
        self.values[key] = self.values.get(key, start) + count


def make_middleware(tmp_path: Path, **overrides):
    values = {
        "ADAPTIVE_PROXY_URL": "http://proxy.example:8080",
        "ADAPTIVE_PROXY_STATE_PATH": str(tmp_path / "state.sqlite3"),
    }
    values.update(overrides)
    stats = Stats()
    crawler = SimpleNamespace(settings=Settings(values), stats=stats, logger=logging.getLogger("test"))
    return AdaptiveProxyMiddleware(crawler), stats


def test_registrable_domains_group_seller_hosts():
    assert registrable_domain("https://seller.gumroad.com/l/item") == "gumroad.com"
    assert registrable_domain("https://shop.example.co.uk/item") == "example.co.uk"


def test_first_request_is_direct_then_escalates(tmp_path):
    middleware, stats = make_middleware(tmp_path)
    request = Request("https://seller.gumroad.com/l/item")
    middleware.process_request(request)
    assert "proxy" not in request.meta
    assert stats.values["adaptive_proxy/direct_requests"] == 1

    retry = middleware.process_response(request, Response(request.url, status=403))
    assert retry.meta["proxy"] == "http://proxy.example:8080"
    assert retry.meta["_adaptive_proxy_attempted"] is True
    assert retry.dont_filter is True
    assert stats.values["adaptive_proxy/escalations"] == 1


def test_remembered_domain_is_proxied_without_direct_request(tmp_path):
    middleware, stats = make_middleware(tmp_path)
    first = Request("https://seller.gumroad.com/l/one")
    middleware.process_response(first, Response(first.url, status=429))
    second = Request("https://another.gumroad.com/l/two")
    middleware.process_request(second)
    assert second.meta["proxy"] == "http://proxy.example:8080"
    assert stats.values["adaptive_proxy/proxied_requests"] == 1


def test_proxy_refusal_does_not_loop(tmp_path):
    middleware, stats = make_middleware(tmp_path)
    request = Request(
        "https://example.com/item",
        meta={"proxy": "http://existing.proxy:8080", "_adaptive_proxy_attempted": True},
    )
    result = middleware.process_response(request, Response(request.url, status=403))
    assert result is not request
    assert result.status == 403
    assert stats.values["adaptive_proxy/blocked_via_proxy"] == 1


def test_bypass_and_unsafe_methods_are_untouched(tmp_path):
    middleware, stats = make_middleware(tmp_path)
    bypass = Request("https://example.com/item", meta={"adaptive_proxy": False})
    middleware.process_request(bypass)
    post = Request("https://example.com/item", method="POST")
    middleware.process_request(post)
    assert "proxy" not in bypass.meta
    assert "proxy" not in post.meta
    assert stats.values["adaptive_proxy/bypassed"] == 1


def test_existing_proxy_is_preserved(tmp_path):
    middleware, _ = make_middleware(tmp_path)
    request = Request("https://example.com/item", meta={"proxy": "http://caller.proxy:1"})
    middleware.process_request(request)
    assert request.meta["proxy"] == "http://caller.proxy:1"


def test_sqlite_state_survives_new_middleware(tmp_path):
    first, _ = make_middleware(tmp_path)
    request = Request("https://example.com/item")
    first.process_response(request, Response(request.url, status=503))
    second, _ = make_middleware(tmp_path)
    next_request = Request("https://example.com/next")
    second.process_request(next_request)
    assert next_request.meta["proxy"] == "http://proxy.example:8080"


def test_expired_state_is_not_used(tmp_path):
    middleware, _ = make_middleware(tmp_path, ADAPTIVE_PROXY_TTL_SECONDS=0)
    request = Request("https://example.com/item")
    middleware.process_response(request, Response(request.url, status=403))
    next_request = Request("https://example.com/next")
    middleware.process_request(next_request)
    assert "proxy" not in next_request.meta


def test_state_errors_degrade_to_memory(tmp_path):
    bad_path = tmp_path / "not-a-directory"
    bad_path.write_text("not a sqlite database", encoding="utf-8")
    middleware, _ = make_middleware(tmp_path, ADAPTIVE_PROXY_STATE_PATH=str(bad_path / "state.sqlite3"))
    request = Request("https://example.com/item")
    retry = middleware.process_response(request, Response(request.url, status=403))
    assert retry.meta["proxy"] == "http://proxy.example:8080"


def test_unknown_status_does_not_escalate(tmp_path):
    middleware, stats = make_middleware(tmp_path)
    request = Request("https://example.com/item")
    result = middleware.process_response(request, Response(request.url, status=401))
    assert result.status == 401
    assert "adaptive_proxy/escalations" not in stats.values
