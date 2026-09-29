"""Minimal async client for the DeepSeek chat completions API."""

from collections.abc import AsyncGenerator
import json
from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=15, sock_read=120)


class DeepSeekError(Exception):
    """Base error for DeepSeek API failures."""


class DeepSeekAuthError(DeepSeekError):
    """Raised when the API key is rejected."""


class DeepSeekClient:
    """Talks to an OpenAI-compatible DeepSeek endpoint."""

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, base_url: str
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def async_list_models(self) -> list[str]:
        """Return the model IDs available to this API key."""
        try:
            async with self._session.get(
                f"{self._base_url}/models",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                await _raise_for_status(response)
                data = await response.json()
        except aiohttp.ClientError as err:
            raise DeepSeekError(f"Cannot connect: {err}") from err
        except TimeoutError as err:
            raise DeepSeekError("Request timed out") from err

        return sorted(
            model["id"] for model in data.get("data", []) if isinstance(model, dict)
        )

    async def async_stream_chat(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any]]:
        """Send a chat completion request and yield streamed chunks."""
        body = {**payload, "stream": True}
        try:
            async with self._session.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers,
                json=body,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                await _raise_for_status(response)
                async for raw_line in response.content:
                    line = raw_line.decode("utf-8").strip()
                    # Blank lines separate events; lines starting with ':' are
                    # keep-alive comments sent while the model is queued.
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError as err:
                        raise DeepSeekError(f"Malformed stream data: {data}") from err
        except aiohttp.ClientError as err:
            raise DeepSeekError(f"Connection error: {err}") from err
        except TimeoutError as err:
            raise DeepSeekError("Request timed out") from err


async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
    """Translate HTTP errors into client exceptions."""
    if response.status < 400:
        return

    message = response.reason or "Unknown error"
    try:
        data = await response.json(content_type=None)
        message = data["error"]["message"]
    except ValueError, KeyError, TypeError, aiohttp.ClientError:
        pass

    if response.status == 401:
        raise DeepSeekAuthError(message)
    raise DeepSeekError(f"HTTP {response.status}: {message}")
