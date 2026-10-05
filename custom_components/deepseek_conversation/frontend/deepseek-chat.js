// Chat UI for DeepSeek Conversation: a sidebar panel and a dashboard card.
// Plain web components without a build step. Messages go through the Assist
// pipeline, the same way Home Assistant's own Assist dialog sends them.

const DOMAIN = "deepseek_conversation";

const escapeHtml = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

// Not a ULID, so Home Assistant keeps the ID when it forgets an idle chat and
// the integration can restore the earlier turns.
const newConversationId = () =>
  `chat-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

// Home Assistant loads <ha-markdown> on demand; creating a markdown card
// loads it. Falls back to plain text if that ever stops working.
let markdownReady;
const ensureMarkdown = () =>
  (markdownReady ??= (async () => {
    if (customElements.get("ha-markdown")) return;
    try {
      const helpers = await window.loadCardHelpers();
      helpers.createCardElement({ type: "markdown", content: "" });
      await Promise.race([
        customElements.whenDefined("ha-markdown"),
        new Promise((resolve) => setTimeout(resolve, 5000)),
      ]);
    } catch (err) {
      console.warn("DeepSeek chat: markdown unavailable", err);
    }
  })());

const formatTime = (iso) => {
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
};

// "intent__HassTurnOn" -> "Turn on", "create_automation" -> "Create automation".
const toolLabel = (name) => {
  const words = name
    .split("__")
    .pop()
    .replace(/^Hass/, "")
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const toolTarget = (args) =>
  ["name", "area", "floor", "query", "domain"]
    .map((key) => args?.[key])
    .flat()
    .filter((value) => typeof value === "string")
    .join(", ");

const toolFailed = (result) =>
  result?.success === false || Boolean(result?.error) || Boolean(result?.error_text);

const CHAT_STYLES = `
  :host {
    display: flex;
    flex-direction: column;
    min-height: 0;
    color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, Roboto, sans-serif);
  }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .column {
    width: 100%;
    max-width: 760px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .empty {
    margin: auto;
    text-align: center;
    color: var(--secondary-text-color);
    padding: 24px;
  }
  .empty ha-icon { --mdc-icon-size: 40px; color: var(--primary-color); }
  .user {
    align-self: flex-end;
    max-width: 85%;
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    padding: 10px 14px;
    border-radius: 18px 18px 4px 18px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .assistant { display: flex; flex-direction: column; gap: 6px; }
  .assistant .text { overflow-wrap: anywhere; line-height: 1.5; }
  .assistant .text.plain { white-space: pre-wrap; }
  .assistant.error .text { color: var(--error-color, #db4437); }
  .typing { color: var(--secondary-text-color); }
  .typing::after { content: "…"; animation: blink 1s infinite; }
  @keyframes blink { 50% { opacity: 0.2; } }
  ha-markdown {
    display: block;
    --markdown-code-background-color: var(--secondary-background-color);
  }
  details {
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--secondary-background-color);
    font-size: 0.875rem;
  }
  summary {
    cursor: pointer;
    padding: 6px 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--secondary-text-color);
    list-style: none;
  }
  summary::-webkit-details-marker { display: none; }
  summary ha-icon { --mdc-icon-size: 16px; }
  .tool.ok summary ha-icon { color: var(--success-color, #43a047); }
  .tool.failed summary ha-icon { color: var(--error-color, #db4437); }
  .tool summary .label { color: var(--primary-text-color); }
  details pre, details .thinking {
    margin: 0;
    padding: 8px 10px;
    border-top: 1px solid var(--divider-color);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 300px;
    overflow-y: auto;
    font-size: 0.8rem;
  }
  .composer {
    border-top: 1px solid var(--divider-color);
    padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
    background: var(--card-background-color, var(--primary-background-color));
  }
  .composer .column { flex-direction: row; align-items: flex-end; gap: 8px; }
  textarea {
    flex: 1;
    box-sizing: border-box;
    resize: none;
    max-height: 160px;
    padding: 10px 14px;
    border-radius: 20px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    font: inherit;
    font-size: 16px; /* 16px stops iOS from zooming in on focus. */
    line-height: 1.4;
    outline: none;
  }
  textarea:focus { border-color: var(--primary-color); }
  .send {
    flex: none;
    width: 42px;
    height: 42px;
    border-radius: 50%;
    border: none;
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  .send:disabled { opacity: 0.4; cursor: default; }
  .notice {
    margin: auto;
    max-width: 420px;
    text-align: center;
    color: var(--secondary-text-color);
    padding: 24px;
  }
`;

// The conversation itself, shared by the panel and the card.
class DeepSeekChat extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${CHAT_STYLES}</style>
      <div class="messages"><div class="column"></div></div>
      <div class="composer"><div class="column">
        <textarea rows="1" enterkeyhint="send" placeholder="Message DeepSeek"></textarea>
        <button class="send" title="Send" disabled><ha-icon icon="mdi:arrow-up"></ha-icon></button>
      </div></div>`;
    this._scroller = this.shadowRoot.querySelector(".messages");
    this._list = this.shadowRoot.querySelector(".messages .column");
    this._input = this.shadowRoot.querySelector("textarea");
    this._sendButton = this.shadowRoot.querySelector(".send");
    this._messages = [];
    this._results = {}; // tool_call_id -> result
    this._elements = new Map(); // assistant message -> element
    this._busy = false;
    this.pipelineId = undefined;
    this.conversationId = newConversationId();

    this._input.addEventListener("input", () => this._onInput());
    this._input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        this._send();
      }
    });
    this._sendButton.addEventListener("click", () => this._send());
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      ensureMarkdown().then(() => this._renderAll());
      this._loadPipeline();
    }
  }

  get hass() {
    return this._hass;
  }

  // Pick an Assist pipeline that uses a DeepSeek conversation agent.
  async _loadPipeline() {
    const agents = new Set(
      Object.values(this._hass.entities || {})
        .filter((entity) => entity.platform === DOMAIN)
        .map((entity) => entity.entity_id)
    );
    try {
      const { pipelines, preferred_pipeline } = await this._hass.callWS({
        type: "assist_pipeline/pipeline/list",
      });
      const usable = pipelines.filter((p) => agents.has(p.conversation_engine));
      const chosen =
        usable.find((p) => p.id === this.pipelineId) ||
        usable.find((p) => p.id === preferred_pipeline) ||
        usable[0];
      this._pipeline = chosen;
    } catch (err) {
      console.error("DeepSeek chat: cannot load pipelines", err);
      this._pipeline = undefined;
    }
    this._renderAll();
    this._onInput();
  }

  newConversation() {
    if (this._busy) return;
    this.conversationId = newConversationId();
    this._messages = [];
    this._results = {};
    this._renderAll();
    this._input.focus();
  }

  async openConversation(conversationId) {
    if (this._busy) return;
    const stored = await this._hass.callWS({
      type: `${DOMAIN}/history/get`,
      conversation_id: conversationId,
    });
    this.conversationId = stored.id;
    this._messages = [];
    this._results = {};
    for (const message of stored.messages) {
      if (message.role === "tool_result") {
        this._results[message.tool_call_id] = message.result;
      } else {
        this._messages.push(message);
      }
    }
    this._renderAll();
    this._scrollToEnd(true);
  }

  _onInput() {
    this._input.style.height = "auto";
    // scrollHeight excludes the border, which border-box sizing includes.
    this._input.style.height = `${this._input.scrollHeight + 2}px`;
    // Mobile browsers draw a scrollbar even when nothing overflows.
    this._input.style.overflowY = this._input.scrollHeight > 160 ? "auto" : "hidden";
    this._sendButton.disabled =
      this._busy || !this._pipeline || !this._input.value.trim();
  }

  async _send() {
    const text = this._input.value.trim();
    if (!text || this._busy || !this._pipeline) return;
    this._input.value = "";
    this._busy = true;
    this._onInput();

    this._addMessage({ role: "user", content: text });
    // Shown until the first delta arrives.
    let current = this._addMessage({ role: "assistant", content: "", pending: true });
    let role;

    const finish = (unsub) => {
      unsub?.();
      if (current.pending) {
        this._messages.splice(this._messages.indexOf(current), 1);
        this._renderAll();
      }
      this._busy = false;
      this._onInput();
      this.dispatchEvent(new CustomEvent("conversation-updated", { bubbles: true, composed: true }));
    };

    let unsub;
    let done = false;
    const handle = (event) => {
      if (event.type === "intent-progress" && event.data.chat_log_delta) {
        const delta = event.data.chat_log_delta;
        role = delta.role || role;
        if (role === "assistant") {
          if (delta.role && !current.pending) {
            current = this._addMessage({ role: "assistant", content: "" });
          }
          current.pending = false;
          current.content = (current.content || "") + (delta.content || "");
          current.thinking_content = (current.thinking_content || "") + (delta.thinking_content || "");
          if (delta.tool_calls) {
            current.tool_calls = [...(current.tool_calls || []), ...delta.tool_calls];
          }
          this._renderMessage(current);
        } else if (role === "tool_result") {
          // Home Assistant 2026.10 sends `result`, 2026.9 sends `tool_result`.
          this._results[delta.tool_call_id] = delta.result ? delta.result.data : delta.tool_result;
          const owner = this._messages.find((m) =>
            m.tool_calls?.some((call) => call.id === delta.tool_call_id)
          );
          if (owner) this._renderMessage(owner);
        }
      } else if (event.type === "intent-end") {
        const output = event.data.intent_output;
        const speech = output.response?.speech?.plain?.speech;
        if (output.response?.response_type === "error") {
          this._showError(current, speech || "Something went wrong.");
        } else if (current.pending && speech) {
          // Answered without streaming, e.g. by a local intent.
          current.pending = false;
          current.content = speech;
          this._renderMessage(current);
        }
        done = true;
        finish(unsub);
      } else if (event.type === "error") {
        this._showError(current, event.data.message);
        done = true;
        finish(unsub);
      }
    };

    try {
      unsub = await this._hass.connection.subscribeMessage(handle, {
        type: "assist_pipeline/run",
        start_stage: "intent",
        end_stage: "intent",
        input: { text },
        pipeline: this._pipeline.id,
        conversation_id: this.conversationId,
      });
      if (done) unsub();
    } catch (err) {
      this._showError(current, err.message || String(err));
      finish();
    }
  }

  _showError(message, text) {
    message.pending = false;
    message.error = true;
    message.content = text;
    this._renderMessage(message);
  }

  _addMessage(message) {
    this._messages.push(message);
    this._renderAll();
    return message;
  }

  _scrollToEnd(force = false) {
    const el = this._scroller;
    const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (force || nearEnd) el.scrollTop = el.scrollHeight;
  }

  _renderAll() {
    this._elements.clear();
    this._list.textContent = "";
    if (this._hass && !this._pipeline) {
      this._list.innerHTML = `<div class="notice">
        No Assist pipeline uses DeepSeek yet. Create one under
        <b>Settings → Voice assistants</b> and select DeepSeek as its
        conversation agent.</div>`;
      return;
    }
    if (!this._messages.length) {
      this._list.innerHTML = `<div class="empty">
        <ha-icon icon="mdi:chat-processing-outline"></ha-icon>
        <p>Ask anything, control your home or describe an automation.</p></div>`;
      return;
    }
    for (const message of this._messages) {
      if (message.role === "user") {
        const el = document.createElement("div");
        el.className = "user";
        el.textContent = message.content;
        this._list.append(el);
      } else if (message.role === "assistant") {
        const el = document.createElement("div");
        this._elements.set(message, el);
        this._list.append(el);
        this._renderMessage(message);
      }
    }
    this._scrollToEnd(true);
  }

  _renderMessage(message) {
    const el = this._elements.get(message);
    if (!el) return;
    el.className = `assistant${message.error ? " error" : ""}`;

    // Rebuild the reasoning and tool parts; they change rarely. Keep open
    // <details> open across updates.
    const open = new Set(
      [...el.querySelectorAll("details[open]")].map((d) => d.dataset.key)
    );
    let head = "";
    if (message.thinking_content) {
      head += `<details data-key="thinking" ${open.has("thinking") ? "open" : ""}>
        <summary><ha-icon icon="mdi:brain"></ha-icon>Reasoning</summary>
        <div class="thinking">${escapeHtml(message.thinking_content)}</div></details>`;
    }
    for (const call of message.tool_calls || []) {
      const hasResult = call.id in this._results;
      const result = this._results[call.id];
      const state = !hasResult ? "running" : toolFailed(result) ? "failed" : "ok";
      const icon = { running: "mdi:progress-wrench", failed: "mdi:alert-circle-outline", ok: "mdi:check-circle-outline" }[state];
      const target = toolTarget(call.tool_args);
      head += `<details class="tool ${state}" data-key="${escapeHtml(call.id)}" ${open.has(call.id) ? "open" : ""}>
        <summary><ha-icon icon="${icon}"></ha-icon>
          <span class="label">${escapeHtml(toolLabel(call.tool_name))}</span>
          ${target ? `<span>· ${escapeHtml(target)}</span>` : ""}</summary>
        <pre>${escapeHtml(JSON.stringify(call.tool_args, null, 2))}${
          hasResult ? `\n\n→ ${escapeHtml(JSON.stringify(result, null, 2))}` : ""
        }</pre></details>`;
    }
    if (el._head !== head) {
      el._head = head;
      let headEl = el.querySelector(".head");
      if (!headEl) {
        headEl = document.createElement("div");
        headEl.className = "head";
        headEl.style.cssText = "display:flex;flex-direction:column;gap:6px";
        el.prepend(headEl);
      }
      headEl.innerHTML = head;
    }

    // The text is updated in place so streaming does not flicker.
    let textEl = el.querySelector(".text");
    const markdown = customElements.get("ha-markdown") && !message.error;
    if (!textEl || textEl.isMarkdown !== Boolean(markdown)) {
      textEl?.remove();
      textEl = document.createElement(markdown ? "ha-markdown" : "div");
      textEl.className = markdown ? "text" : "text plain";
      textEl.isMarkdown = Boolean(markdown);
      if (markdown) textEl.breaks = true;
      el.append(textEl);
    }
    if (message.pending) {
      textEl.remove();
      if (!el.querySelector(".typing")) {
        el.insertAdjacentHTML("beforeend", `<span class="typing">Thinking</span>`);
      }
    } else {
      el.querySelector(".typing")?.remove();
      if (markdown) textEl.content = message.content || "";
      else textEl.textContent = message.content || "";
      textEl.hidden = !message.content;
    }
    this._scrollToEnd();
  }
}

const PANEL_STYLES = `
  :host {
    display: flex;
    flex-direction: column;
    /* The panel container has no height of its own; dvh follows the iOS toolbar. */
    height: 100vh;
    height: 100dvh;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, Roboto, sans-serif);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 4px;
    height: var(--header-height, 56px);
    padding: 0 8px;
    padding-top: env(safe-area-inset-top);
    border-bottom: 1px solid var(--divider-color);
    background: var(--app-header-background-color, var(--primary-background-color));
    color: var(--app-header-text-color, var(--primary-text-color));
    flex: none;
  }
  .toolbar .title { flex: 1; font-size: 20px; margin-left: 8px; }
  button.icon {
    width: 40px;
    height: 40px;
    border: none;
    border-radius: 50%;
    background: none;
    color: inherit;
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  button.icon:hover { background: rgba(127, 127, 127, 0.15); }
  .body { flex: 1; display: flex; min-height: 0; position: relative; }
  .history {
    width: 280px;
    flex: none;
    border-right: 1px solid var(--divider-color);
    overflow-y: auto;
    background: var(--sidebar-background-color, var(--card-background-color));
  }
  :host([narrow]) .history {
    position: absolute;
    inset: 0;
    width: auto;
    z-index: 2;
    display: none;
  }
  :host([narrow][show-history]) .history { display: block; }
  :host(:not([narrow])) .history-toggle { display: none; }
  :host(:not([narrow])) .menu { display: none; }
  .item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 8px 10px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--divider-color);
  }
  .item:hover { background: rgba(127, 127, 127, 0.08); }
  .item.active { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
  .item .info { flex: 1; min-width: 0; }
  .item .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .item .meta { font-size: 0.8rem; color: var(--secondary-text-color); }
  .item button.icon { width: 32px; height: 32px; color: var(--secondary-text-color); opacity: 0.6; }
  .item button.icon:hover { opacity: 1; }
  .history .none { padding: 16px; color: var(--secondary-text-color); }
  deepseek-chat { flex: 1; min-width: 0; }
`;

class DeepSeekChatPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${PANEL_STYLES}</style>
      <div class="toolbar">
        <button class="icon menu" title="Menu"><ha-icon icon="mdi:menu"></ha-icon></button>
        <div class="title">DeepSeek</div>
        <button class="icon history-toggle" title="Chats"><ha-icon icon="mdi:history"></ha-icon></button>
        <button class="icon new" title="New chat"><ha-icon icon="mdi:square-edit-outline"></ha-icon></button>
      </div>
      <div class="body">
        <div class="history"></div>
        <deepseek-chat></deepseek-chat>
      </div>`;
    this._chat = this.shadowRoot.querySelector("deepseek-chat");
    this._history = this.shadowRoot.querySelector(".history");
    this._conversations = [];

    this.shadowRoot.querySelector(".menu").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }))
    );
    this.shadowRoot.querySelector(".history-toggle").addEventListener("click", () =>
      this.toggleAttribute("show-history")
    );
    this.shadowRoot.querySelector(".new").addEventListener("click", () => {
      this._chat.newConversation();
      this.removeAttribute("show-history");
      this._renderHistory();
    });
    this._chat.addEventListener("conversation-updated", () => this._loadHistory());
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._chat.hass = hass;
    if (first) this._loadHistory();
  }

  set narrow(narrow) {
    this.toggleAttribute("narrow", Boolean(narrow));
  }

  // Unused properties Home Assistant sets on every panel.
  set panel(_panel) {}
  set route(_route) {}

  async _loadHistory() {
    try {
      const { conversations } = await this._hass.callWS({ type: `${DOMAIN}/history/list` });
      this._conversations = conversations;
    } catch (err) {
      console.error("DeepSeek chat: cannot load history", err);
    }
    this._renderHistory();
  }

  _renderHistory() {
    if (!this._conversations.length) {
      this._history.innerHTML = `<div class="none">No saved chats yet.</div>`;
      return;
    }
    this._history.innerHTML = this._conversations
      .map(
        (c) => `<div class="item ${c.id === this._chat.conversationId ? "active" : ""}" data-id="${escapeHtml(c.id)}">
          <div class="info">
            <div class="title">${escapeHtml(c.title || "Untitled")}</div>
            <div class="meta">${escapeHtml(formatTime(c.updated))}${c.source ? ` · ${escapeHtml(c.source)}` : ""}</div>
          </div>
          <button class="icon delete" title="Delete"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
        </div>`
      )
      .join("");
    for (const item of this._history.querySelectorAll(".item")) {
      const id = item.dataset.id;
      item.addEventListener("click", async () => {
        await this._chat.openConversation(id);
        this.removeAttribute("show-history");
        this._renderHistory();
      });
      item.querySelector(".delete").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm("Delete this chat?")) return;
        await this._hass.callWS({ type: `${DOMAIN}/history/delete`, conversation_id: id });
        if (id === this._chat.conversationId) this._chat.newConversation();
        this._loadHistory();
      });
    }
  }
}

const CARD_STYLES = `
  ha-card { display: flex; flex-direction: column; overflow: hidden; }
  .header { display: flex; align-items: center; padding: 8px 8px 8px 16px; border-bottom: 1px solid var(--divider-color); }
  .header .title { flex: 1; font-size: 1.1rem; }
  .icon {
    width: 36px; height: 36px; border: none; border-radius: 50%;
    background: none; color: var(--secondary-text-color); cursor: pointer;
    display: grid; place-items: center;
  }
  .icon:hover { background: rgba(127, 127, 127, 0.15); }
  deepseek-chat { flex: 1; min-height: 0; }
`;

class DeepSeekChatCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `
        <style>${CARD_STYLES}</style>
        <ha-card>
          <div class="header">
            <div class="title"></div>
            <a class="icon" href="/deepseek-chat" title="All chats"><ha-icon icon="mdi:open-in-new"></ha-icon></a>
            <button class="icon new" title="New chat"><ha-icon icon="mdi:square-edit-outline"></ha-icon></button>
          </div>
          <deepseek-chat></deepseek-chat>
        </ha-card>`;
      this._chat = this.shadowRoot.querySelector("deepseek-chat");
      this.shadowRoot.querySelector(".new").addEventListener("click", () => this._chat.newConversation());
    }
    this.shadowRoot.querySelector(".title").textContent = this._config.title ?? "DeepSeek";
    this.shadowRoot.querySelector("ha-card").style.height = this._config.height || "480px";
    this._chat.pipelineId = this._config.pipeline_id;
  }

  set hass(hass) {
    this._chat.hass = hass;
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, rows: 8, min_rows: 5 };
  }
}

// Home Assistant replaces window.customElements with a scoped registry while
// it boots, and this file can load before that happens. Elements defined in
// the old registry work in the panel but never show up as dashboard cards, so
// wait for Home Assistant's own root element before defining them.
customElements.whenDefined("home-assistant").then(() => {
  const define = (name, element) => {
    if (!window.customElements.get(name)) window.customElements.define(name, element);
  };
  define("deepseek-chat", DeepSeekChat);
  define("deepseek-chat-panel", DeepSeekChatPanel);
  define("deepseek-chat-card", DeepSeekChatCard);
});

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "deepseek-chat-card")) {
  window.customCards.push({
    type: "deepseek-chat-card",
    name: "DeepSeek chat",
    description: "Chat with DeepSeek from a dashboard.",
  });
}
