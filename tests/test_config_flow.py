"""Tests for the config and options flows."""

from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deepseek_conversation.client import DeepSeekAuthError
from custom_components.deepseek_conversation.const import (
    AUTOMATION_API_ID,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    DEFAULT_BASE_URL,
    DOMAIN,
)

from .conftest import CLIENT

USER_INPUT = {CONF_API_KEY: "test-key", CONF_BASE_URL: DEFAULT_BASE_URL}


async def test_user_flow(hass: HomeAssistant, mock_models: Any) -> None:
    """A valid key creates an entry with default options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.deepseek_conversation.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT
    assert result["options"][CONF_CHAT_MODEL] == "deepseek-chat"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """A rejected key shows an error and allows retrying."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(f"{CLIENT}.async_list_models", side_effect=DeepSeekAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_key_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_models: Any
) -> None:
    """The same API key cannot be added twice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Options can enable the automation builder."""
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CHAT_MODEL: "deepseek-chat",
            CONF_LLM_HASS_API: ["assist", AUTOMATION_API_ID],
            CONF_MAX_TOKENS: 2048,
            CONF_TEMPERATURE: 0.3,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LLM_HASS_API] == ["assist", AUTOMATION_API_ID]
    assert entry.options[CONF_MAX_TOKENS] == 2048
