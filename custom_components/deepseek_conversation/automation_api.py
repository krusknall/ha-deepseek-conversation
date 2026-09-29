"""LLM API that lets a conversation agent create automations."""

import asyncio
import os
from typing import Any, override
import uuid

from homeassistant.components import persistent_notification
from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.const import CONF_ID, SERVICE_RELOAD
from homeassistant.core import HomeAssistant, callback, valid_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    llm,
)
from homeassistant.util import yaml as yaml_util
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.json import JsonObjectType
import voluptuous as vol

from .const import AUTOMATION_API_ID, AUTOMATION_API_NAME

MAX_SEARCH_RESULTS = 50

# Keys written first, matching the order used by the automation editor.
ORDERED_KEYS = (
    "alias",
    "description",
    "triggers",
    "trigger",
    "conditions",
    "condition",
    "actions",
    "action",
)

API_PROMPT = (
    "You can create Home Assistant automations.\n"
    "1. Call find_entities to look up the exact entity IDs you need. "
    "Never guess entity IDs.\n"
    "2. Write the automation as Home Assistant YAML with an `alias`, "
    "`triggers`, optional `conditions` and `actions`.\n"
    "3. Briefly describe the automation to the user and ask for confirmation "
    "before calling create_automation.\n"
    "4. If creation fails, fix the YAML based on the error and try again."
)

_write_lock = asyncio.Lock()


@callback
def async_register_automation_api(hass: HomeAssistant) -> None:
    """Register the automation builder API."""
    llm.async_register_api(hass, AutomationAPI(hass))


class AutomationAPI(llm.API):
    """Exposes tools for building automations."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=AUTOMATION_API_ID, name=AUTOMATION_API_NAME)

    @override
    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return an instance of the API."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[FindEntitiesTool(), CreateAutomationTool()],
        )


class FindEntitiesTool(llm.Tool):
    """Search exposed entities and return their IDs."""

    name = "find_entities"
    description = (
        "Search the entities exposed to the assistant. Returns entity IDs, "
        "names, areas and current states. Call without arguments to list all."
    )
    parameters = vol.Schema(
        {
            vol.Optional(
                "query", description="Text to match against names, IDs and areas."
            ): cv.string,
            vol.Optional(
                "domain", description="Only return this domain, e.g. 'light'."
            ): cv.string,
        }
    )

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Return matching entities."""
        args = self.parameters(tool_input.tool_args)
        query = args.get("query", "").strip().lower()
        domain = args.get("domain", "").strip().lower()

        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        area_registry = ar.async_get(hass)
        results: list[dict[str, Any]] = []

        for state in hass.states.async_all():
            if domain and state.domain != domain:
                continue
            if not async_should_expose(hass, llm_context.assistant, state.entity_id):
                continue

            area_name = None
            if entry := entity_registry.async_get(state.entity_id):
                area_id = entry.area_id
                if area_id is None and entry.device_id:
                    device = device_registry.async_get(entry.device_id)
                    area_id = device.area_id if device else None
                if area_id and (area := area_registry.async_get_area(area_id)):
                    area_name = area.name

            haystack = f"{state.entity_id} {state.name} {area_name or ''}".lower()
            if query and query not in haystack:
                continue

            item: dict[str, Any] = {
                "entity_id": state.entity_id,
                "name": state.name,
                "state": state.state,
            }
            if area_name:
                item["area"] = area_name
            results.append(item)

        results.sort(key=lambda item: item["entity_id"])
        if not results:
            return {"success": False, "error": "No exposed entities matched."}
        return {
            "success": True,
            "entities": results[:MAX_SEARCH_RESULTS],
            "truncated": len(results) > MAX_SEARCH_RESULTS,
        }


class CreateAutomationTool(llm.Tool):
    """Create a new automation in automations.yaml."""

    name = "create_automation"
    description = (
        "Create a new Home Assistant automation. Only call this after the user "
        "has confirmed what the automation should do."
    )
    parameters = vol.Schema(
        {
            vol.Required(
                "yaml",
                description=(
                    "The automation as Home Assistant YAML, including an alias, "
                    "triggers and actions. Do not include an id."
                ),
            ): cv.string,
        }
    )

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Validate, store and load the automation."""
        args = self.parameters(tool_input.tool_args)
        config = _parse_automation(args["yaml"])

        if unexposed := _unexposed_entities(hass, llm_context.assistant, config):
            raise HomeAssistantError(
                "The automation references entities that are not exposed to "
                f"the assistant: {', '.join(sorted(unexposed))}"
            )

        automation_id = uuid.uuid4().hex
        try:
            await async_validate_config_item(hass, automation_id, config)
        except vol.Invalid as err:
            raise HomeAssistantError(f"Invalid automation: {err}") from err

        ordered = {CONF_ID: automation_id}
        ordered.update({key: config[key] for key in ORDERED_KEYS if key in config})
        ordered.update(config)

        path = hass.config.path(AUTOMATION_CONFIG_PATH)
        async with _write_lock:
            await hass.async_add_executor_job(_append_automation, path, ordered)

        await hass.services.async_call(
            AUTOMATION_DOMAIN, SERVICE_RELOAD, {CONF_ID: automation_id}, blocking=True
        )

        entity_id = er.async_get(hass).async_get_entity_id(
            AUTOMATION_DOMAIN, AUTOMATION_DOMAIN, automation_id
        )
        alias = str(config["alias"])
        persistent_notification.async_create(
            hass,
            f"The assistant created the automation **{alias}**.",
            title="Automation created",
            notification_id=f"{AUTOMATION_API_ID}_{automation_id}",
        )
        return {"success": True, "alias": alias, "entity_id": entity_id}


def _parse_automation(text: str) -> dict[str, Any]:
    """Parse the YAML provided by the model."""
    try:
        parsed = yaml_util.parse_yaml(text)
    except HomeAssistantError as err:
        raise HomeAssistantError(f"Could not parse YAML: {err}") from err

    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise HomeAssistantError("The YAML must describe a single automation")

    config = dict(parsed)
    config.pop(CONF_ID, None)
    if not config.get("alias"):
        raise HomeAssistantError("The automation needs an alias")
    return config


def _unexposed_entities(hass: HomeAssistant, assistant: str, config: Any) -> set[str]:
    """Return referenced entity IDs that exist but are not exposed."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and valid_entity_id(value):
            found.add(value)

    walk(config)
    return {
        entity_id
        for entity_id in found
        if hass.states.get(entity_id) is not None
        and not async_should_expose(hass, assistant, entity_id)
    }


def _append_automation(path: str, automation: dict[str, Any]) -> None:
    """Append an automation to the automations file."""
    current: Any = yaml_util.load_yaml(path) if os.path.isfile(path) else None
    if current is None:
        current = []
    if not isinstance(current, list):
        raise HomeAssistantError(
            f"{AUTOMATION_CONFIG_PATH} does not contain a list of automations"
        )
    current.append(automation)
    write_utf8_file_atomic(path, yaml_util.dump(current))
