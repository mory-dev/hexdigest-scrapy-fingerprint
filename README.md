# HexDigest Scrapy Fingerprint

[![CI](https://github.com/mory-dev/hexdigest-scrapy-fingerprint/actions/workflows/ci.yml/badge.svg)](https://github.com/mory-dev/hexdigest-scrapy-fingerprint/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/hexdigest-scrapy-fingerprint)](https://pypi.org/project/hexdigest-scrapy-fingerprint/)
[![Python](https://img.shields.io/pypi/pyversions/hexdigest-scrapy-fingerprint)](https://pypi.org/project/hexdigest-scrapy-fingerprint/)
[![License](https://img.shields.io/github/license/mory-dev/hexdigest-scrapy-fingerprint)](LICENSE)

Browser TLS fingerprints and adaptive proxy fallback for Scrapy. The package
combines a `curl_cffi` download handler, coherent browser headers, and
direct-first proxy escalation for crawlers that need an explicit transport
policy.

This is the reusable transport layer extracted from the production crawler
infrastructure behind [HexDigest](https://hexdigest.com), a marketplace
intelligence service built from public listings.

## What it solves

Some origins respond differently to a Scrapy/Twisted TLS handshake than to a
real browser profile, even when the IP, URL, and application headers are the
same. Other origins are happy with direct requests, making an always-on
residential proxy an unnecessary cost. This package makes both choices
explicit and observable:

```text
request → normal Scrapy HTTP handler
       → optional curl_cffi browser TLS profile
       → optional direct-first proxy fallback after a configured refusal
```

The components are independent. Adopt the download handler, header middleware,
adaptive proxy middleware, or any combination of them.

## Install

```bash
pip install "hexdigest-scrapy-fingerprint[curl]"
```

The base package includes the adaptive proxy and header middleware. The `curl`
extra installs `curl-cffi` for browser TLS profiles.

## Browser TLS profiles

Register the handler for HTTP and HTTPS, then opt in per request:

```python
# settings.py
DOWNLOAD_HANDLERS = {
    "http": "hexdigest_scrapy_fingerprint.CurlDownloadHandler",
    "https": "hexdigest_scrapy_fingerprint.CurlDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
```

```python
import scrapy


class ProductSpider(scrapy.Spider):
    name = "products"

    def start_requests(self):
        yield scrapy.Request(
            "https://example.com/products/123",
            meta={"curl": {"impersonate": "chrome"}},
        )

    def parse(self, response):
        yield {"url": response.url, "title": response.css("title::text").get()}
```

Set `CURL_IMPERSONATE = "chrome"` to use a browser profile for every request;
`meta["curl"] = False` opts an individual request out. Supported per-request
options include `impersonate`, `ja3`, `akamai`, `extra_fp`, `http_version`,
`verify`, `default_headers`, and ordered `raw_headers`.

Redirects remain under Scrapy’s control. Proxy credentials from Scrapy’s
`Proxy-Authorization` header are removed from origin headers and supplied only
to the proxy leg. Unknown curl options fail loudly instead of being ignored.

## Browser header middleware

```python
DOWNLOADER_MIDDLEWARES = {
    "hexdigest_scrapy_fingerprint.BrowserFingerprintMiddleware": 400,
}
FINGERPRINT_DEFAULT_REFERER = "https://example.com/"
```

The middleware rotates a small, configurable set of coherent desktop user
agents, optionally supplies a referer, and can close each connection. Set
`meta["fingerprint"] = False` for a request that should keep its headers.

## Adaptive proxy fallback

Place the middleware immediately before Scrapy’s built-in proxy middleware:

```python
import os

DOWNLOADER_MIDDLEWARES = {
    "hexdigest_scrapy_fingerprint.AdaptiveProxyMiddleware": 749,
}
ADAPTIVE_PROXY_URL = os.environ["PROXY_URL"]
ADAPTIVE_PROXY_STATE_PATH = ".scrapy/adaptive-proxy.sqlite3"  # optional
ADAPTIVE_PROXY_TTL_SECONDS = 86_400
```

Eligible `GET` and `HEAD` requests go direct first. A configured `403`, `429`,
or `503` response is retried once through the proxy; the public-suffix-aware
domain is remembered until the TTL expires. A refusal received from the proxy
is returned to Scrapy’s normal retry/error handling and never escalates in a
loop. Existing `request.meta["proxy"]` values are preserved.

The middleware records `adaptive_proxy/direct_requests`,
`proxied_requests`, `escalations`, `blocked_via_proxy`, and `bypassed` stats.
State is memory-only unless `ADAPTIVE_PROXY_STATE_PATH` is set. Configure
`ADAPTIVE_PROXY_STATUS_CODES`, `ADAPTIVE_PROXY_METHODS`, and
`ADAPTIVE_PROXY_DOMAIN_SCOPE` (`registrable` or `host`) for a different policy.

## Responsible use

This package does not disable robots.txt, solve CAPTCHAs, rotate identities, or
grant permission to crawl a site. Respect each origin’s robots policy, terms,
rate limits, privacy requirements, and applicable law. Use a proxy only when
you are authorized to do so.

## TLS-fingerprint validation

To compare transports, point a local test crawl at a diagnostic endpoint such
as your own server and record status, negotiated protocol, and request headers.
Do not put third-party credentials or private URLs in fixtures. The package’s
automated tests use local fakes and do not contact a marketplace or proxy.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
pytest
ruff check .
python -m build
twine check dist/*
```

## About HexDigest

HexDigest compiles daily market intelligence from public marketplace listings.
If you need maintained datasets rather than crawler infrastructure, visit
[hexdigest.com](https://hexdigest.com/?utm_source=github&utm_medium=referral&utm_campaign=hexdigest-scrapy-fingerprint).

## License

MIT. See [LICENSE](LICENSE).
