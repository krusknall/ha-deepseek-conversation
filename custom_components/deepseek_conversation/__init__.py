"""The DeepSeek Conversation integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .automation_api import async_register_automation_api
from .client import DeepSeekAuthError, DeepSeekClient, DeepSeekError
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION,)

type DeepSeekConfigEntry = ConfigEntry[DeepSeekClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the automation builder so any assistant can select it."""
    async_register_automation_api(hass)
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
