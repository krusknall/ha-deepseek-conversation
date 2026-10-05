"""Tests for the saved chat history and the chat UI registration."""

from datetime import timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.auth.models import User
from homeassistant.components import conversation
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.chat_session import CONVERSATION_TIMEOUT
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from .conftest import ScriptedStream, text_response, tool_response

AGENT_ID = "conversation.deepseek"
CHAT_ID = "chat-test"


async def _converse(
    hass: HomeAssistant, text: str, user_id: str | None = None
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, CHAT_ID, Context(user_id=user_id), agent_id=AGENT_ID
    )


async def test_history_saved_and_listed(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_stream: ScriptedStream,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: User,
) -> None:
    """A conversation with a tool call is saved and can be read back."""
    assert await async_setup_component(hass, "intent", {})
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    async_expose_entity(hass, "conversation", "light.kitchen", True)
    async_mock_service(hass, "light", "turn_on")

    mock_stream.responses.extend(
        [
            lambda payload: tool_response(
                "call_1",
                next(
                    tool["function"]["name"]
                    for tool in payload["tools"]
                    if tool["function"]["name"].endswith("HassTurnOn")
                ),
                json.dumps({"name": "Kitchen"}),
            ),
            text_response("The kitchen light is on."),
        ]
    )
    await _converse(hass, "Turn on the kitchen light", hass_admin_user.id)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "deepseek_conversation/history/list"})
    listing = (await client.receive_json())["result"]["conversations"]
    assert [(c["id"], c["title"]) for c in listing] == [
        (CHAT_ID, "Turn on the kitchen light")
    ]

    await client.send_json_auto_id(
        {"type": "deepseek_conversation/history/get", "conversation_id": CHAT_ID}
    )
    messages = (await client.receive_json())["result"]["messages"]
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]
    assert messages[1]["tool_calls"][0]["tool_args"] == {"name": "Kitchen"}
    assert messages[2]["tool_call_id"] == "call_1"
    assert messages[3]["content"] == "The kitchen light is on."


async def test_history_private_to_user(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_stream: ScriptedStream,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Another user's chat is neither listed nor readable."""
    other = await hass.auth.async_create_user("Other")
    mock_stream.responses.append(text_response("Hi"))
    await _converse(hass, "Secret question", other.id)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "deepseek_conversation/history/list"})
    assert (await client.receive_json())["result"]["conversations"] == []

    for command in ("get", "delete"):
        await client.send_json_auto_id(
            {
                "type": f"deepseek_conversation/history/{command}",
                "conversation_id": CHAT_ID,
            }
        )
        assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_resume_after_session_expired(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_stream: ScriptedStream,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Earlier turns are sent again after Home Assistant forgot the chat."""
    mock_stream.responses.append(text_response("Your name is Simon."))
    await _converse(hass, "My name is Simon")

    freezer.tick(CONVERSATION_TIMEOUT + timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_stream.responses.append(text_response("Simon."))
    await _converse(hass, "What is my name?")

    messages = mock_stream.payloads[1]["messages"]
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "My name is Simon"),
        ("assistant", "Your name is Simon."),
        ("user", "What is my name?"),
    ]


async def test_delete(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_stream: ScriptedStream,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A chat without a user, such as one from a voice satellite, can be deleted."""
    mock_stream.responses.append(text_response("Hi"))
    await _converse(hass, "Hello")

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "deepseek_conversation/history/delete", "conversation_id": CHAT_ID}
    )
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id({"type": "deepseek_conversation/history/list"})
    assert (await client.receive_json())["result"]["conversations"] == []


async def test_chat_panel_registered(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The chat appears in the sidebar."""
    panel: Any = hass.data[DATA_PANELS]["deepseek-chat"]
    assert panel.sidebar_title == "DeepSeek"
