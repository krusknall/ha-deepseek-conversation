"""Constants for the DeepSeek Conversation integration."""

import logging

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm

DOMAIN = "deepseek_conversation"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_REASONING = "reasoning"
CONF_TEMPERATURE = "temperature"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CHAT_MODEL = "deepseek-flash"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_REASONING = "off"
DEFAULT_TEMPERATURE = 0.6
DEFAULT_MODELS = ["deepseek-flash", "deepseek-v4-pro"]
# "off" disables thinking mode; the others are DeepSeek's reasoning efforts.
REASONING_LEVELS = ["off", "low", "high", "max"]
# Discontinued model names and the reasoning setting they stood for.
LEGACY_MODELS = {"deepseek-chat": "off", "deepseek-reasoner": "high"}

DEFAULT_OPTIONS = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_REASONING: DEFAULT_REASONING,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
}

MAX_TOOL_ITERATIONS = 10

AUTOMATION_API_ID = "deepseek_conversation_automations"
AUTOMATION_API_NAME = "Automation builder"
