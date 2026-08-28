"""Coherent browser-header middleware for Scrapy."""

from __future__ import annotations

DEFAULT_USER_AGENTS = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
)


class BrowserFingerprintMiddleware:
    """Rotate a User-Agent and optional referer per request.

    This changes ordinary HTTP headers only. Use ``CurlDownloadHandler`` when
    a request also needs a browser TLS profile.
    """

    def __init__(self, crawler):
        self.crawler = crawler
        settings = crawler.settings
        values = settings.get("FINGERPRINT_USER_AGENTS")
        if isinstance(values, str):
            values = [value.strip() for value in values.split(",") if value.strip()]
        self.agents = tuple(values or DEFAULT_USER_AGENTS)
        self.default_referer = settings.get("FINGERPRINT_DEFAULT_REFERER")
        self.connection_close = settings.getbool("FINGERPRINT_CONNECTION_CLOSE", True)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_request(self, request, spider=None):
        if request.meta.get("fingerprint") is False:
            return
        stats = self.crawler.stats
        index = stats.get_value("fingerprint_rotations", 0)
        request.headers["User-Agent"] = self.agents[index % len(self.agents)]
        if self.default_referer and not request.headers.get("Referer"):
            request.headers["Referer"] = self.default_referer
        if self.connection_close:
            request.headers["Connection"] = "close"
        return

    def process_response(self, request, response, spider=None):
        self.crawler.stats.inc_value("fingerprint_rotations")
        return response
