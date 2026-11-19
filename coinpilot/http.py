"""Minimal JSON-over-HTTP helpers. Standard library only."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "CoinPilot/0.1 (paper trading)"

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    """Raised when a request cannot be completed."""


def _build_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    clean = {key: value for key, value in params.items() if value is not None}
    if not clean:
        return url
    query = urllib.parse.urlencode(clean)
    return f"{url}{'&' if '?' in url else '?'}{query}"


def request_json(
    method: str,
    url: str,
    params: dict | None = None,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 20,
    retries: int = 3,
):
    full_url = _build_url(url, params)
    data = None
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(full_url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:  # noqa: BLE001 - best effort only
                    pass
                raise HttpError(f"HTTP {exc.code} from {full_url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))

    raise HttpError(f"request failed after {retries + 1} attempts: {full_url}: {last_error}")


def get_json(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3, headers: dict | None = None):
    return request_json("GET", url, params=params, headers=headers, timeout=timeout, retries=retries)


def get_text(
    url: str,
    params: dict | None = None,
    timeout: int = 20,
    retries: int = 2,
    headers: dict | None = None,
    accept: str = "application/rss+xml, application/xml, text/xml, text/html, */*",
) -> str:
    """Fetch a page or feed and return it as text, for XML and HTML sources."""

    full_url = _build_url(url, params)
    request_headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(full_url, headers=request_headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                raise HttpError(f"HTTP {exc.code} from {full_url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))

    raise HttpError(f"request failed after {retries + 1} attempts: {full_url}: {last_error}")


def post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 60, retries: int = 1):
    return request_json("POST", url, body=body, headers=headers, timeout=timeout, retries=retries)
