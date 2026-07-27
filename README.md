# Minimal Agent：从终端开始理解 Agent

这是一个只有 Python 标准库的最小 Agent 示例。它会把你的问题交给 DeepSeek；模型可以直接回答，也可以选择调用四个受限制的工具：计算、内置资料搜索、模拟天气和待办。消息、待办、摘要和执行记录都保存到本地 SQLite 数据库，重新启动后仍可继续同一个会话。

## 先认识四个词

- **session（会话）**：一条独立的聊天线，例如 `weather-chat`。每条线各自保存消息、待办、摘要和 trace，互相看不到。
- **context（上下文）**：本次请求随模型一起发送的资料包；它让模型知道当前会话刚才说过什么、工具做了什么。
- **memory（记忆）**：过长历史的滚动摘要。它是 context 的一部分，但不是全部历史的原文。
- **trace（执行轨迹）**：程序的工作日志，记录模型回应、工具调用、耗时和可恢复错误，便于核对 Agent 实际做过什么。

## 为什么没有使用 Agent 框架

这里没有 LangGraph、OpenHands、OpenClaw 或其他 Agent 框架。目的不是重复造大型框架，而是把最小的 Agent 循环摊开：自己发模型请求、自己校验工具参数、自己保存 session、自己组织 context。这样初学者能看清每一步的数据在哪里、为何调用工具，以及出错时如何追踪。运行时只需要 Python 3.11+ 标准库，不需要安装第三方依赖。

## 1. 检查 Python

项目要求 Python 3.11 或更高版本。先在 PowerShell 运行：

```powershell
python --version
```

如果此电脑的 `python` 没有指向可用解释器，可把下文每条 `python` 替换为：

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' --version
```

后文仍以通用的 `python` 写法为主，便于复制到其他电脑。

## 2. 在当前 PowerShell 临时配置 DeepSeek

这些变量只对当前 PowerShell 窗口有效；关闭窗口后需要重新设置。将第一行的占位文本替换为自己的 Key，**不要**把真实 Key 写入代码、截图、提交或聊天记录。

```powershell
$env:DEEPSEEK_API_KEY = "替换为你的 Key"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "deepseek-v4-pro"
```

也可以将变量名与非秘密示例复制自 [.env.example](.env.example)，但本程序只从 PowerShell 环境变量读取 Key，并不会自动读取 `.env` 文件。

## 3. 启动、指定会话、单次调用和测试

在项目根目录执行：

```powershell
# 启动交互模式：未指定时创建一个随机 session
python -m mini_agent

# 打开指定 session；不存在则创建，存在则续聊
python -m mini_agent --session weather-chat

# 单次提问后退出，适合核对或录屏
python -m mini_agent --session demo --once "计算 25 * 18"

# 运行全部离线自动测试；不会访问网络
python -m unittest discover -v
```

本机可直接这样运行同一套测试：

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest discover -v
```

默认数据库是 `.agent-data/agent.db`；可用 `--db .agent-data/another.db` 改到另一个 SQLite 文件。交互模式中的本地命令有 `/new [标题]`、`/sessions`、`/use <session_id>`、`/trace`、`/help` 和 `/exit`。只有普通聊天消息才需要 Key；因此可以先启动后用 `/help` 熟悉命令。

## 四个工具：模型可选用的能力

工具由模型按请求决定是否调用；你通常用自然语言描述目标，而不是手动粘贴 JSON。下列 JSON 是模型实际会收到的参数形状：

| 工具 | 参数示例 | 数据来源与边界 |
| --- | --- | --- |
| `calculator` | `{"expression":"25 * (18 + 2)"}` | 仅计算受限数学表达式，不能执行 Python 代码。 |
| `search` | `{"query":"Agent Runtime"}` | **mock**：只搜索项目内置的小型资料，不访问互联网。 |
| `weather` | `{"city":"杭州","date":"2026-07-28"}` | **mock**：按输入稳定生成天气，不是实时天气。 |
| `todo` | `{"action":"add","text":"带雨伞"}`；`{"action":"list"}`；`{"action":"done","id":1}` | 待办只属于当前 session；模型不能指定另一个 session。 |

## Agent 如何完成一次问题：8 步上限的循环

一次普通输入先被保存为本轮用户消息。程序在第一次模型请求前组装 context，然后最多重复 8 次以下过程：

1. 从 SQLite 取当前 session 的摘要和未压缩消息。
2. 发送 system prompt、当前日期、摘要、历史消息和工具 Schema 给 DeepSeek。
3. 读取模型返回的 `reasoning_content`、`tool_calls` 与 `content`。
4. 若模型要求工具，先解析并校验 JSON 参数。
5. 执行一个或多个工具；未知工具或坏参数会变成带 `ok: false` 的工具结果，返回给模型修正。
6. 保存工具调用、工具结果和 trace，再把它们作为下一次循环的 context。
7. 如果模型给出不带工具调用的普通 `content`，保存并显示它，循环结束。
8. 若连续工具调用超过 8 步，停止并记录 `loop_limit` trace，避免无限循环。

## 两个终端验证 session 隔离

打开两个 PowerShell 窗口，并在两个窗口都完成上面的环境变量配置。然后分别运行：

```powershell
# 终端 1
python -m mini_agent --session weather-chat

# 终端 2
python -m mini_agent --session weekly-report
```

在终端 1 输入“查询杭州明天天气；如有雨，添加待办带雨伞”。在终端 2 输入“生成一份本周工作周报，并添加待办检查周报”。退出后，以相同的 `--session` 再打开两个窗口继续提问。两个 session 的消息、摘要、待办和 trace 都应保持隔离。

## memory 什么时候召回

每收到一条普通用户消息后、**第一次调用模型之前**，程序都会从 SQLite 读取当前 session 的 memory（若有）以及近期未压缩消息。只有当前 session 的资料会被取出，所以 `weather-chat` 的内容不会进入 `weekly-report`。

## memory 放在 context 的什么位置

发给模型的顺序是：

1. 第一条 system 消息：Agent 的固定规则和当天日期；
2. 第二条 system 消息：明确标记为当前 **Session 记忆**的摘要（memory）；
3. 当前 session 近期完整消息，包括用户、助手和工具消息。

工具 Schema 通过 API 的 `tools` 字段传递，不会拼成普通聊天文本。这样固定规则优先，记忆提供较早背景，最近原文保留最精确的上下文。

带工具调用的 assistant 消息会连同 `reasoning_content` 保存在 context 内：DeepSeek 的 thinking 工具调用需要它来继续同一条调用链。相反，普通最终回答的 reasoning 不放回 context，因为它不需要延续工具调用；只把它保存到 trace，能减少无关输入、上下文占用和不必要的思考内容回传。

## 何时压缩、保留什么、原始消息是否删除

当未压缩消息超过 24 条，或其正文合计超过 12,000 个字符时，程序在本轮开始时尝试压缩。它保留最近两个完整用户轮次；更早内容必须按完整轮次一起压缩，绝不会把工具调用和对应工具结果切开。摘要保留用户事实、重要决定、已完成操作、未完成事项和重要工具结果，并且不得编造原文没有的信息。

压缩成功后，早期原始消息只被标记为“已压缩”，**不会删除**，仍可审计；下一次 context 改用摘要加上近期完整消息。摘要请求失败或结果无效时，本轮继续使用原资料，也不会删除或标记任何消息。

## 查看 trace

在交互模式输入：

```text
/trace
```

它只显示当前 session 最近的 trace，例如模型回应、工具名称/参数/结果、错误类型和耗时。显示时会截断很长数据；数据库保留原值。API Key 不应出现在 trace 中。

## 常见错误

| 现象 | 先做什么 |
| --- | --- |
| 提示 `DEEPSEEK_API_KEY is required` | 在**同一个** PowerShell 窗口按上文设置 Key，再重试。 |
| HTTP 401 / 403 | Key 无效、过期或没有权限；检查 Key 和账号配置。程序不会重试认证错误。 |
| HTTP 429 | 服务繁忙或限流；客户端会最多重试两次，仍失败请稍后重试。 |
| 超时、HTTP 5xx 或网络错误 | 客户端会最多重试两次；确认网络和 Base URL 后重试。 |
| `Agent` 超过最大循环步数 | 模型连续请求工具超过 8 步；用 `/trace` 查看最后调用，改写问题使目标更明确后再试。 |

## 文件与限制

- `mini_agent/cli.py`：命令行入口、参数和交互命令。
- `mini_agent/runtime.py`：8 步 Agent 循环、context 组装、压缩和 trace。
- `mini_agent/store.py`：SQLite session、消息、摘要、待办和 trace。
- `mini_agent/tools.py`：四个工具及参数校验。
- `mini_agent/llm.py`：DeepSeek HTTP 请求、解析和重试。
- `PROMPTS.md`：运行时使用的两个 prompt 原文与约束说明。
- `PROBLEM_SOLVING.md`：只记录本项目开发中真实发生过的问题。
- `demo.ps1`：不显示 Key 的双 session 录屏提示。

首版不包含网页界面、多用户权限、向量数据库或语义检索、流式输出、真实搜索 API、真实天气 API、并行工具调用或分布式执行。`search` 与 `weather` 是 mock，不能当作实时外部事实。

## 真实 API 冒烟测试（会访问网络并产生 API 使用）

默认单元测试不会访问网络。确认 Key 已在当前窗口设置后，再单独运行下列命令；录屏和终端输出中不要展示 Key：

```powershell
# 直接回答路径
python -m mini_agent --session live-direct --once "只回答：连接成功"

# 要求调用受限计算器；模型仍会自行决定调用细节
python -m mini_agent --session live-tools --once "请务必使用 calculator 工具计算 25 * 18，然后告诉我结果"
```

若要录制两个 session 的完整演示，可先执行 `./demo.ps1` 获取逐步提示，再分别按“两个终端验证 session 隔离”的命令操作，最后运行 `python -m unittest discover -v`。
