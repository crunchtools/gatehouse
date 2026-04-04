"""Gemini API client using httpx (no SDK)."""

from __future__ import annotations

import asyncio
import http
from typing import Any

import httpx

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0


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
            backoff = INITIAL_BACKOFF * (2 ** attempt)
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
