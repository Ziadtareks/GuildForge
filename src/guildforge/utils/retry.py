"""Retry helper for Discord 429/5xx responses and transient network errors."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp
import discord

from guildforge.utils.logger import GuildForgeLogger

T = TypeVar("T")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def should_retry(exc: BaseException) -> bool:
    if isinstance(exc, discord.HTTPException):
        return exc.status in RETRYABLE_STATUS
    return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError, ConnectionError))


def retry_after_seconds(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            return max(0.0, float(headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            return None
    return None


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    description: str,
    logger: GuildForgeLogger,
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Run an async callable, retrying only transient failures."""

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            if not should_retry(exc) or attempt >= attempts:
                raise
            last_error = exc
            delay = retry_after_seconds(exc)
            if delay is None:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, min(1.0, delay / 4))
            logger.debug(
                f"{description} failed ({exc.__class__.__name__}), "
                f"attempt {attempt}/{attempts}; retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)

    if last_error is not None:  # pragma: no cover - defensive
        raise last_error
    raise RuntimeError("retry loop exited without a result")  # pragma: no cover
