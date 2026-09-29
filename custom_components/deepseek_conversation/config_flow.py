"""Config flow for the DeepSeek Conversation integration."""

from typing import Any, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .client import DeepSeekAuthError, DeepSeekClient, DeepSeekError
from .const import (
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    DEFAULT_BASE_URL,
    DEFAULT_MODELS,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOGGER,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
    }
)


async def _async_validate(hass: HomeAssistant, data: dict[str, Any]) -> list[str]:
    """Check the credentials and return available models."""
    client = DeepSeekClient(
        async_get_clientsession(hass), data[CONF_API_KEY], data[CONF_BASE_URL]
    )
    return await client.async_list_models()


class DeepSeekConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_API_KEY: user_input[CONF_API_KEY]})
            try:
                await _async_validate(self.hass, user_input)
            except DeepSeekAuthError:
                errors["base"] = "invalid_auth"
            except DeepSeekError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="DeepSeek", data=user_input, options=DEFAULT_OPTIONS
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DeepSeekOptionsFlow()


class DeepSeekOptionsFlow(OptionsFlow):
    """Handle model and assistant options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
            return self.async_create_entry(data=user_input)

        try:
            models = await _async_validate(self.hass, dict(self.config_entry.data))
        except DeepSeekError:
            models = []

        apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        api_ids = {api["value"] for api in apis}
        suggested = dict(self.config_entry.options)
        if selected := suggested.get(CONF_LLM_HASS_API):
            suggested[CONF_LLM_HASS_API] = [a for a in selected if a in api_ids]

        schema = vol.Schema(
            {
                vol.Required(CONF_CHAT_MODEL): SelectSelector(
                    SelectSelectorConfig(
                        options=models or DEFAULT_MODELS,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_LLM_HASS_API): SelectSelector(
                    SelectSelectorConfig(options=apis, multiple=True)
                ),
                vol.Optional(CONF_PROMPT): TemplateSelector(),
                vol.Required(CONF_MAX_TOKENS): NumberSelector(
                    NumberSelectorConfig(
                        min=64, max=8192, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_TEMPERATURE): NumberSelector(
                    NumberSelectorConfig(min=0, max=2, step=0.05)
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )
