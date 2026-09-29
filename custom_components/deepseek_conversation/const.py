"""Constants for the DeepSeek Conversation integration."""

import logging

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm

DOMAIN = "deepseek_conversation"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.6
DEFAULT_MODELS = ["deepseek-chat", "deepseek-reasoner"]

DEFAULT_OPTIONS = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
}

MAX_TOOL_ITERATIONS = 10

AUTOMATION_API_ID = "deepseek_conversation_automations"
AUTOMATION_API_NAME = "Automation builder"
