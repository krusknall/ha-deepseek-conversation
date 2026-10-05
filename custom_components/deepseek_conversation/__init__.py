"""The DeepSeek Conversation integration."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .automation_api import async_register_automation_api
from .client import DeepSeekAuthError, DeepSeekClient, DeepSeekError
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN
from .history import async_setup_history

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION,)

type DeepSeekConfigEntry = ConfigEntry[DeepSeekClient]


FRONTEND_URL = f"/{DOMAIN}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the parts shared by all config entries."""
    # Registered here so any assistant can select the automation builder.
    async_register_automation_api(hass)
    await async_setup_history(hass)

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend"))]
    )
    # The version busts the browser cache after an update.
    version = (await async_get_integration(hass, DOMAIN)).version
    module_url = f"{FRONTEND_URL}/deepseek-chat.js?v={version}"
    frontend.add_extra_js_url(hass, module_url)  # Makes the card available.
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path="deepseek-chat",
        webcomponent_name="deepseek-chat-panel",
        sidebar_title="DeepSeek",
        sidebar_icon="mdi:chat-processing-outline",
        module_url=module_url,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DeepSeekConfigEntry) -> bool:
    """Set up DeepSeek from a config entry."""
    client = DeepSeekClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    try:
        await client.async_list_models()
    except DeepSeekAuthError as err:
        raise ConfigEntryError(f"Invalid API key: {err}") from err
    except DeepSeekError as err:
        raise ConfigEntryNotReady(f"Cannot reach DeepSeek: {err}") from err

    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeepSeekConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: DeepSeekConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
