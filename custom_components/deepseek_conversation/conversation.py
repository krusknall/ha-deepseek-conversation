"""Conversation agent backed by DeepSeek."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
import json
from typing import Any, Literal, override

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.json import json_dumps
from probatio import to_openapi

from . import DeepSeekConfigEntry
from .client import DeepSeekAuthError, DeepSeekError
from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
)

# JSON schema keywords the DeepSeek function-calling API does not accept at
# the top level of a tool's parameters.
UNSUPPORTED_SCHEMA_KEYS = {"oneOf", "anyOf", "allOf"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeepSeekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation entity."""
    async_add_entities([DeepSeekConversationEntity(entry)])


def _format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> dict[str, Any]:
    """Convert a Home Assistant LLM tool into an OpenAI-style tool spec."""
    schema = to_openapi(tool.parameters, custom_serializer=custom_serializer)
    schema = {k: v for k, v in schema.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    function: dict[str, Any] = {"name": tool.name, "parameters": schema}
    if tool.description:
        function["description"] = tool.description
    return {"type": "function", "function": function}


def _convert_chat_log(chat_log: conversation.ChatLog) -> list[dict[str, Any]]:
    """Convert the chat log into DeepSeek chat messages."""
    last_user_index = max(
        (i for i, c in enumerate(chat_log.content) if c.role == "user"), default=-1
    )
    messages: list[dict[str, Any]] = []

    for index, content in enumerate(chat_log.content):
        if isinstance(content, conversation.SystemContent):
            if content.content:
                messages.append({"role": "system", "content": content.content})
        elif isinstance(content, conversation.UserContent):
            messages.append({"role": "user", "content": content.content})
        elif isinstance(content, conversation.AssistantContent):
            message: dict[str, Any] = {
                "role": "assistant",
                "content": content.content or "",
            }
            if content.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json_dumps(tool_call.tool_args),
                        },
                    }
                    for tool_call in content.tool_calls
                ]
            # Reasoning models require their reasoning to be sent back while
            # they are still working through tool calls for the current turn.
            if index > last_user_index and content.thinking_content:
                message["reasoning_content"] = content.thinking_content
            messages.append(message)
        elif isinstance(content, conversation.ToolResultContent):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": content.tool_call_id,
                    "content": json_dumps(content.tool_result),
                }
            )

    return messages


def _decode_tool_arguments(arguments: str) -> dict[str, Any]:
    """Parse the JSON arguments of a tool call."""
    if not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as err:
        raise HomeAssistantError(f"Invalid tool arguments from model: {err}") from err
    if not isinstance(parsed, dict):
        raise HomeAssistantError("Tool arguments from model must be a JSON object")
    return parsed


async def _transform_stream(
    stream: AsyncIterator[dict[str, Any]],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Convert DeepSeek stream chunks into chat log deltas."""
    yield {"role": "assistant"}
    pending_calls: dict[int, dict[str, str]] = {}

    async for chunk in stream:
        if not (choices := chunk.get("choices")):
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        if reasoning := delta.get("reasoning_content"):
            yield {"thinking_content": reasoning}
        if text := delta.get("content"):
            yield {"content": text}

        for call in delta.get("tool_calls") or []:
            slot = pending_calls.setdefault(
                call.get("index", 0), {"id": "", "name": "", "arguments": ""}
            )
            if call_id := call.get("id"):
                slot["id"] = call_id
            function = call.get("function") or {}
            slot["name"] += function.get("name") or ""
            slot["arguments"] += function.get("arguments") or ""

        if choice.get("finish_reason") == "length":
            LOGGER.warning(
                "Response was cut off by the token limit; consider raising "
                "the maximum tokens option"
            )

    if pending_calls:
        tool_calls: list[llm.ToolInput] = []
        for slot in (pending_calls[i] for i in sorted(pending_calls)):
            tool_input = llm.ToolInput(
                tool_name=slot["name"],
                tool_args=_decode_tool_arguments(slot["arguments"]),
            )
            if slot["id"]:
                tool_input.id = slot["id"]
            tool_calls.append(tool_input)
        yield {"tool_calls": tool_calls}


class DeepSeekConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """DeepSeek conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: DeepSeekConfigEntry) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="DeepSeek",
            model=entry.options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if entry.options.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    @override
    async def async_added_to_hass(self) -> None:
        """Register the agent when the entity is added."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent when the entity is removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a user message."""
        options = self.entry.options

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _async_handle_chat_log(self, chat_log: conversation.ChatLog) -> None:
        """Query the model until it no longer requests tool calls."""
        options = self.entry.options
        client = self.entry.runtime_data

        payload: dict[str, Any] = {
            "model": options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            "max_tokens": int(options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)),
            "temperature": options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
        }
        if chat_log.llm_api:
            payload["tools"] = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        for _iteration in range(MAX_TOOL_ITERATIONS):
            payload["messages"] = _convert_chat_log(chat_log)
            stream = _transform_stream(client.async_stream_chat(payload))

            try:
                async for _content in chat_log.async_add_delta_content_stream(
                    self.entity_id, stream
                ):
                    pass
            except DeepSeekAuthError as err:
                raise HomeAssistantError(
                    "DeepSeek rejected the API key. Reconfigure the integration."
                ) from err
            except DeepSeekError as err:
                raise HomeAssistantError(f"Error talking to DeepSeek: {err}") from err

            if not chat_log.unresponded_tool_results:
                break
        else:
            raise HomeAssistantError(
                f"DeepSeek was still calling tools after {MAX_TOOL_ITERATIONS} "
                "rounds; stopped to avoid a loop."
            )
