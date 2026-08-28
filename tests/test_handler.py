from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import ClassVar

from scrapy import Request
from scrapy.settings import Settings

from hexdigest_scrapy_fingerprint.fingerprint import BrowserFingerprintMiddleware
from hexdigest_scrapy_fingerprint.handler import (
    CurlDownloadHandler,
    _proxy_with_header_credentials,
)


class Stats:
    def __init__(self):
        self.values = {}

    def get_value(self, key, default=None):
        return self.values.get(key, default)

    def inc_value(self, key, count=1, start=0, spider=None):
        self.values[key] = self.values.get(key, start) + count


def crawler(settings=None):
    return SimpleNamespace(
        settings=Settings(settings or {}),
        stats=Stats(),
        logger=None,
    )


def test_proxy_basic_credentials_are_removed_from_origin_headers():
    token = base64.b64encode(b"user:p%40ss").decode()
    request = Request(
        "https://example.com/item",
        headers={"Proxy-Authorization": f"Basic {token}", "Accept": "text/html"},
        meta={"proxy": "http://proxy.example:8080"},
    )
    proxy, headers = _proxy_with_header_credentials(request, "utf-8")
    # A percent sign already present in the decoded header is literal and is
    # not unquoted a second time.
    assert proxy == "http://user:p%2540ss@proxy.example:8080"
    assert all(name.lower() != "proxy-authorization" for name, _ in headers)


def test_curl_fetch_disables_internal_redirects_and_strips_raw_proxy_auth():
    captured = {}

    class FakeResponse:
        url: ClassVar[str] = "https://example.com/item"
        status_code: ClassVar[int] = 200
        headers: ClassVar[dict] = {"content-type": "text/html"}
        content: ClassVar[bytes] = b"<title>ok</title>"
        encoding: ClassVar[str] = "utf-8"

    class FakeRequests:
        @staticmethod
        def get(url, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return FakeResponse()

    handler = object.__new__(CurlDownloadHandler)
    handler._requests = FakeRequests
    handler._default_impersonate = None
    handler._verify = True
    handler._timeout = 10
    handler._auth_encoding = "utf-8"
    handler._default_headers = None
    token = base64.b64encode(b"u:p").decode()
    request = Request(
        "https://example.com/item",
        headers={"Proxy-Authorization": f"Basic {token}"},
        meta={
            "proxy": "http://proxy.example:8080",
            "curl": {"impersonate": "chrome", "raw_headers": [("Proxy-Authorization", "leak")]},
        },
    )
    response = handler._fetch(request)
    assert response.status == 200
    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["kwargs"]["proxies"] == {
        "http": "http://u:p@proxy.example:8080",
        "https": "http://u:p@proxy.example:8080",
    }
    assert all(name.lower() != "proxy-authorization" for name, _ in captured["kwargs"]["headers"])


def test_fingerprint_middleware_rotates_and_respects_opt_out():
    c = crawler({"FINGERPRINT_USER_AGENTS": ["agent-a", "agent-b"]})
    middleware = BrowserFingerprintMiddleware(c)
    first = Request("https://example.com/one")
    second = Request("https://example.com/two", meta={"fingerprint": False})
    middleware.process_request(first)
    middleware.process_request(second)
    assert first.headers["User-Agent"] == b"agent-a"
    assert "User-Agent" not in second.headers


def test_handler_opt_in_and_global_default():
    handler = object.__new__(CurlDownloadHandler)
    handler._default_impersonate = None
    assert handler._handles(Request("https://example.com", meta={"curl": {}}))
    assert not handler._handles(Request("https://example.com", meta={"curl": False}))
    handler._default_impersonate = "chrome"
    assert handler._handles(Request("https://example.com"))
