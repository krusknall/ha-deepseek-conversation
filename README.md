# DeepSeek Conversation for Home Assistant

A conversation agent for [Home Assistant](https://www.home-assistant.io) powered by the [DeepSeek API](https://platform.deepseek.com). Use it with Assist, voice satellites and the chat dialog to control your home and create automations in plain language.

## Features

- **Device control** through Home Assistant's built-in Assist API, limited to the entities you expose.
- **Automation builder**: describe an automation and the assistant looks up your entities, writes it, validates it and adds it to `automations.yaml`.
- **Streaming responses** so voice replies start playing sooner.
- **Reasoning models** such as `deepseek-reasoner`, including tool calls.
- **No extra Python dependencies**, so it cannot conflict with packages bundled with Home Assistant.

## Requirements

- Home Assistant 2026.9 or newer
- A DeepSeek API key from [platform.deepseek.com](https://platform.deepseek.com)

## Installation

### HACS

1. In HACS, open the menu and select **Custom repositories**.
2. Add `https://github.com/krusknall/ha-deepseek-conversation` with the type **Integration**.
3. Download **DeepSeek Conversation** and restart Home Assistant.

### Manual

Copy `custom_components/deepseek_conversation` into the `custom_components` folder of your configuration directory and restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for **DeepSeek Conversation**.
2. Enter your API key.
3. Go to **Settings → Voice assistants**, edit an assistant and select **DeepSeek** as the conversation agent.
4. Expose the entities the assistant may control on the **Expose** tab.

## Options

Open the integration and select **Configure**.

| Option | Description |
| --- | --- |
| Model | `deepseek-chat` is the fastest and most reliable for device control. |
| Control Home Assistant | Select **Assist** for device control and **Automation builder** to allow creating automations. Both can be enabled. |
| Instructions | The system prompt. Supports templates. |
| Maximum tokens per response | Raise this if answers are cut off. |
| Temperature | Lower values give more predictable answers. |

## Automation builder

When **Automation builder** is enabled, the assistant can:

1. Search your exposed entities to find exact entity IDs.
2. Describe the automation and ask you to confirm.
3. Create it after confirmation.

Every automation is checked before it is saved:

- It must pass Home Assistant's own automation validation.
- It may only reference entities exposed to the assistant.
- It is written to `automations.yaml` the same way the automation editor saves it, then loaded immediately.

A notification appears whenever an automation is created. Automations can be reviewed, edited or deleted as usual under **Settings → Automations & scenes**.

The automation builder is a separate option, so you can enable it on a dedicated assistant and keep it off for the one used by voice satellites. Any conversation integration can use it, not only DeepSeek.

The builder requires the default `automation: !include automations.yaml` line in `configuration.yaml`.

## Troubleshooting

Enable debug logging to see requests and tool calls:

```yaml
logger:
  logs:
    custom_components.deepseek_conversation: debug
```

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

## License

[MIT](LICENSE)
