# 最小可用 Agent 设计

日期：2026-07-27

## 1. 目标

从空目录实现一个可在终端运行的最小 Agent。核心循环、工具注册、模型输出解析、session 管理、context 压缩和 trace 均自行实现，不使用 LangGraph、OpenHands、OpenClaw 或其他 Agent 框架。

项目使用真实的 DeepSeek API：

- OpenAI-compatible Base URL：`https://api.deepseek.com`
- 模型：`deepseek-v4-pro`
- API Key：只从环境变量 `DEEPSEEK_API_KEY` 读取

运行时只依赖 Python 3.11+ 标准库。

## 2. 用户可以做什么

用户可以在终端中：

- 创建、列出和切换 session。
- 在指定 session 中持续聊天和追问。
- 让 Agent 自主决定直接回答或调用工具。
- 查看当前 session 的待办。
- 查看当前 session 最近的 Agent 执行 trace。
- 在两个终端窗口中使用不同 session，且两边的消息、摘要和待办互不影响。

CLI 命令：

- `/new [标题]`：创建并切换到新 session。
- `/sessions`：列出已有 session。
- `/use <session_id>`：切换 session。
- `/trace`：查看当前 session 最近的 trace。
- `/help`：显示帮助。
- `/exit`：退出。

程序也支持单次调用，方便测试和录屏：

```powershell
python -m mini_agent --session demo --once "计算 25 * 18"
```

## 3. 系统结构

```text
CLI
 └─ AgentRuntime
     ├─ DeepSeekClient
     ├─ OutputParser
     ├─ ToolRegistry
     ├─ SessionStore
     └─ TraceLogger
```

### 3.1 CLI

负责读取用户输入、处理以 `/` 开头的本地命令，并把普通消息交给 `AgentRuntime`。CLI 不负责判断是否调用工具。

### 3.2 AgentRuntime

负责 Agent 主循环。一次用户输入最多允许 8 次 LLM 请求，避免模型无限调用工具。

每一步执行：

1. 从当前 session 加载摘要和近期消息。
2. 把 system prompt、工具 Schema、历史摘要和近期消息发给 DeepSeek。
3. 解析返回消息中的 `reasoning_content`、`tool_calls` 和 `content`。
4. 如果存在工具调用，校验并执行工具，把工具结果加入消息，再继续循环。
5. 如果不存在工具调用，把 `content` 当作最终答案保存并返回。

DeepSeek 在思考模式下执行工具调用时，后续请求必须带回该 assistant 消息的 `reasoning_content`。Runtime 会完整保存并传回当前 context 中尚未压缩的工具调用消息。

### 3.3 DeepSeekClient

使用 `urllib.request` 直接调用 `/chat/completions`，不使用 OpenAI SDK。

请求包含：

- `model: deepseek-v4-pro`
- `thinking: {"type": "enabled"}`
- `reasoning_effort: high`
- `stream: false`
- 当前消息
- ToolRegistry 生成的 OpenAI function tools 列表

超时、HTTP 429 和 HTTP 5xx 最多重试两次。HTTP 401/403 立即返回清楚的配置错误。日志不得记录 API Key。

### 3.4 OutputParser

解析 `choices[0].message`：

- `reasoning_content`：保存到 trace；如果同一消息带有工具调用，则也保存到 session 消息中并在后续请求中传回。
- `tool_calls`：逐个解析调用 ID、工具名称和 JSON 参数。
- `content`：没有工具调用时作为最终答案。

如果响应中既没有工具调用也没有非空答案，则作为格式错误处理。

## 4. 工具注册

每个工具由一个 `Tool` 数据对象表示，包含：

- `name`：唯一名称。
- `description`：给 LLM 阅读的用途说明。
- `parameters`：JSON Schema。
- `handler`：真正执行工具的 Python 函数。

`ToolRegistry.register(tool)` 添加工具。重复名称会直接报错。`ToolRegistry.schemas()` 生成发给 DeepSeek 的工具列表。`ToolRegistry.execute()` 检查工具名称、参数 JSON、必填字段和基础类型后执行 handler。

首版只实现本项目需要的 JSON Schema 子集：`object`、`string`、`number`、`integer`、`boolean`、`array`、`required` 和 `enum`。

### 4.1 calculator

参数：

```json
{"expression": "25 * (18 + 2)"}
```

使用 Python AST 只允许数字、括号、加减乘除、取模和乘方。禁止名称、属性访问和函数调用，并限制表达式长度与乘方大小。

### 4.2 search

参数：

```json
{"query": "Agent Runtime"}
```

在项目内置的小型资料列表中进行大小写不敏感的关键词匹配，返回最多 5 条结果。结果明确标注为 mock search。

### 4.3 weather

参数：

```json
{"city": "杭州", "date": "2026-07-28"}
```

返回确定性的模拟天气数据，结果明确标注为 mock weather，便于无额外 API Key 的录屏和测试。

### 4.4 todo

参数：

```json
{"action": "add", "text": "带雨伞"}
```

支持：

- `add`：新增待办。
- `list`：列出当前 session 待办。
- `done`：按待办 ID 标记完成。

工具执行时由 Runtime 注入当前 session ID，模型不能替用户指定其他 session。

## 5. Session 和数据保存

使用一个 SQLite 文件保存数据，并启用 WAL 模式。数据库至少包含：

- `sessions`：ID、标题、滚动摘要、创建和更新时间。
- `messages`：session ID、角色、正文、reasoning、tool calls、tool call ID、是否已压缩和时间。
- `todos`：session ID、内容、完成状态和时间。
- `traces`：session ID、循环步数、事件类型、参数、结果、错误、耗时和时间。

所有消息、待办、摘要和 trace 查询都必须包含 session ID。两个终端窗口使用不同 session ID 时互不影响。

## 6. Context 和 memory

### 6.1 召回时机

每次收到用户普通消息后、第一次调用 LLM 前，从 SQLite 读取当前 session 的滚动摘要和未压缩消息。

### 6.2 放置顺序

发送给 LLM 的内容顺序为：

1. Agent system prompt。
2. 一条明确标记为“当前 session 历史摘要”的 system 消息。
3. 当前 session 最近的完整消息。
4. 当前用户输入或刚执行完成的工具结果。

工具 Schema 通过 Chat Completions 请求的 `tools` 字段传递，不拼成普通文本。

### 6.3 保存哪些内容

- 用户输入：保存并放入后续 context。
- 最终答案：保存并放入后续 context。
- 工具调用和工具结果：保存并放入后续 context。
- 带工具调用的 `reasoning_content`：保存并在该消息仍位于 context 时传回 DeepSeek。
- 不带工具调用的 `reasoning_content`：只保存到 trace，不放入后续 context。

### 6.4 基础压缩

当未压缩消息超过 24 条或总正文超过 12,000 个字符时触发：

1. 保留最近两个完整用户轮次。
2. 选取更早的完整轮次，不切断 tool call 和 tool result。
3. 用一次不带工具的 LLM 请求，把旧摘要和选中消息压成新摘要。
4. 摘要必须保留用户事实、重要决定、已完成操作、未完成事项和重要工具结果。
5. 更新 session 摘要，把已总结的消息标记为已压缩。
6. 原始消息不删除，仍可审计。

如果摘要请求失败，本轮继续使用最近消息，不删除或标记任何原始消息。

## 7. 错误处理

- API Key 缺失：启动真实聊天前给出环境变量设置方法。
- API 超时、429、5xx：重试两次并记录 trace。
- API 401/403：不重试，返回认证错误。
- 响应 JSON 损坏或缺少关键字段：返回可理解的模型响应错误。
- 工具名未知：生成失败的 tool result，包含可用工具名，让 LLM 有机会修正。
- 工具参数不是 JSON：生成失败的 tool result，让 LLM 有机会重新调用。
- 参数缺失或类型错误：返回具体字段错误。
- handler 抛出异常：捕获并写入 trace，返回不包含 Python 堆栈的工具错误。
- 超过 8 次循环：保存 trace 并返回最大步数错误。
- SQLite 写入：使用事务，失败时回滚。

## 8. Trace

每次 LLM 请求和工具执行记录：

- 时间和 session ID。
- 当前循环步数。
- 事件类型。
- 模型返回的 reasoning、content 和 tool call 概要。
- 工具名称、参数、结果和耗时。
- API 或工具错误。

`/trace` 只显示当前 session 最近记录。API Key 永不进入 trace。过长的 reasoning 和工具结果在显示时截断，但数据库保留原值。

## 9. 测试

默认测试不访问网络，使用可编排返回顺序的 `FakeLLM`。

测试覆盖：

1. 没有工具调用时直接返回。
2. calculator 单工具调用。
3. weather 后继续调用 todo。
4. 多工具调用参数的解析和 Schema 校验。
5. 普通聊天追问能读到先前消息。
6. 工具型追问能继续调用工具。
7. 两个 session 的消息、摘要和 todo 隔离。
8. 错误 JSON、未知工具、缺少参数和 handler 异常可以恢复。
9. 连续调用在第 8 次后停止。
10. 超长 context 触发摘要，并保留最近两个完整轮次。
11. trace 按 session 保存和查询。
12. SQLite 关闭后重新打开仍能继续已有 session。

另提供需要 `DEEPSEEK_API_KEY` 的真实 API 冒烟测试。它验证 `deepseek-v4-pro` 可以直接回答，并可以自主调用 calculator。该测试不会在默认测试命令中自动运行。

## 10. 提交内容

- 可运行的 Python 源码与自动测试。
- `README.md`：安装、配置、运行、测试、系统设计、memory 召回时机与放置方式。
- `PROMPTS.md`：Agent system prompt 和 context 压缩 prompt。
- `PROBLEM_SOLVING.md`：真实记录开发中遇到的问题、原因和解决方案。
- `.env.example`：只含变量名和非秘密示例。
- `demo.ps1`：录屏演示步骤。
- `artifacts/demo.mp4`：在配置真实 API Key 后录制的终端演示。
- Git 提交历史。

本机若已有可用 GitHub 登录，则在完成和验证后创建远程仓库并提供代码链接；否则提供一组可复制的推送命令。

实际录屏必须在本机设置 `DEEPSEEK_API_KEY` 后进行，且录屏与日志中不得出现密钥。

## 11. 不包含的内容

首版不实现网页界面、多用户权限系统、向量数据库、语义检索、流式输出、真实搜索 API、真实天气 API、并行工具调用优化或分布式执行。这些功能不影响本题要求，可在后续确有需要时增加。

## 12. 验收标准

满足以下条件才算完成：

- 核心循环未使用任何 Agent 框架。
- 使用真实 `deepseek-v4-pro` API 的代码路径可运行。
- LLM 根据注册工具 Schema 自主决定工具调用。
- Runtime 能解析 reasoning、tool calls 和最终答案。
- 四个工具均可被 LLM 调用。
- session 可持久保存、恢复并互相隔离。
- 支持普通追问和工具型追问。
- 有最大循环限制和基础 context 压缩。
- 有基本异常处理与可查询 trace。
- 默认自动测试全部通过。
- README、Prompt、问题解决记录、录屏脚本和实际录屏齐全。

## 13. 接口参考

- DeepSeek API 入门：<https://api-docs.deepseek.com/>
- DeepSeek 思考模式与工具调用：<https://api-docs.deepseek.com/guides/thinking_mode/>
