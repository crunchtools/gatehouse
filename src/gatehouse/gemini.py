"""Gemini API client using httpx (no SDK)."""

from __future__ import annotations

import asyncio
import http
import random
from typing import Any

import httpx

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0
MAX_RETRY_AFTER = 90.0


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value (seconds). Returns None if unparseable."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER)


def _parse_retry_delay_from_body(response: httpx.Response) -> float | None:
    """Extract Gemini's RetryInfo.retryDelay from an error response body.

    Gemini returns 429s with a structured body of the form:
        {"error": {"details": [{"@type": ".../google.rpc.RetryInfo",
                                 "retryDelay": "30s"}, ...]}}
    The header is usually absent, so this body field is the authoritative signal.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    details = body.get("error", {}).get("details", [])
    if not isinstance(details, list):
        return None
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if not str(detail.get("@type", "")).endswith("RetryInfo"):
            continue
        raw = detail.get("retryDelay")
        if not isinstance(raw, str):
            continue
        try:
            seconds = float(raw.rstrip("s"))
        except ValueError:
            continue
        if seconds < 0:
            continue
        return min(seconds, MAX_RETRY_AFTER)
    return None


async def call_gemini(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
) -> str:
    """Call Gemini generateContent API and return the text response.

    Retries on 429 (rate limit) and 503 (overloaded) with exponential backoff.
    """
    url = f"{GEMINI_API_URL}/{model}:generateContent"
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    last_error: httpx.HTTPStatusError | None = None
    for attempt in range(MAX_RETRIES):
        response = await client.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (
            http.HTTPStatus.TOO_MANY_REQUESTS,
            http.HTTPStatus.SERVICE_UNAVAILABLE,
        ):
            backoff = _parse_retry_delay_from_body(response)
            if backoff is None:
                backoff = _parse_retry_after(response.headers.get("Retry-After"))
            if backoff is None:
                backoff = INITIAL_BACKOFF * (2 ** attempt) * (0.5 + random.random())
            await asyncio.sleep(backoff)
            last_error = httpx.HTTPStatusError(
                f"{response.status_code}",
                request=response.request,
                response=response,
            )
            continue
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        candidates: list[Any] = data.get("candidates", [])
        if not candidates:
            return "[]"
        content: dict[str, Any] = candidates[0].get("content", {})
        parts: list[Any] = content.get("parts", [])
        if not parts:
            return "[]"
        text: str = parts[0].get("text", "[]")
        return text

    if last_error is not None:
        raise last_error
    return "[]"
