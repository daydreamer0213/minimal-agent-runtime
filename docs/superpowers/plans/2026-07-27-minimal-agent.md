# 最小可用 Agent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零实现一个使用真实 DeepSeek V4 Pro API、支持工具循环、独立 session、context 压缩、异常处理和 trace 的 Python CLI Agent。

**Architecture:** CLI 把用户消息交给 `AgentRuntime`。Runtime 从 SQLite 读取当前 session 的记忆，通过标准库 HTTP 客户端请求 DeepSeek，解析直接回答或工具调用，再通过 `ToolRegistry` 执行工具并继续循环。

**Tech Stack:** Python 3.11+ 标准库、SQLite、`urllib.request`、`unittest`、DeepSeek OpenAI-compatible Chat Completions API。

## Global Constraints

- 核心 Agent Runtime 不得依赖 LangGraph、OpenHands、OpenClaw 或其他 Agent 框架。
- Python 运行时代码不得增加第三方依赖。
- 默认模型必须是 `deepseek-v4-pro`，默认 Base URL 必须是 `https://api.deepseek.com`。
- API Key 只允许从 `DEEPSEEK_API_KEY` 读取，不得写入源码、数据库、日志或录屏。
- 一次用户输入最多执行 8 次 LLM 循环。
- calculator、search、weather、todo 四个工具必须通过统一注册机制提供名称、描述、参数 Schema 和 handler。
- 自动测试默认不得访问网络或消耗 API 额度。

---

## 文件结构

```text
mini_agent/
├─ __init__.py       包版本
├─ __main__.py       `python -m mini_agent` 入口
├─ cli.py            参数解析、交互命令和终端输出
├─ llm.py            DeepSeek HTTP 请求和响应解析
├─ prompts.py        Agent prompt 与压缩 prompt
├─ runtime.py        Agent 循环和 context 压缩
├─ store.py          SQLite session、消息、todo 和 trace
└─ tools.py          工具对象、注册表、Schema 校验和四个工具
tests/
├─ __init__.py
├─ test_llm.py
├─ test_cli.py
├─ test_docs.py
├─ test_runtime.py
├─ test_store.py
└─ test_tools.py
artifacts/
└─ demo.mp4
.env.example
.gitignore
demo.ps1
PROMPTS.md
PROBLEM_SOLVING.md
README.md
pyproject.toml
```

## Task 1: SQLite session、消息、待办和 trace

**Files:**

- Create: `pyproject.toml`
- Create: `mini_agent/__init__.py`
- Create: `mini_agent/store.py`
- Create: `tests/__init__.py`
- Create: `tests/test_store.py`
- Create: `.gitignore`

**Interfaces:**

- Produces: `SessionStore(db_path)`.
- Produces: `create_session(title="", session_id=None) -> str`.
- Produces: `list_sessions() -> list[dict]`.
- Produces: `add_message(session_id, turn_id, role, content="", reasoning_content="", tool_calls=None, tool_call_id=None) -> int`.
- Produces: `context_messages(session_id) -> list[dict]`.
- Produces: `session_exists(session_id) -> bool` 和 `next_turn_id(session_id) -> int`.
- Produces: `add_todo(session_id, text) -> dict`, `list_todos(session_id) -> list[dict]`, `finish_todo(session_id, todo_id) -> bool`.
- Produces: `context_stats(session_id) -> tuple[int, int]`, `compactable_messages(session_id, keep_user_turns) -> list[dict]`.
- Produces: `get_summary(session_id) -> str` 和 `save_summary_and_compact(session_id, summary, message_ids)`.
- Produces: `add_trace(session_id, step, event, data, duration_ms=0)` and `list_traces(session_id, limit=20)`.

- [ ] **Step 1: 写 session 隔离和持久化的失败测试**

```python
# tests/test_store.py
import tempfile
import unittest
from pathlib import Path

from mini_agent.store import SessionStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "agent.db"
        self.store = SessionStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sessions_keep_messages_and_todos_separate(self):
        first = self.store.create_session("天气", "weather-chat")
        second = self.store.create_session("周报", "weekly-report")
        self.store.add_message(first, 1, "user", "杭州天气如何？")
        self.store.add_message(second, 1, "user", "帮我写周报")
        self.store.add_todo(first, "带雨伞")
        self.store.add_todo(second, "整理数据")

        self.assertEqual(
            [message["content"] for message in self.store.context_messages(first)],
            ["杭州天气如何？"],
        )
        self.assertEqual(
            [item["text"] for item in self.store.list_todos(first)],
            ["带雨伞"],
        )
        self.assertEqual(
            [item["text"] for item in self.store.list_todos(second)],
            ["整理数据"],
        )

    def test_data_survives_reopening_database(self):
        session_id = self.store.create_session("持久化", "persistent")
        self.store.add_message(session_id, 1, "assistant", "记住这句话")
        self.store.close()
        self.store = SessionStore(self.db_path)

        messages = self.store.context_messages(session_id)

        self.assertEqual(messages[0]["content"], "记住这句话")

    def test_compaction_marks_whole_old_turns(self):
        session_id = self.store.create_session("长对话", "long-chat")
        for turn in range(1, 5):
            self.store.add_message(session_id, turn, "user", f"问题 {turn}")
            self.store.add_message(session_id, turn, "assistant", f"回答 {turn}")

        rows = self.store.compactable_messages(session_id, keep_user_turns=2)
        self.store.save_summary_and_compact(
            session_id,
            "前两轮摘要",
            [row["id"] for row in rows],
        )

        self.assertEqual(self.store.get_summary(session_id), "前两轮摘要")
        self.assertEqual(
            [message["content"] for message in self.store.context_messages(session_id)],
            ["问题 3", "回答 3", "问题 4", "回答 4"],
        )

    def test_trace_is_filtered_by_session(self):
        first = self.store.create_session("一", "one")
        second = self.store.create_session("二", "two")
        self.store.add_trace(first, 1, "tool", {"name": "calculator"})
        self.store.add_trace(second, 1, "tool", {"name": "weather"})

        traces = self.store.list_traces(first)

        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["data"]["name"], "calculator")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认它因为模块尚不存在而失败**

Run:

```powershell
python -m unittest tests.test_store -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent'`。

- [ ] **Step 3: 添加最小项目配置和 SQLite 实现**

```toml
# pyproject.toml
[project]
name = "minimal-agent"
version = "0.1.0"
description = "A minimal Agent runtime built without Agent frameworks"
requires-python = ">=3.11"
dependencies = []
```

```python
# mini_agent/__init__.py
__version__ = "0.1.0"
```

```python
# tests/__init__.py
```

`mini_agent/store.py` 使用以下数据表和事务边界：

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning_content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    compacted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    step INTEGER NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
```

实现要点：

- 连接后执行 `PRAGMA foreign_keys=ON` 和 `PRAGMA journal_mode=WAL`。
- 时间统一使用 `datetime.now(timezone.utc).isoformat()`。
- 自动 session ID 使用 `uuid.uuid4().hex[:12]`。
- 自定义 session ID 必须匹配 `[A-Za-z0-9_-]{1,64}`。
- `context_messages()` 把数据库字段还原为 Chat Completions message；只有非空字段才放入字典。
- `compacted=0` 的消息才进入 context。
- `compactable_messages()` 查询除最近 N 个不同 `turn_id` 外的完整旧轮次。
- `save_summary_and_compact()` 在同一事务中更新摘要并标记消息。
- `finish_todo()` 的 SQL 必须同时包含 `id` 和 `session_id`。
- JSON 字段统一使用 `json.dumps(value, ensure_ascii=False)` 和 `json.loads(text)`。

```gitignore
# .gitignore
__pycache__/
*.py[cod]
.agent-data/
.env
```

- [ ] **Step 4: 运行 store 测试并确认通过**

Run:

```powershell
python -m unittest tests.test_store -v
```

Expected: `Ran 4 tests` 和 `OK`。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add pyproject.toml .gitignore mini_agent/__init__.py mini_agent/store.py tests/__init__.py tests/test_store.py
git commit -m "feat: add persistent session store"
```

## Task 2: 工具注册、Schema 校验和四个工具

**Files:**

- Create: `mini_agent/tools.py`
- Create: `tests/test_tools.py`

**Interfaces:**

- Consumes: `SessionStore`.
- Produces: `Tool(name, description, parameters, handler)`.
- Produces: `ToolRegistry.register()`, `schemas()` 和 `execute()`.
- Produces: `build_default_registry() -> ToolRegistry`.

- [ ] **Step 1: 写工具行为和安全限制的失败测试**

```python
# tests/test_tools.py
import tempfile
import unittest
from pathlib import Path

from mini_agent.store import SessionStore
from mini_agent.tools import build_default_registry


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "agent.db")
        self.session_id = self.store.create_session("工具", "tools")
        self.registry = build_default_registry()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def execute(self, name, arguments):
        return self.registry.execute(name, arguments, self.session_id, self.store)

    def test_registry_exposes_openai_function_schemas(self):
        schemas = self.registry.schemas()
        names = {item["function"]["name"] for item in schemas}
        self.assertEqual(names, {"calculator", "search", "weather", "todo"})
        self.assertTrue(all(item["type"] == "function" for item in schemas))

    def test_calculator_evaluates_arithmetic(self):
        result = self.execute("calculator", {"expression": "25 * (18 + 2)"})
        self.assertEqual(result, {"ok": True, "result": 500})

    def test_calculator_rejects_python_code(self):
        result = self.execute("calculator", {"expression": "__import__('os').getcwd()"})
        self.assertFalse(result["ok"])
        self.assertIn("不支持", result["error"])

    def test_schema_reports_missing_argument(self):
        result = self.execute("weather", {"city": "杭州"})
        self.assertEqual(result["ok"], False)
        self.assertIn("date", result["error"])

    def test_weather_is_deterministic_and_marked_as_mock(self):
        first = self.execute("weather", {"city": "杭州", "date": "2026-07-28"})
        second = self.execute("weather", {"city": "杭州", "date": "2026-07-28"})
        self.assertEqual(first, second)
        self.assertEqual(first["source"], "mock")

    def test_todo_is_scoped_to_current_session(self):
        added = self.execute("todo", {"action": "add", "text": "带雨伞"})
        listed = self.execute("todo", {"action": "list"})
        self.assertTrue(added["ok"])
        self.assertEqual(listed["items"][0]["text"], "带雨伞")

    def test_unknown_tool_returns_recoverable_error(self):
        result = self.execute("missing", {})
        self.assertFalse(result["ok"])
        self.assertIn("calculator", result["available_tools"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认缺少 tools 模块**

Run:

```powershell
python -m unittest tests.test_tools -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.tools'`。

- [ ] **Step 3: 实现注册表和工具**

`mini_agent/tools.py` 必须包含：

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, str, SessionStore], dict]
```

`ToolRegistry` 行为：

```python
class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name, arguments, session_id, store):
        tool = self._tools.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"未知工具: {name}",
                "available_tools": sorted(self._tools),
            }
        try:
            validate(arguments, tool.parameters)
            return tool.handler(arguments, session_id, store)
        except (ValueError, TypeError, ArithmeticError) as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": f"工具执行失败: {type(error).__name__}"}
```

具体工具规则：

- calculator 使用 `ast.parse(expression, mode="eval")`。
- 只接受 `ast.Constant` 数字、`ast.UnaryOp` 的正负号，以及 `Add/Sub/Mult/Div/FloorDiv/Mod/Pow`。
- 表达式最长 200 字符，指数绝对值不超过 20。
- search 在 6 条内置中文资料中按查询词分词匹配，返回最多 5 条并带 `"source": "mock"`。
- weather 用 `hashlib.sha256(f"{city}|{date}".encode()).digest()` 选择固定天气和温度，返回 `"source": "mock"`。
- todo 的 `add` 要求非空 `text`，`list` 不要求其他字段，`done` 要求正整数 `id`。
- `build_default_registry()` 逐一注册四个工具。

- [ ] **Step 4: 运行工具测试并确认通过**

Run:

```powershell
python -m unittest tests.test_tools -v
```

Expected: `Ran 7 tests` 和 `OK`。

- [ ] **Step 5: 提交 Task 2**

```powershell
git add mini_agent/tools.py tests/test_tools.py
git commit -m "feat: add registered agent tools"
```

## Task 3: DeepSeek HTTP 客户端和输出解析

**Files:**

- Create: `mini_agent/llm.py`
- Create: `tests/test_llm.py`

**Interfaces:**

- Produces: `ToolCall(id, name, arguments)`.
- Produces: `LLMResponse(content, reasoning_content, tool_calls)`.
- Produces: `LLMError` 和 `LLMAuthError(LLMError)`.
- Produces: `parse_response(payload) -> LLMResponse`.
- Produces: `DeepSeekClient.chat(messages, tools) -> LLMResponse`.

- [ ] **Step 1: 写模型输出解析和 HTTP 请求的失败测试**

```python
# tests/test_llm.py
import json
import unittest
from unittest.mock import patch

from mini_agent.llm import DeepSeekClient, LLMError, parse_response


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class LLMTests(unittest.TestCase):
    def test_parser_extracts_reasoning_tool_calls_and_content(self):
        payload = {
            "choices": [{
                "message": {
                    "reasoning_content": "需要精确计算",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calculator",
                            "arguments": '{"expression":"2+2"}',
                        },
                    }],
                }
            }]
        }

        result = parse_response(payload)

        self.assertEqual(result.reasoning_content, "需要精确计算")
        self.assertEqual(result.tool_calls[0].name, "calculator")
        self.assertEqual(result.tool_calls[0].arguments, '{"expression":"2+2"}')

    def test_parser_rejects_empty_message(self):
        with self.assertRaises(LLMError):
            parse_response({"choices": [{"message": {}}]})

    @patch("mini_agent.llm.urlopen")
    def test_client_sends_deepseek_configuration(self, urlopen):
        urlopen.return_value = FakeHTTPResponse({
            "choices": [{"message": {"content": "你好"}}]
        })
        client = DeepSeekClient("secret")

        result = client.chat([{"role": "user", "content": "你好"}], [])

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(result.content, "你好")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认缺少 llm 模块**

Run:

```powershell
python -m unittest tests.test_llm -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.llm'`。

- [ ] **Step 3: 实现响应数据对象、解析器和客户端**

`mini_agent/llm.py` 的公开数据对象：

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_api_dict(self):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class LLMResponse:
    content: str
    reasoning_content: str
    tool_calls: list[ToolCall]
```

`parse_response()` 必须：

- 检查 `choices` 是非空列表。
- 检查第一个 choice 有字典类型的 `message`。
- 把缺失或 `None` 的 `content`、`reasoning_content` 转为空字符串。
- 检查每个 tool call 的 ID、函数名和参数字符串。
- 同时没有非空 content 和 tool calls 时抛出 `LLMError("模型没有返回答案或工具调用")`。

`DeepSeekClient` 必须：

- 构造函数支持 `api_key`、`base_url`、`model`、`timeout=60`、`max_retries=2`。
- 请求头包含 `Content-Type: application/json` 和 `Authorization: Bearer <key>`。
- 请求体包含 model、messages、thinking、reasoning_effort 和 stream。
- 只有 tools 非空时才添加 `tools`。
- 使用 `urlopen(request, timeout=self.timeout)`。
- 对 `HTTPError` 401/403 抛出不包含 key 的 `LLMAuthError`。
- 对 429、500、502、503、504 和 `URLError` 重试，等待时间依次为 0.5 秒和 1 秒。
- 其他错误包装为 `LLMError`。

- [ ] **Step 4: 运行 llm 测试并确认通过**

Run:

```powershell
python -m unittest tests.test_llm -v
```

Expected: `Ran 3 tests` 和 `OK`。

- [ ] **Step 5: 提交 Task 3**

```powershell
git add mini_agent/llm.py tests/test_llm.py
git commit -m "feat: call and parse DeepSeek API"
```

## Task 4: Agent 基本循环、追问和 context 压缩

**Files:**

- Create: `mini_agent/prompts.py`
- Create: `mini_agent/runtime.py`
- Create: `tests/test_runtime.py`

**Interfaces:**

- Consumes: `SessionStore`, `ToolRegistry`, `LLMResponse`.
- Produces: `AgentRuntime.run(session_id, user_input) -> str`.
- Produces: `AgentError` 和 `AgentLoopLimitError(AgentError)`.

- [ ] **Step 1: 写直接回答和多工具循环的失败测试**

```python
# tests/test_runtime.py
import tempfile
import unittest
from pathlib import Path

from mini_agent.llm import LLMResponse, ToolCall
from mini_agent.runtime import AgentLoopLimitError, AgentRuntime
from mini_agent.store import SessionStore
from mini_agent.tools import build_default_registry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "agent.db")
        self.session = self.store.create_session("测试", "test-session")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def runtime(self, responses, **options):
        fake = FakeLLM(responses)
        runtime = AgentRuntime(
            self.store,
            fake,
            build_default_registry(),
            **options,
        )
        return runtime, fake

    def test_returns_direct_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("你好，我能帮你使用工具。", "直接回答即可", [])
        ])

        answer = runtime.run(self.session, "你好")

        self.assertEqual(answer, "你好，我能帮你使用工具。")
        self.assertEqual(fake.calls[0]["messages"][-1]["content"], "你好")

    def test_weather_then_todo_then_final_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("", "先查天气", [
                ToolCall("call_weather", "weather", '{"city":"杭州","date":"2026-07-28"}')
            ]),
            LLMResponse("", "根据天气记待办", [
                ToolCall("call_todo", "todo", '{"action":"add","text":"带雨伞"}')
            ]),
            LLMResponse("杭州天气为模拟小雨，已添加“带雨伞”。", "结果齐全", []),
        ])

        answer = runtime.run(self.session, "查杭州明天天气，如果下雨就提醒我带伞")

        self.assertIn("已添加", answer)
        self.assertEqual(self.store.list_todos(self.session)[0]["text"], "带雨伞")
        second_messages = fake.calls[1]["messages"]
        assistant = next(message for message in second_messages if message.get("tool_calls"))
        self.assertEqual(assistant["reasoning_content"], "先查天气")
        self.assertTrue(any(message["role"] == "tool" for message in second_messages))

    def test_bad_tool_arguments_are_returned_to_model(self):
        runtime, fake = self.runtime([
            LLMResponse("", "参数写错了", [
                ToolCall("bad", "calculator", "{not-json")
            ]),
            LLMResponse("工具参数无效，请重新描述。", "解释错误", []),
        ])

        answer = runtime.run(self.session, "计算")

        self.assertIn("参数无效", answer)
        tool_message = next(
            message for message in fake.calls[1]["messages"]
            if message["role"] == "tool"
        )
        self.assertIn('"ok": false', tool_message["content"])

    def test_stops_after_maximum_steps(self):
        calls = [
            LLMResponse("", "继续", [
                ToolCall(f"call_{index}", "calculator", '{"expression":"1+1"}')
            ])
            for index in range(3)
        ]
        runtime, fake = self.runtime(calls, max_steps=3)

        with self.assertRaises(AgentLoopLimitError):
            runtime.run(self.session, "一直计算")

        self.assertEqual(len(fake.calls), 3)
```

- [ ] **Step 2: 运行测试并确认缺少 runtime 模块**

Run:

```powershell
python -m unittest tests.test_runtime -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.runtime'`。

- [ ] **Step 3: 实现 prompt 和最小 Agent 循环**

`mini_agent/prompts.py` 定义两个常量：

```python
AGENT_SYSTEM_PROMPT = """你是一个最小可用 Agent。
根据用户请求决定直接回答或调用工具。
需要外部事实、精确计算、天气或待办操作时，应使用合适的工具。
工具结果可能失败；先检查 ok 字段，再决定修正参数、换工具或向用户解释。
不要声称 mock search 或 mock weather 是实时数据。
回答使用用户使用的语言，简洁说明完成了什么。"""

COMPRESSION_PROMPT = """把旧对话压缩为可供后续对话使用的 session 记忆。
必须保留：用户事实、重要决定、已经完成的操作、尚未完成的事项、重要工具结果。
不要添加原文没有的信息。只输出摘要正文。"""
```

`AgentRuntime.run()` 使用下面的固定顺序：

```text
确认 session 存在
取得下一个 turn_id
保存 user 消息
如果超过阈值则压缩旧完整轮次
循环 step = 1..max_steps
  组装 system、summary 和未压缩消息
  调用 llm.chat
  写 model_response trace
  如果有 tool_calls
    保存带 reasoning_content 的 assistant 工具调用消息
    逐个解析 arguments
    调用 registry.execute
    保存 tool 消息和 tool trace
    继续循环
  如果有 content
    保存不带 reasoning_content 的最终 assistant 消息
    写 final trace
    返回 content
抛出 AgentLoopLimitError
```

组装第一条 system message 时，在 `AGENT_SYSTEM_PROMPT` 后追加
`date.today().isoformat()`，让模型知道程序运行当天的日期。

工具参数 JSON 失败时使用：

```python
result = {
    "ok": False,
    "error": f"工具参数不是合法 JSON: {error.msg}",
}
```

工具结果统一通过以下方式保存，保证中文可读并可再次解析：

```python
json.dumps(result, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 4: 运行基本 Runtime 测试并确认通过**

Run:

```powershell
python -m unittest tests.test_runtime -v
```

Expected: `Ran 4 tests` 和 `OK`。

- [ ] **Step 5: 增加追问、session 隔离和压缩的失败测试**

在 `tests/test_runtime.py` 增加：

```python
    def test_follow_up_receives_previous_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("杭州明天可能下雨。", "回答天气", []),
            LLMResponse("我刚才说杭州明天可能下雨。", "读取历史", []),
        ])
        runtime.run(self.session, "杭州明天天气呢？")

        answer = runtime.run(self.session, "你刚才说什么？")

        self.assertIn("可能下雨", answer)
        contents = [
            message.get("content", "")
            for message in fake.calls[1]["messages"]
        ]
        self.assertIn("杭州明天可能下雨。", contents)

    def test_two_sessions_do_not_share_context(self):
        other = self.store.create_session("另一个", "other")
        runtime, fake = self.runtime([
            LLMResponse("窗口一回答", "回答", []),
            LLMResponse("窗口二回答", "回答", []),
        ])
        runtime.run(self.session, "窗口一秘密")
        runtime.run(other, "窗口二问题")

        second_contents = " ".join(
            message.get("content", "")
            for message in fake.calls[1]["messages"]
        )
        self.assertNotIn("窗口一秘密", second_contents)

    def test_long_context_is_summarized_without_splitting_recent_turns(self):
        for turn in range(1, 4):
            self.store.add_message(self.session, turn, "user", "很长问题" * 20)
            self.store.add_message(self.session, turn, "assistant", "很长回答" * 20)
        runtime, fake = self.runtime([
            LLMResponse("早期对话摘要", "总结", []),
            LLMResponse("已接着回答", "使用摘要", []),
        ], context_chars=100, context_messages=2)

        answer = runtime.run(self.session, "继续")

        self.assertEqual(answer, "已接着回答")
        self.assertEqual(self.store.get_summary(self.session), "早期对话摘要")
        final_messages = fake.calls[-1]["messages"]
        self.assertTrue(any(
            message["role"] == "system" and "早期对话摘要" in message["content"]
            for message in final_messages
        ))
```

- [ ] **Step 6: 运行新增测试并确认压缩行为失败**

Run:

```powershell
python -m unittest tests.test_runtime -v
```

Expected: 追问和隔离测试通过，压缩测试因为尚未调用摘要逻辑而失败。

- [ ] **Step 7: 实现压缩逻辑**

`AgentRuntime._compress_if_needed()`：

1. 使用 `store.context_stats()` 得到消息数量和正文字符数。
2. 两项都未超阈值时立即返回。
3. 调用 `store.compactable_messages(session_id, keep_user_turns=2)`。
4. 没有可压缩消息时立即返回。
5. 把旧摘要和待压缩消息格式化为 JSON，交给 `llm.chat(messages, [])`。
6. 摘要响应必须有非空 `content` 且不能包含 tool call。
7. 使用 `save_summary_and_compact()` 原子保存。
8. 记录 `context_compacted` trace。
9. 摘要调用失败时记录 `context_compression_failed`，不标记消息，并继续主循环。

- [ ] **Step 8: 运行完整 Runtime 测试并确认通过**

Run:

```powershell
python -m unittest tests.test_runtime -v
```

Expected: `Ran 7 tests` 和 `OK`。

- [ ] **Step 9: 提交 Task 4**

```powershell
git add mini_agent/prompts.py mini_agent/runtime.py tests/test_runtime.py
git commit -m "feat: implement agent runtime loop"
```

## Task 5: CLI、session 命令和 trace 查看

**Files:**

- Create: `mini_agent/cli.py`
- Create: `mini_agent/__main__.py`
- Modify: `tests/test_store.py`

**Interfaces:**

- Consumes: `DeepSeekClient`, `AgentRuntime`, `SessionStore`, `build_default_registry`.
- Produces: `python -m mini_agent`.
- Produces: `/new`, `/sessions`, `/use`, `/trace`, `/help`, `/exit`.

- [ ] **Step 1: 写 CLI 单次模式的失败测试**

在 `tests/test_store.py` 之外新建 CLI 行为测试，避免启动真实网络：

```python
# tests/test_cli.py
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.cli import main


class CLITests(unittest.TestCase):
    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_once_mode_creates_named_session_and_prints_answer(
        self,
        client_class,
        runtime_class,
    ):
        runtime_class.return_value.run.return_value = "答案是 4"
        with patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "test-key"},
            clear=True,
        ):
            with tempfile.TemporaryDirectory() as temp:
                output = io.StringIO()
                with patch("sys.stdout", output):
                    exit_code = main([
                        "--db", str(Path(temp) / "agent.db"),
                        "--session", "demo",
                        "--once", "2+2 等于多少？",
                    ])

        self.assertEqual(exit_code, 0)
        self.assertIn("答案是 4", output.getvalue())
        runtime_class.return_value.run.assert_called_once_with(
            "demo",
            "2+2 等于多少？",
        )

    def test_missing_api_key_has_clear_message(self):
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True):
                with patch("sys.stderr", output):
                    exit_code = main([
                        "--db", str(Path(temp) / "agent.db"),
                        "--once", "你好",
                    ])

        self.assertEqual(exit_code, 2)
        self.assertIn("DEEPSEEK_API_KEY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认缺少 CLI 模块**

Run:

```powershell
python -m unittest tests.test_cli -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.cli'`。

- [ ] **Step 3: 实现 CLI**

`mini_agent/cli.py`：

- `argparse` 参数为 `--db`、`--session`、`--once`。
- `--db` 默认 `.agent-data/agent.db`。
- `--session` 不存在时按给定 ID 新建；未指定时创建随机 session。
- 创建 `DeepSeekClient` 前检查 `DEEPSEEK_API_KEY`。
- Base URL 和模型可分别由 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 覆盖。
- 单次模式打印最终答案后返回 0。
- 交互模式显示当前 session ID，再循环读取 `input("you> ")`。
- 本地命令不得调用 LLM。
- `AgentError` 和 `LLMError` 只打印清楚的错误消息，不打印堆栈或密钥。
- `finally` 中关闭 `SessionStore`。

`mini_agent/__main__.py`：

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: 运行 CLI 测试并确认通过**

Run:

```powershell
python -m unittest tests.test_cli -v
```

Expected: `Ran 2 tests` 和 `OK`。

- [ ] **Step 5: 手动验证不带 Key 的帮助和错误**

Run:

```powershell
python -m mini_agent --help
python -m mini_agent --once "你好"
```

Expected:

- 第一条命令退出码 0 并列出三个参数。
- 第二条命令退出码 2，并显示如何设置 `DEEPSEEK_API_KEY`。

- [ ] **Step 6: 提交 Task 5**

```powershell
git add mini_agent/cli.py mini_agent/__main__.py tests/test_cli.py
git commit -m "feat: add interactive agent cli"
```

## Task 6: README、Prompt、问题记录和演示脚本

**Files:**

- Create: `README.md`
- Create: `PROMPTS.md`
- Create: `PROBLEM_SOLVING.md`
- Create: `.env.example`
- Create: `demo.ps1`

**Interfaces:**

- Produces: 初学者可复制的配置、运行、测试和录屏步骤。
- Produces: memory 召回时机与 context 放置方式说明。

- [ ] **Step 1: 写文档验收测试**

在 `tests/test_docs.py` 中添加：

```python
from pathlib import Path
import unittest


class DocumentationTests(unittest.TestCase):
    def test_required_submission_documents_exist_and_cover_memory(self):
        required = [
            "README.md",
            "PROMPTS.md",
            "PROBLEM_SOLVING.md",
            ".env.example",
            "demo.ps1",
        ]
        for name in required:
            self.assertTrue(Path(name).is_file(), name)

        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("memory 什么时候召回", readme)
        self.assertIn("memory 放在 context 的什么位置", readme)
        self.assertIn("deepseek-v4-pro", readme)
        self.assertIn("python -m unittest discover", readme)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行文档测试并确认文件缺失**

Run:

```powershell
python -m unittest tests.test_docs -v
```

Expected: `AssertionError: README.md`。

- [ ] **Step 3: 编写文档**

`README.md` 必须按初学者可理解的顺序包含：

1. 这个项目完成了什么。
2. 为什么没有使用 Agent 框架。
3. Python 版本检查。
4. PowerShell 中临时设置三个 DeepSeek 环境变量的命令。
5. 启动、指定 session、单次调用和测试命令。
6. 四个工具的参数示例和 mock 标记。
7. 8 步 Agent 循环的文字说明。
8. 两个窗口 session 隔离的具体命令。
9. “memory 什么时候召回”。
10. “memory 放在 context 的什么位置”。
11. 为什么普通最终回答的 reasoning 不放回 context。
12. 何时触发压缩、保留什么、原始消息是否删除。
13. trace 查看方式。
14. 常见错误：Key 缺失、401、429、超时、最大循环。
15. 项目文件说明和限制。
16. 真实 API 冒烟测试步骤。

`PROMPTS.md` 原样展示 `AGENT_SYSTEM_PROMPT` 和 `COMPRESSION_PROMPT`，并解释每条约束解决什么问题。

`PROBLEM_SOLVING.md` 只记录开发期间真实发生的问题，初始内容包括：

- 空目录且不是 Git 仓库。
- Git 缺少作者身份，采用仓库本地 `Codex <codex@local>`。
- DeepSeek thinking tool call 必须带回 `reasoning_content`。
- 工具结果错误要返回给模型，而不是让进程崩溃。
- 压缩必须以完整 turn 为边界。

`.env.example`：

```dotenv
DEEPSEEK_API_KEY=replace-with-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

`demo.ps1` 不读取或打印 Key，只检查它是否存在，然后依次提示用户：

```text
终端 1 使用 session weather-chat 查询天气并添加待办
终端 2 使用 session weekly-report 生成周报并添加待办
分别继续两个 session
在交互模式输入 /trace
运行完整 unittest
```

- [ ] **Step 4: 运行文档测试并确认通过**

Run:

```powershell
python -m unittest tests.test_docs -v
```

Expected: `Ran 1 test` 和 `OK`。

- [ ] **Step 5: 提交 Task 6**

```powershell
git add README.md PROMPTS.md PROBLEM_SOLVING.md .env.example demo.ps1 tests/test_docs.py
git commit -m "docs: add runbook and demo guide"
```

## Task 7: 全量验证、真实 API 冒烟测试、录屏和代码链接

**Files:**

- Create after recording: `artifacts/demo.mp4`
- Modify if real problems occur: `PROBLEM_SOLVING.md`

**Interfaces:**

- Consumes: 完整 CLI、测试、文档和本机 `DEEPSEEK_API_KEY`。
- Produces: 测试证据、真实 API 证据、录屏文件和远程仓库链接。

- [ ] **Step 1: 运行完整自动测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 所有测试通过，结尾为 `OK`。

- [ ] **Step 2: 检查源码没有 Agent 框架依赖或密钥**

Run:

```powershell
rg -n "langgraph|openhands|openclaw|sk-[A-Za-z0-9]" . -g "*.py" -g "*.md" -g "*.toml" -g "*.ps1"
```

Expected: 只允许 README 或设计文档中说明“未使用框架”的文字；不得找到真实 Key。

- [ ] **Step 3: 编译全部 Python 文件**

Run:

```powershell
python -m compileall -q mini_agent tests
```

Expected: 退出码 0。

- [ ] **Step 4: 使用真实 DeepSeek API 验证直接回答**

Run:

```powershell
python -m mini_agent --session live-direct --once "只回答：连接成功"
```

Expected: 真实返回一条非空回答，数据库 trace 包含 model response。

- [ ] **Step 5: 使用真实 DeepSeek API 验证工具调用**

Run:

```powershell
python -m mini_agent --session live-tools --once "请务必使用 calculator 工具计算 25 * 18，然后告诉我结果"
```

Expected: 最终答案包含 `450`，trace 中存在 `calculator` 工具调用和成功结果。

- [ ] **Step 6: 按 demo.ps1 完成两个 session 的终端录屏**

录屏必须包含：

```text
weather-chat：查杭州天气并记录带雨伞
weekly-report：生成周报并记录整理数据
weather-chat：追问未完成待办
weekly-report：追问刚才的周报
/trace：显示工具调用步骤
python -m unittest discover -s tests -v：显示测试通过
```

保存为 `artifacts/demo.mp4`，检查画面中没有 API Key。
录屏文件应控制在 50 MB 以内，避免远程仓库拒绝普通 Git 推送。

- [ ] **Step 7: 检查 Git 状态和提交录屏**

Run:

```powershell
git status --short
git add artifacts/demo.mp4 PROBLEM_SOLVING.md
git commit -m "chore: add verified agent demo"
git status --short --branch
```

Expected: 最终工作区干净。

- [ ] **Step 8: 创建或连接 GitHub 远程仓库**

先只读检查：

```powershell
gh auth status
git remote -v
```

如果 `gh auth status` 成功且没有远程，先向用户确认仓库公开性。用户同意公开后：

```powershell
gh repo create minimal-agent --source . --public --remote origin --push
```

用户要求私有时把 `--public` 改为 `--private`。

如果已有远程：

```powershell
git push -u origin main
```

如果没有 GitHub 登录，不尝试绕过认证。停止发布步骤，请用户先完成 GitHub 登录或提供已经创建好的远程仓库地址；取得准确地址后再添加 `origin` 并推送。

- [ ] **Step 9: 最终逐项核对规格**

逐项核对：

```text
真实 DeepSeek API
自研循环
四个注册工具
reasoning/tool_calls/content 解析
session 隔离与恢复
普通和工具型追问
最大 8 步
基础压缩
异常处理
trace
自动测试
README
PROMPTS
PROBLEM_SOLVING
录屏
代码链接
```

任何一项没有证据时不得宣称项目完成。
