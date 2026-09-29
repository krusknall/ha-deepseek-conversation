"""Shared fixtures."""

from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import patch

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deepseek_conversation.const import (
    CONF_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_OPTIONS,
    DOMAIN,
)

CLIENT = "custom_components.deepseek_conversation.client.DeepSeekClient"


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """Enable custom integrations and load core, as a real instance does."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def mock_models() -> Generator[Any]:
    """Mock the model listing used to validate credentials."""
    with patch(f"{CLIENT}.async_list_models", return_value=["deepseek-chat"]) as mock:
        yield mock


class ScriptedStream:
    """Replays one list of stream chunks per API call."""

    def __init__(self) -> None:
        """Initialize."""
        self.responses: list[Any] = []
        self.payloads: list[dict[str, Any]] = []

    async def __call__(
        self, _client: Any, payload: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any]]:
        """Yield the next scripted response."""
        self.payloads.append({**payload})
        response = self.responses.pop(0)
        if callable(response):
            response = response(payload)
        for chunk in response:
            yield chunk


@pytest.fixture
def mock_stream() -> Generator[ScriptedStream]:
    """Mock the streaming chat endpoint."""
    stream = ScriptedStream()
    with patch(
        f"{CLIENT}.async_stream_chat",
        new=lambda client, payload: stream(client, payload),
    ):
        yield stream


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="DeepSeek",
        data={CONF_API_KEY: "test-key", CONF_BASE_URL: DEFAULT_BASE_URL},
        options=DEFAULT_OPTIONS,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_models: Any
) -> MockConfigEntry:
    """Set up the integration."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def text_response(text: str) -> list[dict[str, Any]]:
    """Build stream chunks for a plain text answer."""
    half = len(text) // 2
    return [
        {"choices": [{"delta": {"role": "assistant", "content": text[:half]}}]},
        {"choices": [{"delta": {"content": text[half:]}, "finish_reason": "stop"}]},
    ]


def tool_response(call_id: str, name: str, arguments: str) -> list[dict[str, Any]]:
    """Build stream chunks for a single tool call, split across deltas."""
    half = len(arguments) // 2
    return [
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": arguments[:half],
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": arguments[half:]}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]
