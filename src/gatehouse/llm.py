"""OpenRouter chat-completions client using httpx (no SDK)."""

from __future__ import annotations

import asyncio
import http
import random
import re
from typing import Any

import httpx

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-6-luna"
# Served when the requested model is rate-limited or down upstream. A
# different vendor on purpose: Luna's only zero-retention route is Azure.
FALLBACK_MODELS: tuple[str, ...] = ("google/gemini-3.1-flash-lite",)
# Zero data retention, no training on prompts. The reviewed diff is often
# private code, so no provider that keeps it may serve the request.
PROVIDER_POLICY: dict[str, Any] = {"zdr": True, "data_collection": "deny"}
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0
MAX_RETRY_AFTER = 90.0
RETRYABLE_STATUSES = frozenset(
    {
        http.HTTPStatus.TOO_MANY_REQUESTS,
        http.HTTPStatus.BAD_GATEWAY,
        http.HTTPStatus.SERVICE_UNAVAILABLE,
    }
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


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


def _backoff(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait before the next attempt, with jitter when unhinted."""
    if response is not None:
        hinted = _parse_retry_after(response.headers.get("Retry-After"))
        if hinted is not None:
            return hinted
    return INITIAL_BACKOFF * (2.0**attempt) * (0.5 + random.random())


def _models_for(model: str) -> list[str]:
    """The requested model first, then any fallback that isn't it."""
    return [model, *(m for m in FALLBACK_MODELS if m != model)]


def _message_text(body: Any) -> str | None:
    """Return the first choice's content, fences stripped, or None if there is none.

    OpenRouter can answer 200 with an ``error`` object when the upstream
    provider fails mid-request, so the status code alone is not enough. A body
    of the wrong shape, or an empty reply, is no more a review than an error.
    """
    if not isinstance(body, dict) or "error" in body:
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        return None
    return _FENCE.sub("", content.strip())


async def call_model(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
) -> str:
    """Call OpenRouter chat completions and return the response text.

    Retries on 429/502/503, on 200 responses carrying an upstream error, and
    on transport errors (timeouts, dropped connections), with exponential
    backoff and random jitter so concurrent agents do not retry in lockstep
    (thundering herd). Raises the last error once retries are exhausted.
    """
    payload: dict[str, Any] = {
        "model": model,
        "models": _models_for(model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "provider": PROVIDER_POLICY,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Title": "gatehouse",
    }

    last_error: httpx.HTTPError | None = None
    delay = 0.0
    for attempt in range(MAX_RETRIES):
        # Back off between attempts only; the last failure raises at once.
        if attempt:
            await asyncio.sleep(delay)
        try:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TransportError as exc:
            # A large diff can outlast the read timeout; a retry usually lands.
            last_error = exc
            delay = _backoff(None, attempt)
            continue
        if response.status_code in RETRYABLE_STATUSES:
            delay = _backoff(response, attempt)
            last_error = httpx.HTTPStatusError(
                f"{response.status_code}",
                request=response.request,
                response=response,
            )
            continue
        response.raise_for_status()
        text = _message_text(response.json())
        if text is None:
            delay = _backoff(response, attempt)
            last_error = httpx.HTTPStatusError(
                "no review in 200 response",
                request=response.request,
                response=response,
            )
            continue
        return text

    if last_error is not None:
        raise last_error
    return "[]"
