# Minimal Agent 本地网页演示设计

日期：2026-07-27

## 1. 目标与边界

在现有 Python CLI Agent 外增加一个适合录屏的本地网页。网页只负责展示和操作，不重写 `AgentRuntime`、工具注册、session、memory、SQLite 或 DeepSeek 客户端。

首版目标：

- 在一个页面中创建、切换并继续不同 session。
- 发送真实消息并显示最终回答。
- 清楚展示模型响应、工具调用、工具结果和最终回答的循环轨迹。
- 展示当前 session 的待办，直观看出两个 session 互不影响。
- 使用 `python -m mini_agent.web` 启动，并自动打开浏览器。
- 保持 Python 3.11+ 标准库运行时，不增加 Flask、React、Node.js 或其他第三方依赖。

首版不包含：

- 公网部署、登录、多用户权限和远程访问。
- 流式输出、WebSocket 或 Server-Sent Events。
- 在浏览器中填写或查看 API Key。
- 真正的互联网搜索或实时天气。
- 修改现有 Agent 核心行为。

## 2. 方案比较与选择

### 方案 A：Python 标准库 HTTP 服务 + 原生前端（采用）

使用 `ThreadingHTTPServer` 提供固定静态文件和 JSON API，前端使用 HTML、CSS 和 JavaScript。它能直接复用现有 Python 对象，没有新的安装步骤，也最符合“从零实现最小 Agent”的题目。

### 方案 B：Flask + 模板

路由更简短，但会增加运行时依赖和安装步骤。对于只有少量固定 API 的本地演示，收益不足。

### 方案 C：React/Vite + Python API

适合复杂产品界面，但会引入 Node.js、构建工具和前后端两套依赖，明显超出本地录屏演示的范围。

## 3. 页面设计

页面主题是“Agent 实验台”，不是通用聊天软件。视觉重点是让评审者在一次录屏中看懂 Agent 如何循环。

### 3.1 布局

桌面端采用三栏：

```text
┌──────────────┬──────────────────────────────┬─────────────────────┐
│ Sessions     │ Chat                         │ Live Agent Trace    │
│              │                              │                     │
│ weather-chat │ 用户与最终回答               │ step 1  MODEL       │
│ weekly-report│                              │ step 1  TOOL        │
│              │ 快捷演示问题                 │ step 2  MODEL       │
│ + 新会话     │ 输入框              发送      │                     │
│              │                              │ 当前 Session 待办   │
└──────────────┴──────────────────────────────┴─────────────────────┘
```

- 左栏宽约 240px：session 列表、当前标记和“新建会话”。
- 中栏占剩余空间：聊天记录、两个快捷演示问题、输入框和发送按钮。
- 右栏宽约 360px：按时间排列的 trace 卡片和当前 session 待办。
- 窄屏时按“聊天、session、trace/todo”的顺序纵向排列。

### 3.2 视觉语言

- 背景使用冷灰蓝 `#E9EEF5`，主体文字使用墨蓝 `#172033`。
- 交互主色使用钴蓝 `#315EF6`，成功工具结果使用青绿 `#168A72`，错误使用砖红 `#C24B43`，等待状态使用琥珀 `#C47A13`。
- 正文使用系统字体 `Segoe UI Variable, Segoe UI, sans-serif`。
- trace、参数和结果使用 `Cascadia Code, Consolas, monospace`。
- 主要识别元素是一条贯穿 trace 卡片的“循环轨道”，用节点连接 `USER → MODEL → TOOL → MODEL → ANSWER`，让循环关系比普通日志列表更直观。
- 动效只用于新 trace 节点出现和等待状态；尊重 `prefers-reduced-motion`。
- 所有按钮有键盘焦点样式，颜色不作为唯一状态提示。

### 3.3 交互

- 页面启动后选择最近更新的 session；没有 session 时自动创建一个。
- “新建会话”允许输入简短标题，成功后立即切换。
- 切换 session 后同时刷新聊天、trace 和待办。
- 两个快捷问题分别演示“天气 + 待办”和“周报 + 待办”，点击后只填入输入框，仍由用户按“发送”确认。
- 发送期间禁用输入和发送按钮，显示“Agent 正在思考”。
- 请求完成后一次性显示最终回答和最新 trace；首版不伪装流式输出。
- API 失败时在输入框上方显示可操作的错误说明，保留用户原输入便于重试。

## 4. 后端结构

新增内容：

```text
mini_agent/
├── config.py
├── web.py
└── web_static/
    ├── index.html
    ├── app.css
    └── app.js
tests/
└── test_web.py
```

`mini_agent.web` 负责：

- 解析 `--db`、`--host`、`--port` 和 `--no-browser`。
- 默认只绑定 `127.0.0.1:8000`。
- 启动后用标准库 `webbrowser` 打开页面。
- 只提供白名单内的三个静态文件，不能读取任意本地路径。
- 创建 HTTP handler，并把数据库路径与 runtime factory 注入 handler。
- 把异常转换为不含堆栈和 Key 的 JSON 错误。

把现有 CLI 中 `.env`、DeepSeek client、runtime 构建和秘密清理逻辑移动到 `mini_agent.config`，CLI 与网页共同调用。只移动已经存在的行为，不增加配置格式或改变优先级，避免复制两套会逐渐漂移的规则。

`SessionStore` 增加一个只读的 `list_messages(session_id, limit=100)`，供页面恢复聊天记录。它按消息 ID 正序返回最近 100 条审计记录，包括已压缩消息，但不改变 runtime 使用的 `context_messages()`。

## 5. HTTP API

所有响应使用 UTF-8 JSON。API 不启用 CORS，只供同源本地页面调用。

### `GET /api/state?session_id=<id>`

返回：

```json
{
  "current_session_id": "weather-chat",
  "sessions": [],
  "messages": [],
  "todos": [],
  "traces": []
}
```

- 未给 `session_id` 时选择最近更新的 session；如果数据库为空则创建一个。
- 给出的 session 不存在时返回 HTTP 404。
- `messages` 用于聊天区；工具消息不作为普通聊天气泡重复展示。
- `traces` 返回最近 40 条记录，保留 step、event、duration、工具名、参数和结果。每个顶层字符串字段在 API 层限制为 2,000 个字符，API Key 永不进入响应。

### `POST /api/sessions`

请求：

```json
{"title": "天气"}
```

创建 session 并返回新的 session ID。标题去除首尾空白，最多 80 个字符。

### `POST /api/chat`

请求：

```json
{
  "session_id": "weather-chat",
  "message": "查询杭州明天天气，并添加待办带雨伞"
}
```

处理顺序：

1. 校验 JSON、session 和非空消息。
2. 为当前请求打开独立 `SessionStore` 连接。
3. 复用现有 DeepSeek 配置和 `AgentRuntime`。
4. 调用 `runtime.run(session_id, message)`。
5. 重新读取当前 session 的消息、trace 和待办。
6. 返回最终回答与刷新后的 state。

请求正文上限为 32 KiB。聊天写操作使用进程内的单一锁，避免本地演示中两个请求同时写 SQLite、得到相同 turn ID。这个全局锁是首版的有意简化；只有真实多用户吞吐成为需求时才升级为每 session 锁。

## 6. 配置与安全

- 服务启动时从当前进程环境或项目根目录 `.env` 获取 DeepSeek 配置。
- API Key 只存在于 Python 进程，不写入 HTML、JavaScript、API 响应、trace 或浏览器存储。
- 页面没有“输入 Key”的控件。
- 认证、模型和工具异常只返回经过清理的简短中文错误，不返回 Python 堆栈。
- HTTP 服务默认只监听 `127.0.0.1`；用户显式传入其他 `--host` 时在终端显示风险提示。
- 静态路由采用白名单，不把 URL 直接拼成本地文件路径。
- 不接受浏览器指定数据库路径、工具名或其他 session 的待办归属。

## 7. 数据流

```text
浏览器输入
  → POST /api/chat
  → Web handler 校验
  → AgentRuntime.run()
  → DeepSeek 自主决定直接回答或调用 ToolRegistry
  → SessionStore 保存消息 / todo / trace
  → Web handler 读取当前 session state
  → 浏览器同时刷新聊天、循环轨迹和待办
```

切换 session 只会把选中的 ID 传给 `GET /api/state`。所有数据库查询继续带 session ID，所以网页不会改变已有隔离语义。

## 8. 错误处理

- JSON 损坏、字段缺失或消息为空：HTTP 400。
- 请求正文超过 32 KiB：HTTP 413。
- session 不存在：HTTP 404。
- 路由不存在或静态文件不在白名单：HTTP 404。
- API Key 缺失：HTTP 503，并明确提示在本地 `.env` 配置。
- `AgentError` 或 `LLMError`：HTTP 502，返回清理后的可读消息。
- 其他未预期的服务端错误：HTTP 500，只返回通用错误文字并在终端记录不含 Key 的错误类型。
- 浏览器网络失败：页面保留输入，显示“无法连接本地 Agent，请确认服务仍在运行”。

## 9. 测试

默认测试仍完全离线，不访问 DeepSeek。

新增测试至少覆盖：

1. 首页、CSS 和 JavaScript 可通过白名单路由读取。
2. 未知路径和路径穿越返回 404。
3. 空数据库请求 state 时创建首个 session。
4. 创建两个 session 后，消息、todo 和 trace 保持隔离。
5. 聊天 API 使用可注入的 FakeLLM/Runtime，不访问网络。
6. 请求 JSON 损坏、字段缺失、空消息和超大正文返回 400。
7. 缺少 Key 和 runtime 错误不会泄漏秘密或堆栈。
8. `list_messages()` 在关闭并重开数据库后仍能恢复聊天。
9. 原有 CLI、runtime、tools、store、LLM 和文档测试继续通过。

手动验收：

- 用真实 DeepSeek API 分别完成“天气 + 待办”和“周报 + 待办”。
- 切换并追问两个 session，确认聊天、trace 和待办不串线。
- 检查浏览器开发者工具、页面和录屏中没有 API Key。
- 在 1280px 桌面窗口与窄屏宽度检查布局、焦点和错误提示。

## 10. 完成标准

- `python -m mini_agent.web` 能启动本地页面并自动打开浏览器。
- 页面能创建、切换、恢复和继续 session。
- 真实聊天会调用现有 Agent Runtime，而不是 mock 前端数据。
- 工具调用循环、待办和 session 隔离在页面中清晰可见。
- API Key 不进入浏览器。
- 默认测试不联网且全部通过。
- CLI 行为和现有提交材料保持可用。
- 新 README 说明网页运行、录屏步骤和仍可使用的 CLI。
