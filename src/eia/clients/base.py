"""Rate-limited HTTP client wrapper.

Wraps `httpx.Client` with:
    * configurable per-second rate limit
    * tenacity-driven exponential backoff on 429/5xx
    * optional response caching to data/raw/_cache/ for replayability

This is shared infrastructure for every API-backed source (Ticketmaster,
Setlist.fm, RunSignUp, Census, BLS API, FRED, etc.).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class RateLimitedClient:
    """Polite HTTP client with rate limiting, retries, and optional caching."""

    def __init__(
        self,
        base_url: str,
        *,
        requests_per_second: float = 1.0,
        timeout_s: float = 30.0,
        default_headers: dict[str, str] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last_request_at: float = 0.0
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            headers=default_headers or {},
        )
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- internal ----

    def _throttle(self) -> None:
        if self.min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _cache_key(method: str, url: str, params: dict[str, Any] | None) -> str:
        payload = json.dumps(
            {"method": method, "url": url, "params": params or {}},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        self._throttle()
        resp = self._client.request(
            method,
            path,
            params=params,
            headers=headers,
            json=json_body,
        )
        # 429 / 5xx -> retry. 4xx other than 429 -> raise immediately.
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    # ---- public ----

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = False,
    ) -> Any:
        if use_cache and self.cache_dir:
            key = self._cache_key("GET", path, params)
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                return json.loads(cache_file.read_text())
        resp = self._request("GET", path, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if use_cache and self.cache_dir:
            (self.cache_dir / f"{key}.json").write_text(json.dumps(data))
        return data

    def get_bytes(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> bytes:
        resp = self._request("GET", path, params=params)
        resp.raise_for_status()
        return resp.content

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RateLimitedClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
