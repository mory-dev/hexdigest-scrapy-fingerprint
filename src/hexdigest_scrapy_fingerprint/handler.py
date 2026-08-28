"""Optional curl_cffi browser transport for Scrapy.

The handler is opt-in per request. Scrapy remains responsible for cookies,
redirects, retries, compression, and robots.txt; this component only replaces
the low-level HTTP request for requests a spider marks with ``meta["curl"]``
or for every request when ``CURL_IMPERSONATE`` is set.
"""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import quote, urlsplit, urlunsplit

from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler
from scrapy.http import HtmlResponse
from scrapy.utils.reactor import is_asyncio_reactor_installed

from ._compat import ASYNC_API

if not ASYNC_API:  # pragma: no cover - selected by Scrapy < 2.14
    from twisted.internet import threads


_HOP_BY_HOP_PROXY_HEADERS = {
    "proxy-authorization",
    "proxy-authenticate",
    "proxy-connection",
}
_OPTION_KEYS = {
    "impersonate",
    "ja3",
    "akamai",
    "extra_fp",
    "http_version",
    "verify",
    "default_headers",
    "raw_headers",
}


def _header_pairs(request) -> list[tuple[str, str]]:
    """Copy request headers while never forwarding proxy credentials."""

    pairs: list[tuple[str, str]] = []
    for name, values in request.headers.items():
        key = name.decode() if isinstance(name, bytes) else str(name)
        if key.lower() in _HOP_BY_HOP_PROXY_HEADERS:
            continue
        for value in values:
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            pairs.append((key, str(value)))
    return pairs


def _proxy_with_header_credentials(request, encoding: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Move Scrapy's Basic proxy auth header into the proxy URL for curl."""

    proxy = request.meta.get("proxy")
    pairs = _header_pairs(request)
    auth = None
    for name, value in request.headers.items():
        key = name.decode() if isinstance(name, bytes) else str(name)
        if key.lower() != "proxy-authorization":
            continue
        raw = value[0] if value else b""
        raw = raw.decode("latin-1") if isinstance(raw, bytes) else str(raw)
        if raw.lower().startswith("basic "):
            try:
                auth = base64.b64decode(raw[6:], validate=True).decode(encoding)
            except (ValueError, UnicodeError):
                auth = None
        break
    if proxy and auth and "@" not in urlsplit(proxy).netloc:
        username, separator, password = auth.partition(":")
        if separator:
            parsed = urlsplit(proxy)
            authority = f"{quote(username, safe='')}:{quote(password, safe='')}@{parsed.hostname}"
            if parsed.port:
                authority += f":{parsed.port}"
            proxy = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, parsed.fragment))
    return proxy, pairs


class CurlDownloadHandler(HTTP11DownloadHandler):
    """Download selected requests through a browser TLS/HTTP profile."""

    lazy = False

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def __init__(self, crawler):
        if ASYNC_API:
            super().__init__(crawler)
        else:
            super().__init__(crawler.settings, crawler)
        if ASYNC_API and not is_asyncio_reactor_installed():
            raise ValueError(
                "CurlDownloadHandler requires the asyncio Twisted reactor; "
                "set TWISTED_REACTOR to "
                "'twisted.internet.asyncioreactor.AsyncioSelectorReactor'"
            )
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise RuntimeError(
                "CurlDownloadHandler requires curl_cffi; install "
                "hexdigest-scrapy-fingerprint[curl]"
            ) from exc
        self._requests = curl_requests
        settings = crawler.settings
        self._default_impersonate = settings.get("CURL_IMPERSONATE")
        self._verify = settings.getbool("CURL_VERIFY", False)
        self._timeout = settings.getfloat("DOWNLOAD_TIMEOUT", 30)
        self._auth_encoding = settings.get("HTTPPROXY_AUTH_ENCODING", "utf-8")
        self._default_headers = settings.get("CURL_DEFAULT_HEADERS")
        if self._default_headers is not None:
            self._default_headers = settings.getbool("CURL_DEFAULT_HEADERS")

    def _options(self, request) -> dict:
        value = request.meta.get("curl")
        options = {} if value in (True, None) else dict(value)
        unknown = set(options) - _OPTION_KEYS
        if unknown:
            raise ValueError(f"unknown curl option(s): {sorted(unknown)}")
        if "impersonate" not in options and self._default_impersonate:
            options["impersonate"] = self._default_impersonate
        options.setdefault("verify", self._verify)
        if self._default_headers is not None:
            options.setdefault("default_headers", self._default_headers)
        return options

    def _handles(self, request) -> bool:
        if "curl" in request.meta:
            return request.meta["curl"] is not False
        return bool(self._default_impersonate)

    def _fetch(self, request):
        options = self._options(request)
        proxy, headers = _proxy_with_header_credentials(request, self._auth_encoding)
        raw_headers = options.pop("raw_headers", None)
        if raw_headers is not None:
            headers = [
                (str(name), str(value))
                for name, value in raw_headers
                if str(name).lower() not in _HOP_BY_HOP_PROXY_HEADERS
            ]
        kwargs = {
            "headers": headers,
            "data": request.body or None,
            "timeout": request.meta.get("download_timeout", self._timeout),
            # Scrapy's RedirectMiddleware should enforce redirect and offsite
            # policy; curl must not follow redirects internally.
            "allow_redirects": False,
            **options,
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        method = getattr(self._requests, request.method.lower(), None)
        if method is None:
            response = self._requests.request(request.method, request.url, **kwargs)
        else:
            response = method(request.url, **kwargs)
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "transfer-encoding"}
        }
        return HtmlResponse(
            url=str(response.url),
            status=response.status_code,
            headers=response_headers,
            body=response.content,
            request=request,
            encoding=response.encoding or "utf-8",
        )

    if ASYNC_API:

        async def download_request(self, request, spider=None):
            if self._handles(request):
                return await asyncio.to_thread(self._fetch, request)
            return await super().download_request(request)

        async def close(self):
            await super().close()

    else:

        def download_request(self, request, spider=None):
            if self._handles(request):
                return threads.deferToThread(self._fetch, request)
            return super().download_request(request, spider)

        def close(self):
            return super().close()
