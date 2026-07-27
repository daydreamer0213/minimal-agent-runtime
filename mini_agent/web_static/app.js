"use strict";

const ui = {
  sessionList: document.querySelector("#session-list"),
  newSessionForm: document.querySelector("#new-session-form"),
  newSessionTitle: document.querySelector("#new-session-title"),
  newSessionSubmit: document.querySelector("#new-session-form button[type='submit']"),
  chatList: document.querySelector("#chat-list"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  sendButton: document.querySelector("#send-button"),
  quickPrompts: document.querySelector("#quick-prompts"),
  traceList: document.querySelector("#trace-list"),
  todoList: document.querySelector("#todo-list"),
  status: document.querySelector("#status-message"),
  currentSession: document.querySelector("#current-session"),
  runtimeState: document.querySelector(".runtime-state"),
};

const page = {
  sessionId: null,
  busy: false,
};

function setText(node, text) {
  node.textContent = text == null ? "" : String(text);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function setState(message, level = "info") {
  setText(ui.status, message);
  ui.status.dataset.state = level;
}

function setBusy(busy, message) {
  page.busy = busy;
  ui.chatInput.disabled = busy;
  ui.sendButton.disabled = busy;
  ui.newSessionTitle.disabled = busy;
  if (ui.newSessionSubmit) {
    ui.newSessionSubmit.disabled = busy;
  }

  if (ui.quickPrompts) {
    for (const button of ui.quickPrompts.querySelectorAll("button")) {
      button.disabled = busy;
    }
  }

  for (const button of ui.sessionList.querySelectorAll("[data-session-id]")) {
    button.disabled = busy;
  }

  if (message !== undefined) {
    setState(message, busy ? "info" : "info");
  }

  document.body.classList.toggle("is-busy", busy);
}

function formatRole(role) {
  if (role === "user") {
    return "YOU";
  }
  if (role === "assistant" || role === "agent") {
    return "AGENT";
  }
  return String(role || "TRACE").toUpperCase();
}

function traceLabel(eventType) {
  return {
    model_response: "MODEL",
    model: "MODEL",
    tool: "TOOL",
    final: "ANSWER",
    runtime_error: "ERROR",
    loop_limit: "LIMIT",
    context_compacted: "MEMORY",
  }[String(eventType)] || String(eventType || "UNKNOWN").toUpperCase();
}

function renderEmpty(target, text, asListItem = false) {
  if (asListItem) {
    target.replaceChildren(element("li", "empty-state", text));
    return;
  }

  target.replaceChildren(element("p", "empty-state", text));
}

function renderSessions(sessions = [], currentSessionId) {
  ui.sessionList.replaceChildren();

  if (!sessions.length) {
    renderEmpty(ui.sessionList, "当前无会话，请先创建");
    return;
  }

  for (const session of sessions) {
    const button = element("button", "session-item");
    button.type = "button";
    button.dataset.sessionId = session.id;
    button.classList.toggle("active", session.id === currentSessionId);
    button.setAttribute("aria-pressed", session.id === currentSessionId ? "true" : "false");
    button.setAttribute("aria-current", session.id === currentSessionId ? "page" : "false");
    button.append(
      element("strong", "", session.title || "(未命名会话)"),
      element("span", "", session.id),
    );
    ui.sessionList.append(button);
  }

  for (const button of ui.sessionList.querySelectorAll("[data-session-id]")) {
    button.disabled = page.busy;
  }
}

function renderMessages(messages = []) {
  ui.chatList.replaceChildren();

  if (!messages.length) {
    renderEmpty(ui.chatList, "会话中还没有消息。发送一条开始交流。", false);
    return;
  }

  for (const message of messages) {
    const role = formatRole(message.role);
    const row = element("article", `message ${role === "YOU" ? "user" : ""}");
    row.append(
      element("strong", "message-role", role),
      element("p", "message-content", message.content || ""),
    );
    ui.chatList.append(row);
  }

  ui.chatList.scrollTop = ui.chatList.scrollHeight;
}

function renderTraces(traces = []) {
  ui.traceList.replaceChildren();

  if (!traces.length) {
    renderEmpty(ui.traceList, "暂无 trace：等待 Agent 完成一次循环。", false);
    return;
  }

  for (const trace of traces) {
    const eventType = String(trace.event || "unknown");
    const card = element(
      "article",
      `trace-card event-${eventType}`,
    );
    const heading = element("div", "trace-heading");
    heading.append(
      element("strong", "", traceLabel(trace.event)),
      element("span", "", `step ${trace.step} · ${trace.duration_ms ?? "?"}ms`),
    );

    const data = element("pre", "trace-data");
    data.textContent = JSON.stringify(trace.data || {}, null, 2);
    card.append(heading, data);
    ui.traceList.append(card);
  }

  ui.traceList.scrollTop = ui.traceList.scrollHeight;
}

function renderTodos(todos = []) {
  ui.todoList.replaceChildren();

  if (!todos.length) {
    renderEmpty(ui.todoList, "当前 session 暂无待办。", true);
    return;
  }

  for (const todo of todos) {
    const row = element("li", todo.done ? "done" : "");
    row.append(
      element("span", "todo-status", todo.done ? "完成" : "待办"),
      element("span", "", todo.text),
    );
    ui.todoList.append(row);
  }
}

function render(state) {
  const sessions = Array.isArray(state.sessions) ? state.sessions : [];
  const messages = Array.isArray(state.messages) ? state.messages : [];
  const traces = Array.isArray(state.traces) ? state.traces : [];
  const todos = Array.isArray(state.todos) ? state.todos : [];

  page.sessionId = state.current_session_id || null;
  const active = sessions.find((item) => item.id === page.sessionId);

  setText(ui.currentSession, active ? active.title || active.id : "未选择会话");
  setRuntimeState("Agent loop: ready");
  renderSessions(sessions, page.sessionId);
  renderMessages(messages);
  renderTraces(traces);
  renderTodos(todos);
}

function setRuntimeState(text) {
  const marker = element("span");
  marker.setAttribute("aria-hidden", "true");
  setText(marker, "");
  ui.runtimeState.replaceChildren(marker, ` ${text}`);
}

function parseJsonSafely(text, label) {
  if (!text) {
    throw new Error(`${label}: empty response body`);
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${label}: invalid JSON`);
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const bodyText = await response.text();
  let payload;

  try {
    payload = parseJsonSafely(bodyText, `${path}`);
  } catch (error) {
    if (response.ok) {
      throw error;
    }
    payload = {};
  }

  if (!response.ok) {
    const errorMessage = payload.error || bodyText || `HTTP ${response.status}`;
    throw new Error(errorMessage);
  }

  return payload;
}

function ensureStatePayload(payload, actionLabel) {
  if (!payload || typeof payload !== "object") {
    throw new Error(`${actionLabel} 失败：响应不是合法 JSON`);
  }

  if (!payload.state || typeof payload.state !== "object") {
    throw new Error(`${actionLabel} 失败：响应缺少 state`);
  }

  return payload.state;
}

function describeBusyError(error) {
  const text = (error && error.message ? error.message : String(error || "未知错误")).trim();
  if (text.includes("session")) {
    return `${text}，请先创建或选择一个会话。`;
  }
  if (text.includes("400") || text.includes("Bad Request")) {
    return `${text}，请检查输入是否完整。`;
  }
  return text || "请求失败，请稍后重试。";
}

async function refresh(sessionId = null) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const state = await request(`/api/state${query}`);
  render(state);
  return state;
}

function onSessionClick(event) {
  const button = event.target.closest("[data-session-id]");
  if (!button || page.busy) {
    return;
  }
  const sessionId = button.dataset.sessionId;
  if (!sessionId || sessionId === page.sessionId) {
    return;
  }

  setBusy(true, "正在切换会话...");
  refresh(sessionId)
    .then(() => {
      setState("");
    })
    .catch((error) => {
      setState(describeBusyError(error), "error");
    })
    .finally(() => setBusy(false));
}

function onQuickPrompt(event) {
  const button = event.target.closest("[data-prompt]");
  if (!button || page.busy) {
    return;
  }
  ui.chatInput.value = button.dataset.prompt;
  ui.chatInput.focus();
}

async function createSession(event) {
  event.preventDefault();
  if (page.busy) {
    return;
  }

  const title = ui.newSessionTitle.value.trim();
  if (!title) {
    setState("请输入会话标题。", "error");
    return;
  }

  try {
    setBusy(true, "正在创建会话...");
    const payload = await request("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    const state = ensureStatePayload(payload, "会话创建");
    render(state);
    ui.newSessionTitle.value = "";
    setState("会话创建成功。", "ok");
  } catch (error) {
    setState(describeBusyError(error), "error");
  } finally {
    setBusy(false);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (page.busy) {
    return;
  }
  if (!page.sessionId) {
    setState("请先选择或创建会话，再发送消息。", "error");
    return;
  }

  const message = ui.chatInput.value.trim();
  if (!message) {
    setState("请输入消息内容。", "error");
    return;
  }

  try {
    setBusy(true, "Agent 正在处理中...");
    const payload = await request("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: page.sessionId,
        message,
      }),
    });
    const state = ensureStatePayload(payload, "聊天");
    ui.chatInput.value = "";
    render(state);
    setState("已完成：收到回复。", "ok");
  } catch (error) {
    setState(describeBusyError(error), "error");
  } finally {
    setBusy(false);
  }
}

function onChatKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey) {
    return;
  }
  if (!event.isComposing && event.key === "Enter") {
    event.preventDefault();
    if (typeof ui.chatForm.requestSubmit === "function") {
      ui.chatForm.requestSubmit();
    } else {
      ui.chatForm.dispatchEvent(
        new Event("submit", {
          bubbles: true,
          cancelable: true,
        }),
      );
    }
  }
}

ui.sessionList.addEventListener("click", onSessionClick);
ui.newSessionForm.addEventListener("submit", createSession);
ui.quickPrompts.addEventListener("click", onQuickPrompt);
ui.chatForm.addEventListener("submit", sendMessage);
ui.chatInput.addEventListener("keydown", onChatKeydown);

setState("加载会话中...");
setBusy(true, "加载会话中...");
refresh()
  .then(() => {
    setState("", "ok");
  })
  .catch((error) => {
    setState(describeBusyError(error), "error");
  })
  .finally(() => setBusy(false));
