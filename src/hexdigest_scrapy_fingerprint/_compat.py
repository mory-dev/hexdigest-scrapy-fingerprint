"""Compatibility fact shared by the download handler."""

import inspect

from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler

ASYNC_API = inspect.iscoroutinefunction(HTTP11DownloadHandler.download_request)
