"""Saved chat history, so conversations survive restarts and can be resumed."""

from typing import Any

from homeassistant.auth.models import User
from homeassistant.components import conversation, websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.json import json_dumps
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey
import voluptuous as vol

from .const import DOMAIN

DATA_HISTORY: HassKey[ChatHistory] = HassKey(f"{DOMAIN}_history")

STORAGE_VERSION = 1
SAVE_DELAY = 5
MAX_CONVERSATIONS = 200
# Tool results such as the live context can be large; keep only the start.
MAX_TOOL_RESULT_CHARS = 2000
SUMMARY_KEYS = ("id", "title", "source", "created", "updated")


def tool_result_data(content: conversation.ToolResultContent) -> Any:
    """Return a tool result's data on every supported Home Assistant version."""
    # 2026.10 wraps results in llm.ToolResult; 2026.9 stores the data directly.
    if (result := getattr(content, "result", None)) is not None:
        return result.data
    return content.tool_result


class ChatHistory:
    """Conversations handled by DeepSeek, stored per conversation ID."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self.conversations: dict[str, dict[str, Any]] = {}
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{DOMAIN}.history")

    async def async_load(self) -> None:
        """Load stored conversations."""
        data = await self._store.async_load() or {}
        self.conversations = data.get("conversations", {})

    @callback
    def async_restore(self, chat_log: conversation.ChatLog, agent_id: str) -> None:
        """Give a resumed conversation the turns Home Assistant has forgotten.

        Home Assistant drops a chat log after five idle minutes or a restart,
        while the chat UI keeps its conversation ID.
        """
        stored = self.conversations.get(chat_log.conversation_id)
        if not stored or any(
            isinstance(content, conversation.AssistantContent)
            for content in chat_log.content
        ):
            return

        # Only text is restored; old tool calls are not needed as context.
        earlier: list[conversation.Content] = []
        for message in stored["messages"]:
            if message["role"] == "user":
                earlier.append(conversation.UserContent(content=message["content"]))
            elif message["role"] == "assistant" and message.get("content"):
                earlier.append(
                    conversation.AssistantContent(
                        agent_id=agent_id, content=message["content"]
                    )
                )
        chat_log.content[-1:-1] = earlier

    @callback
    def async_record(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> None:
        """Store the turn that just finished."""
        start = max(
            i
            for i, content in enumerate(chat_log.content)
            if isinstance(content, conversation.UserContent)
        )
        now = dt_util.utcnow().isoformat()
        stored = self.conversations.setdefault(
            chat_log.conversation_id,
            {
                "id": chat_log.conversation_id,
                "title": user_input.text[:80],
                "source": _source(self.hass, user_input),
                "user_id": user_input.context.user_id,
                "created": now,
                "messages": [],
            },
        )
        stored["messages"].extend(
            message
            for content in chat_log.content[start:]
            if (message := _serialize(content))
        )
        stored["updated"] = now

        if len(self.conversations) > MAX_CONVERSATIONS:
            oldest = min(self.conversations.values(), key=lambda c: c["updated"])
            del self.conversations[oldest["id"]]
        self._async_schedule_save()

    @callback
    def async_delete(self, conversation_id: str) -> None:
        """Delete a conversation."""
        del self.conversations[conversation_id]
        self._async_schedule_save()

    @callback
    def _async_schedule_save(self) -> None:
        self._store.async_delay_save(
            lambda: {"conversations": self.conversations}, SAVE_DELAY
        )


def _source(hass: HomeAssistant, user_input: conversation.ConversationInput) -> Any:
    """Name the device a conversation came from, such as a voice satellite."""
    if user_input.device_id and (
        device := dr.async_get(hass).async_get(user_input.device_id)
    ):
        return device.name_by_user or device.name
    return None


def _serialize(content: conversation.Content) -> dict[str, Any] | None:
    """Store chat log content in the shape the chat UI receives it live."""
    if isinstance(content, conversation.UserContent):
        return {"role": "user", "content": content.content}
    if isinstance(content, conversation.AssistantContent):
        return {
            "role": "assistant",
            "content": content.content or "",
            "thinking_content": content.thinking_content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "tool_name": call.tool_name,
                    "tool_args": call.tool_args,
                }
                for call in content.tool_calls or []
            ],
        }
    if isinstance(content, conversation.ToolResultContent):
        result = tool_result_data(content)
        if len(text := json_dumps(result)) > MAX_TOOL_RESULT_CHARS:
            result = {"truncated": text[:MAX_TOOL_RESULT_CHARS]}
        return {
            "role": "tool_result",
            "tool_call_id": content.tool_call_id,
            "tool_name": content.tool_name,
            "result": result,
        }
    return None


def _visible(stored: dict[str, Any], user: User) -> bool:
    """Users see their own chats and those from devices without a user."""
    return stored.get("user_id") in (None, user.id)


async def async_setup_history(hass: HomeAssistant) -> None:
    """Load the history and register its websocket commands."""
    history = ChatHistory(hass)
    await history.async_load()
    hass.data[DATA_HISTORY] = history
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_delete)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/history/list"})
@callback
def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List the user's conversations, newest first."""
    conversations = [
        {key: stored.get(key) for key in SUMMARY_KEYS}
        for stored in hass.data[DATA_HISTORY].conversations.values()
        if _visible(stored, connection.user)
    ]
    conversations.sort(key=lambda c: c["updated"], reverse=True)
    connection.send_result(msg["id"], {"conversations": conversations})


def _get_visible(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> dict[str, Any] | None:
    stored = hass.data[DATA_HISTORY].conversations.get(msg["conversation_id"])
    if stored is None or not _visible(stored, connection.user):
        connection.send_error(msg["id"], "not_found", "Conversation not found")
        return None
    return stored


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history/get",
        vol.Required("conversation_id"): str,
    }
)
@callback
def ws_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return one conversation with its messages."""
    if stored := _get_visible(hass, connection, msg):
        connection.send_result(
            msg["id"], {key: stored[key] for key in (*SUMMARY_KEYS, "messages")}
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history/delete",
        vol.Required("conversation_id"): str,
    }
)
@callback
def ws_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete a conversation."""
    if _get_visible(hass, connection, msg):
        hass.data[DATA_HISTORY].async_delete(msg["conversation_id"])
        connection.send_result(msg["id"])
