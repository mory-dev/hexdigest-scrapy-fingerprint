"""Direct-first proxy fallback middleware for Scrapy.

The middleware sends an eligible request directly.  When the origin returns a
configured refusal status, the request is retried once through a proxy and the
domain is remembered for a configurable TTL.  An optional SQLite state file
lets that decision survive a worker restart; memory-only operation is the
default.

This is a transport-cost and availability tool, not an anti-bot bypass.  A
caller remains responsible for robots.txt, rate limits, terms of service, and
the law applicable to its crawl.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import tldextract
from scrapy.exceptions import NotConfigured

DEFAULT_BLOCKED_STATUSES = frozenset({403, 429, 503})
DEFAULT_METHODS = frozenset({"GET", "HEAD"})
STATE_TABLE = "adaptive_proxy_domains"

# Never allow tldextract to fetch a suffix list at runtime.  The package ships
# with the library's bundled snapshot, which keeps crawls deterministic and
# usable in offline CI environments.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def _registered_domain(url: str) -> str:
    parts = _EXTRACT(url)
    # tldextract renamed this property; supporting both avoids needlessly
    # coupling callers to one minor release of the dependency.
    value = getattr(parts, "top_domain_under_public_suffix", "")
    if not value:
        value = getattr(parts, "registered_domain", "")
    if value:
        return value.lower()
    return (parts.fqdn or urlparse(url).hostname or "").lower()


def registrable_domain(url: str) -> str:
    """Return the public-suffix-aware domain used for shared proxy state."""

    return _registered_domain(url)


def _setting_values(value, default: set[str]) -> set[str]:
    if value is None:
        return set(default)
    if isinstance(value, str):
        value = value.split(",")
    try:
        return {str(item).strip() for item in value if str(item).strip()}
    except TypeError:
        return set(default)


class _SQLiteState:
    """Tiny optional state backend; all operations are idempotent."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
            "domain TEXT PRIMARY KEY, marked_at REAL NOT NULL)"
        )
        return conn

    def load(self, now: float, ttl: float) -> dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT domain, marked_at FROM {STATE_TABLE} WHERE marked_at > ?",
                (now - ttl,),
            ).fetchall()
        return {str(domain): float(marked_at) for domain, marked_at in rows}

    def mark(self, domain: str, marked_at: float) -> None:
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {STATE_TABLE} (domain, marked_at) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET marked_at = excluded.marked_at",
                (domain, marked_at),
            )


class AdaptiveProxyMiddleware:
    """Escalate direct requests to a proxy only after a refusal.

    Enable at priority 749 so this decision runs immediately before Scrapy's
    built-in ``HttpProxyMiddleware`` at priority 750::

        DOWNLOADER_MIDDLEWARES = {
            "hexdigest_scrapy_fingerprint.AdaptiveProxyMiddleware": 749,
        }
        ADAPTIVE_PROXY_URL = os.environ["PROXY_URL"]
    """

    def __init__(self, crawler):
        self.crawler = crawler
        settings = crawler.settings
        self.proxy_url = settings.get("ADAPTIVE_PROXY_URL")
        if not self.proxy_url:
            raise NotConfigured("ADAPTIVE_PROXY_URL is not configured")

        raw_statuses = settings.get("ADAPTIVE_PROXY_STATUS_CODES")
        if raw_statuses is None:
            self.status_codes = set(DEFAULT_BLOCKED_STATUSES)
        else:
            self.status_codes = {
                int(item) for item in _setting_values(raw_statuses, set())
            }
        raw_methods = settings.get("ADAPTIVE_PROXY_METHODS")
        self.methods = {
            item.upper()
            for item in _setting_values(raw_methods, set(DEFAULT_METHODS))
        }
        self.ttl = max(0.0, float(settings.get("ADAPTIVE_PROXY_TTL_SECONDS", 86400)))
        self.domain_scope = str(
            settings.get("ADAPTIVE_PROXY_DOMAIN_SCOPE", "registrable")
        ).lower()
        if self.domain_scope not in {"registrable", "host"}:
            raise ValueError("ADAPTIVE_PROXY_DOMAIN_SCOPE must be registrable or host")

        state_path = settings.get("ADAPTIVE_PROXY_STATE_PATH")
        self._state = _SQLiteState(state_path) if state_path else None
        self._blocked: dict[str, float] = {}
        self._loaded = False
        self._state_warning_logged = False

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def _logger(self, spider=None):
        return getattr(spider, "logger", None) or getattr(self.crawler, "logger", None)

    def _warn_state(self, message: str, spider=None) -> None:
        if self._state_warning_logged:
            return
        self._state_warning_logged = True
        logger = self._logger(spider)
        if logger:
            logger.warning("adaptive proxy state disabled: %s", message)

    def _load(self, spider=None) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._state is None:
            return
        try:
            self._blocked.update(self._state.load(time.time(), self.ttl))
        except (OSError, sqlite3.Error) as exc:
            self._warn_state(str(exc), spider)

    def _domain(self, url: str) -> str:
        if self.domain_scope == "host":
            return (urlparse(url).hostname or "").lower()
        return registrable_domain(url)

    def _needs_proxy(self, domain: str, now: float | None = None) -> bool:
        marked = self._blocked.get(domain)
        if marked is None:
            return False
        if (time.time() if now is None else now) - marked >= self.ttl:
            self._blocked.pop(domain, None)
            return False
        return True

    def _remember(self, domain: str, spider=None) -> None:
        marked_at = time.time()
        self._blocked[domain] = marked_at
        if self._state is None:
            return
        try:
            self._state.mark(domain, marked_at)
        except (OSError, sqlite3.Error) as exc:
            self._warn_state(str(exc), spider)

    def _inc(self, key: str) -> None:
        stats = getattr(self.crawler, "stats", None)
        if stats is not None:
            stats.inc_value(f"adaptive_proxy/{key}")

    def process_request(self, request, spider=None):
        if request.meta.get("adaptive_proxy") is False:
            self._inc("bypassed")
            return
        if request.meta.get("proxy") or request.method.upper() not in self.methods:
            return
        self._load(spider)
        if self._needs_proxy(self._domain(request.url)):
            request.meta["proxy"] = self.proxy_url
            request.meta["_adaptive_proxy_attempted"] = True
            self._inc("proxied_requests")
        else:
            self._inc("direct_requests")
        return

    def process_response(self, request, response, spider=None):
        if not self.proxy_url or response.status not in self.status_codes:
            return response
        if request.method.upper() not in self.methods:
            return response
        # This covers both a caller-supplied proxy and a retry produced by this
        # middleware.  A refused proxy response is left for RetryMiddleware.
        if request.meta.get("proxy") or request.meta.get("_adaptive_proxy_attempted"):
            self._inc("blocked_via_proxy")
            return response

        self._load(spider)
        domain = self._domain(request.url)
        self._remember(domain, spider)
        retry = request.copy()
        retry.meta["proxy"] = self.proxy_url
        retry.meta["_adaptive_proxy_attempted"] = True
        retry.dont_filter = True
        self._inc("escalations")
        logger = self._logger(spider)
        if logger:
            logger.info(
                "adaptive proxy: %s returned %s direct; retrying through proxy for %s",
                request.url,
                response.status,
                domain,
            )
        return retry
