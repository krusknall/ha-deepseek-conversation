"""Tests for the conversation agent."""

import json
from pathlib import Path
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.deepseek_conversation.client import DeepSeekError
from custom_components.deepseek_conversation.const import AUTOMATION_API_ID

from .conftest import ScriptedStream, text_response, tool_response

AGENT_ID = "conversation.deepseek"


async def _converse(hass: HomeAssistant, text: str) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, None, Context(), agent_id=AGENT_ID
    )


def _find_tool(payload: dict[str, Any], suffix: str) -> str:
    names = [tool["function"]["name"] for tool in payload.get("tools", [])]
    return next(name for name in names if name.endswith(suffix))


async def test_text_response(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_stream: ScriptedStream
) -> None:
    """A streamed answer is returned as speech."""
    mock_stream.responses.append(text_response("Hello from DeepSeek!"))

    result = await _converse(hass, "Hi")

    assert result.response.speech["plain"]["speech"] == "Hello from DeepSeek!"
    payload = mock_stream.payloads[0]
    assert payload["model"] == "deepseek-chat"
    assert isinstance(payload["max_tokens"], int)
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1] == {"role": "user", "content": "Hi"}
    assert payload["tools"], "Assist tools should be offered"


async def test_device_control(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_stream: ScriptedStream
) -> None:
    """The model can turn on an exposed light through Assist."""
    assert await async_setup_component(hass, "intent", {})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    calls = async_mock_service(hass, "light", "turn_on")

    mock_stream.responses.append(
        lambda payload: tool_response(
            "call_1", _find_tool(payload, "HassTurnOn"), json.dumps({"name": "Kitchen"})
        )
    )
    mock_stream.responses.append(text_response("The kitchen light is on."))

    result = await _converse(hass, "Turn on the kitchen light")

    assert result.response.speech["plain"]["speech"] == "The kitchen light is on."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.kitchen"]

    second = mock_stream.payloads[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "call_1"


async def test_reasoning_sent_back_during_tool_loop(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_stream: ScriptedStream
) -> None:
    """Reasoning content is returned to the model within the same turn."""
    assert await async_setup_component(hass, "intent", {})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    async_mock_service(hass, "light", "turn_on")

    def first(payload: dict[str, Any]) -> list[dict[str, Any]]:
        chunks = tool_response(
            "call_1", _find_tool(payload, "HassTurnOn"), json.dumps({"name": "Kitchen"})
        )
        return [
            {
                "choices": [
                    {"delta": {"role": "assistant", "reasoning_content": "Plan."}}
                ]
            },
            *chunks,
        ]

    mock_stream.responses.extend([first, text_response("Done.")])
    await _converse(hass, "Turn on the kitchen light")

    assistant = mock_stream.payloads[1]["messages"][-2]
    assert assistant["reasoning_content"] == "Plan."


async def test_api_error(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_stream: ScriptedStream
) -> None:
    """API errors are reported as a conversation error."""

    def fail(_payload: dict[str, Any]) -> list[dict[str, Any]]:
        raise DeepSeekError("HTTP 402: Insufficient Balance")

    mock_stream.responses.append(fail)
    result = await _converse(hass, "Hi")

    assert result.response.response_type is conversation.intent.IntentResponseType.ERROR


@pytest.fixture
async def automation_setup(
    hass: HomeAssistant, tmp_path: Path, setup_integration: MockConfigEntry
) -> MockConfigEntry:
    """Enable the automation builder with a real automations.yaml."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n"
    )
    (tmp_path / "automations.yaml").write_text("[]\n")
    assert await async_setup_component(hass, "automation", {"automation": []})

    hass.states.async_set("light.porch", "off", {"friendly_name": "Porch light"})
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Front door"})
    async_expose_entity(hass, "conversation", "light.porch", True)
    async_expose_entity(hass, "conversation", "lock.front_door", False)

    entry = setup_integration
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_LLM_HASS_API: [AUTOMATION_API_ID]}
    )
    await hass.async_block_till_done()
    return entry


PORCH_AUTOMATION = """
alias: Porch light on event
triggers:
  - trigger: event
    event_type: porch_test
actions:
  - action: light.turn_on
    target:
      entity_id: light.porch
"""


async def test_create_automation(
    hass: HomeAssistant,
    tmp_path: Path,
    automation_setup: MockConfigEntry,
    mock_stream: ScriptedStream,
) -> None:
    """The model looks up entities and creates a working automation."""
    mock_stream.responses.extend(
        [
            lambda p: tool_response(
                "call_1", _find_tool(p, "find_entities"), json.dumps({"query": "porch"})
            ),
            lambda p: tool_response(
                "call_2",
                _find_tool(p, "create_automation"),
                json.dumps({"yaml": PORCH_AUTOMATION}),
            ),
            text_response("I created the automation."),
        ]
    )

    result = await _converse(hass, "Turn on the porch light on the test event")
    assert result.response.speech["plain"]["speech"] == "I created the automation."

    lookup = json.loads(mock_stream.payloads[1]["messages"][-1]["content"])
    assert lookup["entities"][0]["entity_id"] == "light.porch"
    assert all(e["entity_id"] != "lock.front_door" for e in lookup["entities"])

    created = json.loads(mock_stream.payloads[2]["messages"][-1]["content"])
    assert created["success"] is True

    stored = yaml_util.load_yaml(str(tmp_path / "automations.yaml"))
    assert len(stored) == 1
    assert list(stored[0])[:2] == ["id", "alias"]
    assert stored[0]["alias"] == "Porch light on event"

    entity_id = created["entity_id"]
    assert er.async_get(hass).async_get(entity_id) is not None
    assert hass.states.get(entity_id).state == "on"


@pytest.mark.parametrize(
    ("automation_yaml", "error_fragment"),
    [
        (
            PORCH_AUTOMATION.replace("light.porch", "lock.front_door").replace(
                "light.turn_on", "lock.unlock"
            ),
            "not exposed",
        ),
        (
            "alias: Broken\ntriggers:\n  - trigger: not_a_trigger\nactions: []\n",
            "Invalid",
        ),
        ("triggers: []\nactions: []\n", "alias"),
    ],
)
async def test_create_automation_rejected(
    hass: HomeAssistant,
    tmp_path: Path,
    automation_setup: MockConfigEntry,
    mock_stream: ScriptedStream,
    automation_yaml: str,
    error_fragment: str,
) -> None:
    """Unsafe or invalid automations are refused and reported to the model."""
    mock_stream.responses.extend(
        [
            lambda p: tool_response(
                "call_1",
                _find_tool(p, "create_automation"),
                json.dumps({"yaml": automation_yaml}),
            ),
            text_response("That did not work."),
        ]
    )

    await _converse(hass, "Make an automation")

    tool_result = json.loads(mock_stream.payloads[1]["messages"][-1]["content"])
    assert error_fragment in tool_result["error_text"]
    assert yaml_util.load_yaml(str(tmp_path / "automations.yaml")) == []
