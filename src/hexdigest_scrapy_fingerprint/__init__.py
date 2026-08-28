"""Browser TLS fingerprints and adaptive proxy fallback for Scrapy."""

from .fingerprint import BrowserFingerprintMiddleware
from .handler import CurlDownloadHandler
from .middleware import AdaptiveProxyMiddleware, registrable_domain

__all__ = [
    "AdaptiveProxyMiddleware",
    "BrowserFingerprintMiddleware",
    "CurlDownloadHandler",
    "registrable_domain",
]
__version__ = "0.1.0"
