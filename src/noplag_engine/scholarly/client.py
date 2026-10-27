"""Rate-limited async HTTP client for scholarly APIs.

All scholarly API clients share this base to:
- Enforce per-source rate limits
- Handle retries with exponential backoff
- Provide consistent error handling and logging
- Respect API terms of service
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("noplag.scholarly.client")


@dataclass
class RateLimiter:
    """Token-bucket rate limiter for API calls."""
    max_per_second: float
    _tokens: float = 0.0
    _last_refill: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = self.max_per_second
        self._last_refill = time.monotonic()

    async def acquire(self) -> None:
        """Wait until a request token is available."""
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self.max_per_second,
                self._tokens + elapsed * self.max_per_second,
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self.max_per_second
            await asyncio.sleep(wait)


class ScholarlyAPIError(Exception):
    """An API call failed after retries."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BaseScholarlyClient(ABC):
    """Abstract base for scholarly API clients.

    Subclasses implement ``_base_url``, ``_headers``, and domain-specific
    search / fetch methods.  This base provides ``_get`` and ``_post``
    with rate limiting and retries.
    """

    def __init__(
        self,
        *,
        max_per_second: float = 5.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        polite_email: str = "",
    ) -> None:
        self._limiter = RateLimiter(max_per_second=max_per_second)
        self._max_retries = max_retries
        self._timeout = timeout
        self._polite_email = polite_email
        self._client: httpx.AsyncClient | None = None

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable name for logging."""
        ...

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers=self._default_headers(),
            )
        return self._client

    def _default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": (
                "VeriScholarAI/1.0 "
                "(scholarly plagiarism detection; "
                f"mailto:{self._polite_email})"
                if self._polite_email
                else "VeriScholarAI/1.0"
            ),
        }
        return headers

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rate-limited GET with retry."""
        return await self._request("GET", url, params=params)

    async def _post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rate-limited POST with retry."""
        return await self._request("POST", url, json=json)

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                resp = await client.request(
                    method, url, params=params, json=json
                )
                if resp.status_code == 429:
                    # Rate limited — back off and retry.
                    wait = min(2 ** attempt * 5, 60)
                    logger.warning(
                        "%s 429 rate limited, waiting %ds (attempt %d/%d)",
                        self.source_name, wait, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = min(2 ** attempt * 2, 30)
                    logger.warning(
                        "%s %d server error, retrying in %ds (attempt %d/%d)",
                        self.source_name, resp.status_code, wait,
                        attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning(
                    "%s HTTP %d: %s",
                    self.source_name, exc.response.status_code, str(exc)[:200],
                )
                raise ScholarlyAPIError(
                    f"{self.source_name}: HTTP {exc.response.status_code}",
                    status_code=exc.response.status_code,
                ) from exc
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                wait = min(2 ** attempt * 2, 30)
                logger.warning(
                    "%s network error, retrying in %ds: %s",
                    self.source_name, wait, str(exc)[:200],
                )
                await asyncio.sleep(wait)

        raise ScholarlyAPIError(
            f"{self.source_name}: failed after {self._max_retries + 1} attempts"
        ) from last_exc

    async def _get_text(self, url: str) -> str:
        """Rate-limited GET returning raw text (for full-text downloads)."""
        client = await self._get_client()
        await self._limiter.acquire()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    async def _get_bytes(self, url: str) -> bytes:
        """Rate-limited GET returning raw bytes (for PDF downloads)."""
        client = await self._get_client()
        await self._limiter.acquire()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
