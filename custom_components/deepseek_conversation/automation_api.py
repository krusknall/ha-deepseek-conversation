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
import yaml

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

SERVICE_KEYS = ("action", "service", "service_template")
# Targets that expand to entities the exposure check cannot see.
INDIRECT_TARGET_KEYS = ("area_id", "device_id", "floor_id", "label_id")
# Parts of an action sequence that only read state.
READ_ONLY_KEYS = (
    "condition",
    "conditions",
    "if",
    "while",
    "until",
    "wait_template",
    "wait_for_trigger",
    "value_template",
)
# Services that need no entity target and may use templates in their data.
NOTIFY_DOMAINS = ("notify", "persistent_notification")
TEMPLATE_DATA_DOMAINS = (*NOTIFY_DOMAINS, "tts")
GENERIC_SERVICES = (
    "homeassistant.turn_on",
    "homeassistant.turn_off",
    "homeassistant.toggle",
)

API_PROMPT = (
    "You can create Home Assistant automations.\n"
    "1. Call find_entities to look up the exact entity IDs you need. "
    "Never guess entity IDs.\n"
    "2. Write the automation as Home Assistant YAML with an `alias`, "
    "`triggers`, optional `conditions` and `actions`.\n"
    "3. Briefly describe the automation to the user and ask for confirmation "
    "before calling create_automation.\n"
    "4. If creation fails, fix the YAML based on the error and try again.\n"
    "Actions may only control exposed entities, targeted by `entity_id`. "
    "Do not target areas, devices, floors or labels, and only use templates "
    "in the data of notify and tts actions."
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

        if problems := _action_problems(hass, llm_context.assistant, config):
            raise HomeAssistantError(" ".join(sorted(problems)))

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
    # Plain safe_load: Home Assistant's own loader would resolve tags such as
    # !include and !env_var and leak local files into the automation.
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as err:
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


def _is_template(value: str) -> bool:
    return "{{" in value or "{%" in value


def _strings(value: Any) -> list[str]:
    """Return every string in a config, including dictionary keys."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return [value] if isinstance(value, str) else []


def _action_problems(hass: HomeAssistant, assistant: str, config: Any) -> set[str]:
    """Return reasons the automation's actions may not be created.

    Triggers and conditions only read state, so only actions are checked.
    """
    # ponytail: static check; templates are refused rather than evaluated, so
    # templated targets need a smarter check if they are ever wanted.
    domains = {state.domain for state in hass.states.async_all()}
    problems: set[str] = set()
    entities: set[str] = set()

    def entity_ids(value: Any) -> set[str]:
        return {
            s
            for s in _strings(value)
            if valid_entity_id(s) and s.partition(".")[0] in domains
        }

    def check_call(step: dict[str, Any]) -> None:
        service = step.get("action", step.get("service"))
        if (
            "service_template" in step
            or not isinstance(service, str)
            or _is_template(service)
        ):
            problems.add("Action names must be written out, not templated.")
            return
        domain = service.partition(".")[0]
        if domain in NOTIFY_DOMAINS:
            return
        targets = entity_ids({k: v for k, v in step.items() if k not in SERVICE_KEYS})
        if hass.states.get(service):  # A script called as an action.
            targets.add(service)
            entities.add(service)
        if not targets or (
            service not in GENERIC_SERVICES
            and all(e.partition(".")[0] != domain for e in targets)
        ):
            problems.add(
                f"{service} must target exposed {domain} entities by entity_id."
            )

    def walk(value: Any, allow_templates: bool = False) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item, allow_templates)
            return
        if isinstance(value, str):
            if _is_template(value) and not allow_templates:
                problems.add(f"Templates are not allowed here: {value}")
            entities.update(entity_ids(value))
            return
        if not isinstance(value, dict):
            return

        service = next((value[k] for k in SERVICE_KEYS if k in value), None)
        if service is not None:
            check_call(value)
        templated_data = (
            isinstance(service, str)
            and service.partition(".")[0] in TEMPLATE_DATA_DOMAINS
        )
        for key, item in value.items():
            if key in READ_ONLY_KEYS or key in SERVICE_KEYS:
                continue
            if key in INDIRECT_TARGET_KEYS:
                problems.add(f"Target entities by entity_id, not by {key}.")
                continue
            walk(key)  # Keys can be entity IDs, as in scene.apply.
            if str(key).endswith("entity_id"):
                if any(i in ("all", "none") for i in _strings(item)):
                    problems.add("Target specific entities, not all of them.")
                walk(item)
            else:
                walk(item, allow_templates or (key == "data" and templated_data))

    walk(config.get("actions", config.get("action")))

    for entity_id in sorted(entities):
        if hass.states.get(entity_id) is None:
            problems.add(f"Unknown entity {entity_id}; use find_entities.")
        elif not async_should_expose(hass, assistant, entity_id):
            problems.add(f"{entity_id} is not exposed to the assistant.")
    return problems


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
