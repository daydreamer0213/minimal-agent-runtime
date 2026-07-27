# Minimal Agent Local Web Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有最小 Agent 增加一个零第三方依赖的本地网页，用三栏界面演示真实聊天、工具循环、独立 session、trace 和 todo。

**Architecture:** `mini_agent.web` 使用 Python `ThreadingHTTPServer` 提供白名单静态文件与 JSON API。HTTP 请求为每次数据库操作打开独立 `SessionStore`，聊天写入经过进程内全局锁，再调用现有 `AgentRuntime`；浏览器端只使用原生 HTML/CSS/JavaScript，API Key 始终留在 Python 进程。

**Tech Stack:** Python 3.11+ 标准库、SQLite、`http.server`、`urllib.request`、原生 HTML/CSS/JavaScript、`unittest`、DeepSeek OpenAI-compatible Chat Completions API。

## Global Constraints

- 核心 Agent Runtime 不得依赖 LangGraph、OpenHands、OpenClaw 或其他 Agent 框架。
- Python 运行时代码不得增加第三方依赖。
- 默认模型必须是 `deepseek-v4-pro`，默认 Base URL 必须是 `https://api.deepseek.com`。
- API Key 只允许从 `DEEPSEEK_API_KEY` 读取，不得写入源码、数据库、日志、网页、浏览器存储、API 响应或录屏。
- 网页默认只绑定 `127.0.0.1:8000`；公网部署、登录、多用户权限、流式输出和 WebSocket 不在首版范围内。
- 一次用户输入仍最多执行 8 次 LLM 循环；网页不得复制或改写核心循环。
- calculator、search、weather、todo 仍通过现有统一工具注册机制提供。
- 默认自动化测试不得访问网络或消耗 API 额度。
- 所有行为变更先写失败测试并确认 RED，再写最小实现并确认 GREEN。
- 使用 `D:\DevData\conda-envs\asset-intel\python.exe` 运行本机测试；此电脑的裸 `python` 命令不可用。

---

## File Structure

```text
mini_agent/
├── config.py                 CLI 与网页共享的 dotenv、client、runtime 和秘密清理
├── cli.py                    改为调用 config.py，CLI 行为保持不变
├── store.py                  增加只读 UI 消息查询
├── web.py                    本地 HTTP 服务、JSON API、错误边界和启动入口
└── web_static/
    ├── index.html            三栏 Agent 实验台语义结构
    ├── app.css               布局、视觉 token、循环轨道、响应式和无障碍
    └── app.js                state、session、chat、trace 和 todo 交互
tests/
├── test_config.py            共享配置单元测试
├── test_store.py             UI 消息查询与持久化测试
├── test_web.py               真实本地 HTTP 边界的离线测试
└── test_docs.py              网页提交材料和录屏指令测试
README.md                     网页运行、结构和录屏说明
demo.ps1                      网页录屏步骤提示
```

Task 5 的三个 `web_static` 文件和对应静态验收测试适合交给用户指定的 `gpt-5.3-codex-spark` 会话 `019fa280-8b0c-7211-9551-e054207e0ed8`。分派前必须满足：

1. 该会话不再停留在无关的 Atlassian 插件审批。
2. Task 3 和 Task 4 已完成并审查，HTTP API 契约冻结。
3. Spark 会话明确在 `D:\Guo\vibe\.worktrees\minimal-agent` 工作。
4. 当前会话在 Spark 完成前不修改 `mini_agent/web_static/*`。
5. Spark 提交后仍由当前会话运行测试、浏览器视觉检查和独立代码审查。

---

### Task 1: Extract Shared Configuration Without Changing CLI Behavior

**Files:**

- Create: `mini_agent/config.py`
- Create: `tests/test_config.py`
- Modify: `mini_agent/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

- Produces: `ConfigurationError`.
- Produces: `load_dotenv(path: Path) -> None`.
- Produces: `build_client(api_key: str) -> DeepSeekClient`.
- Produces: `build_runtime(store: SessionStore, api_key: str) -> AgentRuntime`.
- Produces: `safe_text(message: str, api_key: str | None) -> str`.
- Preserves: process environment wins over `.env`; the last supported duplicate in `.env` wins; unknown variables are ignored; malformed supported entries never expose their value.

- [ ] **Step 1: Write the failing shared-configuration tests**

Create `tests/test_config.py`:

```python
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.config import build_client, load_dotenv, safe_text


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_keeps_process_values_and_uses_last_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            path.write_text(
                "\ufeffDEEPSEEK_API_KEY=dotenv-key\n"
                "DEEPSEEK_BASE_URL=https://first.example\n"
                "DEEPSEEK_BASE_URL=https://last.example\n"
                "DEEPSEEK_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"DEEPSEEK_MODEL": "process-model"},
                clear=True,
            ):
                load_dotenv(path)
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "dotenv-key")
                self.assertEqual(
                    os.environ["DEEPSEEK_BASE_URL"],
                    "https://last.example",
                )
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "process-model")

    @patch("mini_agent.config.DeepSeekClient")
    def test_build_client_uses_supported_environment_overrides(self, client_class):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "https://example.test/api",
                "DEEPSEEK_MODEL": "custom-model",
            },
            clear=True,
        ):
            build_client("test-key")

        client_class.assert_called_once_with(
            "test-key",
            base_url="https://example.test/api",
            model="custom-model",
        )

    def test_safe_text_redacts_the_exact_key(self):
        self.assertEqual(
            safe_text("request failed for secret-key", "secret-key"),
            "request failed for [redacted]",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_config -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.config'`.

- [ ] **Step 3: Create the shared configuration module**

Create `mini_agent/config.py`:

```python
"""Shared local configuration and runtime composition."""

import os
from pathlib import Path

from .llm import DeepSeekClient
from .runtime import AgentRuntime
from .store import SessionStore
from .tools import build_default_registry


_DOTENV_VARIABLES = {
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
}


class ConfigurationError(ValueError):
    """A safe-to-display local configuration error."""


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return

    process_variables = {
        name for name in _DOTENV_VARIABLES if name in os.environ
    }
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            name = line.split(maxsplit=1)[0]
            if name in _DOTENV_VARIABLES:
                raise ConfigurationError(f"Invalid .env entry for {name}")
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        if name not in _DOTENV_VARIABLES:
            continue
        value = value.strip()
        if value[:1] in {"'", '"'} or value[-1:] in {"'", '"'}:
            if len(value) < 2 or value[0] != value[-1]:
                raise ConfigurationError(f"Invalid .env entry for {name}")
            value = value[1:-1]
        if name not in process_variables:
            os.environ[name] = value


def build_client(api_key: str) -> DeepSeekClient:
    options = {}
    if base_url := os.environ.get("DEEPSEEK_BASE_URL"):
        options["base_url"] = base_url
    if model := os.environ.get("DEEPSEEK_MODEL"):
        options["model"] = model
    return DeepSeekClient(api_key, **options)


def build_runtime(store: SessionStore, api_key: str) -> AgentRuntime:
    return AgentRuntime(store, build_client(api_key), build_default_registry())


def safe_text(message: str, api_key: str | None) -> str:
    return message.replace(api_key, "[redacted]") if api_key else message
```

- [ ] **Step 4: Make CLI consume the shared functions**

In `mini_agent/cli.py`:

```python
from .config import (
    ConfigurationError,
    build_runtime,
    load_dotenv,
    safe_text,
)
from .llm import LLMError
from .runtime import AgentError, AgentRuntime
from .store import SessionStore
```

Delete the local `_DOTENV_VARIABLES`, `ConfigurationError`, `_load_dotenv`,
`_build_client`, `_build_runtime`, and `_safe_text` definitions. Replace calls
exactly as follows:

```text
_load_dotenv()                  -> load_dotenv(Path.cwd() / ".env")
_build_runtime(store, api_key)  -> build_runtime(store, api_key)
_safe_text(message, api_key)    -> safe_text(message, api_key)
```

In `tests/test_cli.py`, replace patch targets:

```text
mini_agent.cli.DeepSeekClient -> mini_agent.config.DeepSeekClient
mini_agent.cli.AgentRuntime   -> mini_agent.config.AgentRuntime
```

Keep all existing assertions; they are the regression contract for CLI behavior.

- [ ] **Step 5: Run focused and CLI tests and verify GREEN**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_config tests.test_cli -v
```

Expected: all config and CLI tests pass with `OK`; no network request is made.

- [ ] **Step 6: Commit Task 1**

```powershell
git add mini_agent/config.py mini_agent/cli.py tests/test_config.py tests/test_cli.py
git commit -m "refactor: share agent configuration"
```

---

### Task 2: Add Read-Only Message History for the Web UI

**Files:**

- Modify: `mini_agent/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**

- Produces: `SessionStore.list_messages(session_id: str, limit: int = 100) -> list[dict[str, Any]]`.
- Returns: the most recent `limit` audit messages in ascending message-ID order, including compacted messages.
- Raises: `ValueError("limit must be positive")` when `limit <= 0`.
- Preserves: `context_messages()` semantics used by the Agent runtime.

- [ ] **Step 1: Write failing store tests**

Append to `tests/test_store.py`:

```python
    def test_list_messages_returns_recent_audit_history_in_display_order(self):
        session_id = self.store.create_session("网页", "web")
        for turn in range(1, 4):
            self.store.add_message(session_id, turn, "user", f"问题 {turn}")
            self.store.add_message(session_id, turn, "assistant", f"回答 {turn}")
        rows = self.store.compactable_messages(session_id, keep_user_turns=1)
        self.store.save_summary_and_compact(
            session_id,
            "旧内容摘要",
            [row["id"] for row in rows],
        )

        messages = self.store.list_messages(session_id, limit=3)

        self.assertEqual(
            [message["content"] for message in messages],
            ["回答 2", "问题 3", "回答 3"],
        )
        self.assertTrue(messages[0]["compacted"])

    def test_list_messages_rejects_non_positive_limit(self):
        session_id = self.store.create_session("网页", "web")

        with self.assertRaisesRegex(ValueError, "limit must be positive"):
            self.store.list_messages(session_id, limit=0)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_store -v
```

Expected: `AttributeError: 'SessionStore' object has no attribute 'list_messages'`.

- [ ] **Step 3: Implement the minimal read-only query**

Add to `SessionStore` in `mini_agent/store.py`:

```python
    def list_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self.connection.execute(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id
            """,
            (session_id, limit),
        ).fetchall()
        return [self._message_dict(row) for row in rows]
```

- [ ] **Step 4: Run store tests and verify GREEN**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_store -v
```

Expected: all store tests pass with `OK`.

- [ ] **Step 5: Commit Task 2**

```powershell
git add mini_agent/store.py tests/test_store.py
git commit -m "feat: expose session message history"
```

---

### Task 3: Build the Local HTTP Server and Read-Only State APIs

**Files:**

- Create: `mini_agent/web.py`
- Create: `mini_agent/web_static/index.html`
- Create: `mini_agent/web_static/app.css`
- Create: `mini_agent/web_static/app.js`
- Create: `tests/test_web.py`

**Interfaces:**

- Produces: `create_server(address, db_path, runtime_factory=build_runtime, environment=None) -> AgentHTTPServer`.
- Produces: `main(argv: list[str] | None = None) -> int`.
- Produces: `GET /`, `/app.css`, `/app.js`.
- Produces: `GET /api/state?session_id=<id>`.
- Produces: `POST /api/sessions` with `{"title": string}`.
- Static files are exact-route whitelisted; arbitrary filesystem paths are never resolved from URLs.

- [ ] **Step 1: Write failing HTTP boundary tests**

Create `tests/test_web.py`:

```python
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mini_agent.web import create_server, main


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "agent.db"
        self.server = create_server(
            ("127.0.0.1", 0),
            self.db_path,
            environment={},
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, *, method="GET", payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=2) as response:
            return response.status, response.read(), response.headers

    def test_static_files_are_served_but_unknown_paths_are_not(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'data-app="minimal-agent"', body)
        self.assertIn("text/html", headers["Content-Type"])

        with self.assertRaises(HTTPError) as unknown:
            self.request("/../../README.md")
        self.assertEqual(unknown.exception.code, 404)

    def test_state_creates_first_session_and_rejects_unknown_session(self):
        status, body, _ = self.request("/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(state["sessions"]), 1)
        self.assertEqual(
            state["current_session_id"],
            state["sessions"][0]["id"],
        )

        with self.assertRaises(HTTPError) as unknown:
            self.request("/api/state?session_id=missing")
        self.assertEqual(unknown.exception.code, 404)

    def test_session_creation_trims_title_and_returns_new_state(self):
        status, body, _ = self.request(
            "/api/sessions",
            method="POST",
            payload={"title": "  天气  "},
        )
        payload = json.loads(body)

        self.assertEqual(status, 201)
        self.assertEqual(payload["state"]["current_session_id"], payload["session_id"])
        current = next(
            session for session in payload["state"]["sessions"]
            if session["id"] == payload["session_id"]
        )
        self.assertEqual(current["title"], "天气")

    @patch("mini_agent.web.load_dotenv")
    @patch("mini_agent.web.threading.Timer")
    @patch("mini_agent.web.create_server")
    def test_main_defaults_to_loopback_and_schedules_browser(
        self,
        server_factory,
        timer_class,
        _load_dotenv,
    ):
        server = Mock()
        server.server_port = 8123
        server.serve_forever.side_effect = KeyboardInterrupt
        server_factory.return_value = server

        exit_code = main(["--db", str(self.db_path)])

        self.assertEqual(exit_code, 0)
        server_factory.assert_called_once_with(
            ("127.0.0.1", 8000),
            str(self.db_path),
        )
        timer_class.assert_called_once_with(
            0.25,
            __import__("webbrowser").open,
            args=("http://127.0.0.1:8123",),
        )
        timer_class.return_value.start.assert_called_once_with()
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_web -v
```

Expected: `ModuleNotFoundError: No module named 'mini_agent.web'`.

- [ ] **Step 3: Add minimal static placeholders used by server tests**

Create `mini_agent/web_static/index.html`:

```html
<!doctype html>
<html lang="zh-CN" data-app="minimal-agent">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Minimal Agent 实验台</title>
    <link rel="stylesheet" href="/app.css">
  </head>
  <body>
    <main id="app">Minimal Agent 实验台</main>
    <script src="/app.js" defer></script>
  </body>
</html>
```

Create `mini_agent/web_static/app.css`:

```css
body { margin: 0; font-family: "Segoe UI", sans-serif; }
```

Create `mini_agent/web_static/app.js`:

```javascript
"use strict";
```

- [ ] **Step 4: Implement the HTTP foundation and state/session APIs**

Create `mini_agent/web.py` with these exact responsibilities and public
interfaces:

```python
"""Local standard-library web UI for the minimal agent."""

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from .config import build_runtime, load_dotenv, safe_text
from .llm import LLMError
from .runtime import AgentError
from .store import SessionStore


MAX_BODY_BYTES = 32 * 1024
STATIC_ROOT = Path(__file__).with_name("web_static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class RequestProblem(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        db_path: str | Path,
        runtime_factory: Callable[..., Any],
        environment: Mapping[str, str],
    ):
        self.db_path = Path(db_path)
        self.runtime_factory = runtime_factory
        self.environment = environment
        self.chat_lock = threading.Lock()
        super().__init__(address, AgentRequestHandler)


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer

    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except RequestProblem as error:
            self._json(error.status, {"error": str(error)})
        except (AgentError, LLMError) as error:
            key = self.server.environment.get("DEEPSEEK_API_KEY")
            self._json(502, {"error": safe_text(str(error), key)})
        except Exception as error:
            print(
                f"Web error: {type(error).__name__}",
                file=sys.stderr,
            )
            self._json(500, {"error": "本地 Agent 服务发生未预期错误"})

    def _get(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/state":
            requested = parse_qs(parsed.query).get("session_id", [None])[0]
            with self._store() as store:
                self._json(200, self._state(store, requested))
            return
        static = STATIC_FILES.get(parsed.path)
        if static is None or parsed.query:
            raise RequestProblem(404, "页面不存在")
        filename, content_type = static
        data = (STATIC_ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _post(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/sessions":
            raise RequestProblem(404, "API 不存在")
        payload = self._read_json()
        title = payload.get("title", "")
        if not isinstance(title, str):
            raise RequestProblem(400, "title 必须是字符串")
        title = title.strip()
        if len(title) > 80:
            raise RequestProblem(400, "title 最多 80 个字符")
        with self._store() as store:
            session_id = store.create_session(title=title)
            state = self._state(store, session_id)
        self._json(201, {"session_id": session_id, "state": state})

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise RequestProblem(400, "Content-Length 无效") from error
        if length > MAX_BODY_BYTES:
            raise RequestProblem(413, "请求正文超过 32 KiB")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestProblem(400, "请求正文必须是 UTF-8 JSON") from error
        if not isinstance(payload, dict):
            raise RequestProblem(400, "请求正文必须是 JSON 对象")
        return payload

    def _state(
        self,
        store: SessionStore,
        requested_session_id: str | None,
    ) -> dict[str, Any]:
        sessions = store.list_sessions()
        if requested_session_id is not None:
            if not store.session_exists(requested_session_id):
                raise RequestProblem(404, "session 不存在")
            current_id = requested_session_id
        elif sessions:
            current_id = sessions[0]["id"]
        else:
            current_id = store.create_session("网页演示")
            sessions = store.list_sessions()

        messages = [
            {
                "role": message["role"],
                "content": message["content"],
                "created_at": message["created_at"],
            }
            for message in store.list_messages(current_id)
            if message["role"] in {"user", "assistant"}
            and message["content"]
            and not message.get("tool_calls")
        ]
        return {
            "current_session_id": current_id,
            "sessions": sessions,
            "messages": messages,
            "todos": store.list_todos(current_id),
            "traces": list(reversed(store.list_traces(current_id, limit=40))),
        }

    def _store(self):
        class StoreContext:
            def __init__(self, path):
                self.store = SessionStore(path)

            def __enter__(self):
                return self.store

            def __exit__(self, *_):
                self.store.close()

        return StoreContext(self.server.db_path)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"Web {self.address_string()}: {format % args}")


def create_server(
    address: tuple[str, int],
    db_path: str | Path,
    runtime_factory: Callable[..., Any] = build_runtime,
    environment: Mapping[str, str] | None = None,
) -> AgentHTTPServer:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return AgentHTTPServer(
        address,
        path,
        runtime_factory,
        os.environ if environment is None else environment,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal Agent local web demo")
    parser.add_argument("--db", default=".agent-data/agent.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    try:
        load_dotenv(Path.cwd() / ".env")
        server = create_server((args.host, args.port), args.db)
    except (OSError, ValueError) as error:
        key = os.environ.get("DEEPSEEK_API_KEY")
        print(f"Error: {safe_text(str(error), key)}", file=sys.stderr)
        return 2

    url = f"http://{args.host}:{server.server_port}"
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: the demo is listening beyond this computer.")
    print(f"Minimal Agent web: {url}")
    if not args.no_browser:
        opener = threading.Timer(0.25, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run web and store tests and verify GREEN**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_store tests.test_web -v
```

Expected: all tests pass with `OK`; `tests.test_web` opens only an ephemeral
loopback port and never calls DeepSeek.

- [ ] **Step 6: Commit Task 3**

```powershell
git add mini_agent/web.py mini_agent/web_static tests/test_web.py
git commit -m "feat: add local agent web server"
```

---

### Task 4: Add the Real Chat API, Sanitized Errors, and Trace Redaction

**Files:**

- Modify: `mini_agent/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**

- Produces: `POST /api/chat`.
- Consumes: `runtime_factory(store: SessionStore, api_key: str)`.
- Request: `{"session_id": str, "message": str}`.
- Success: HTTP 200 with `{"answer": str, "state": object}`.
- Errors: 400 invalid input, 413 body too large, 503 missing Key, 502 Agent/LLM error, 500 sanitized unexpected error.
- Serializes chat writes through `AgentHTTPServer.chat_lock`.

- [ ] **Step 1: Add failing chat and error tests**

Add imports to `tests/test_web.py`:

```python
from mini_agent.llm import LLMResponse, ToolCall
from mini_agent.runtime import AgentRuntime
from mini_agent.store import SessionStore
from mini_agent.tools import build_default_registry
```

Add this helper above `WebTests`:

```python
class QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
```

Allow `WebTests.setUp()` to use a method-level `self.responses`:

```python
        self.responses = []

        def runtime_factory(store, _api_key):
            return AgentRuntime(
                store,
                QueueLLM(self.responses),
                build_default_registry(),
            )

        self.server = create_server(
            ("127.0.0.1", 0),
            self.db_path,
            runtime_factory=runtime_factory,
            environment={"DEEPSEEK_API_KEY": "test-key"},
        )
```

Add tests:

```python
    def test_chat_runs_real_runtime_tools_and_returns_refreshed_state(self):
        _, created, _ = self.request(
            "/api/sessions",
            method="POST",
            payload={"title": "天气"},
        )
        session_id = json.loads(created)["session_id"]
        self.responses.extend([
            LLMResponse("", "先查天气", [
                ToolCall(
                    "weather-call",
                    "weather",
                    '{"city":"杭州","date":"2026-07-28"}',
                )
            ]),
            LLMResponse("", "添加待办", [
                ToolCall(
                    "todo-call",
                    "todo",
                    '{"action":"add","text":"带雨伞"}',
                )
            ]),
            LLMResponse("已查询模拟天气并添加待办。", "完成", []),
        ])

        status, body, _ = self.request(
            "/api/chat",
            method="POST",
            payload={
                "session_id": session_id,
                "message": "查天气并记待办",
            },
        )
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertIn("已查询", payload["answer"])
        self.assertEqual(payload["state"]["todos"][0]["text"], "带雨伞")
        events = [trace["event"] for trace in payload["state"]["traces"]]
        self.assertIn("tool", events)
        self.assertEqual(payload["state"]["current_session_id"], session_id)

    def test_chat_rejects_bad_inputs_without_running_runtime(self):
        with self.assertRaises(HTTPError) as empty:
            self.request(
                "/api/chat",
                method="POST",
                payload={"session_id": "missing", "message": ""},
            )
        self.assertEqual(empty.exception.code, 400)

        oversized = Request(
            self.base_url + "/api/chat",
            data=b"x" * (32 * 1024 + 1),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as too_large:
            urlopen(oversized, timeout=2)
        self.assertEqual(too_large.exception.code, 413)

    def test_missing_key_and_runtime_errors_are_sanitized(self):
        self.server.environment = {}
        with self.assertRaises(HTTPError) as missing:
            self.request(
                "/api/chat",
                method="POST",
                payload={"session_id": "missing", "message": "你好"},
            )
        self.assertEqual(missing.exception.code, 503)

        self.server.environment = {"DEEPSEEK_API_KEY": "secret-key"}
        self.responses.append(RuntimeError("secret-key must not escape"))
        _, state_body, _ = self.request("/api/state")
        session_id = json.loads(state_body)["current_session_id"]
        with self.assertRaises(HTTPError) as failed:
            self.request(
                "/api/chat",
                method="POST",
                payload={"session_id": session_id, "message": "触发错误"},
            )
        error_body = failed.exception.read().decode("utf-8")
        self.assertEqual(failed.exception.code, 502)
        self.assertNotIn("secret-key", error_body)
        self.assertNotIn("Traceback", error_body)

    def test_state_redacts_the_key_from_nested_trace_data(self):
        _, body, _ = self.request("/api/state")
        session_id = json.loads(body)["current_session_id"]
        store = SessionStore(self.db_path)
        try:
            store.add_trace(
                session_id,
                1,
                "tool",
                {"result": {"message": "test-key must not reach browser"}},
            )
        finally:
            store.close()

        _, state_body, _ = self.request(
            f"/api/state?session_id={session_id}"
        )

        self.assertNotIn("test-key", state_body.decode("utf-8"))
        self.assertIn("[redacted]", state_body.decode("utf-8"))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_web -v
```

Expected: chat tests fail because `/api/chat` returns HTTP 404.

- [ ] **Step 3: Implement chat dispatch and validation**

In `AgentRequestHandler._post()` dispatch exact routes:

```python
        path = urlsplit(self.path).path
        if path == "/api/sessions":
            self._create_session()
            return
        if path == "/api/chat":
            self._chat()
            return
        raise RequestProblem(404, "API 不存在")
```

Move the existing session body into `_create_session()`. Add:

```python
    def _chat(self) -> None:
        payload = self._read_json()
        session_id = payload.get("session_id")
        message = payload.get("message")
        if not isinstance(session_id, str) or not session_id:
            raise RequestProblem(400, "session_id 必须是非空字符串")
        if not isinstance(message, str) or not message.strip():
            raise RequestProblem(400, "message 必须是非空字符串")

        api_key = self.server.environment.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RequestProblem(
                503,
                "缺少 DEEPSEEK_API_KEY，请填写项目根目录 .env",
            )

        with self.server.chat_lock:
            with self._store() as store:
                if not store.session_exists(session_id):
                    raise RequestProblem(404, "session 不存在")
                runtime = self.server.runtime_factory(store, api_key)
                answer = runtime.run(session_id, message.strip())
                state = self._state(store, session_id)
        self._json(200, {"answer": answer, "state": state})
```

- [ ] **Step 4: Redact and truncate trace response data recursively**

Add to `AgentRequestHandler`:

```python
    def _safe_value(self, value: Any) -> Any:
        key = self.server.environment.get("DEEPSEEK_API_KEY")
        if isinstance(value, str):
            return safe_text(value, key)[:2000]
        if isinstance(value, list):
            return [self._safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(name): self._safe_value(item)
                for name, item in value.items()
            }
        return value
```

Change `_state()` trace construction to:

```python
        traces = [
            {
                **trace,
                "data": self._safe_value(trace["data"]),
            }
            for trace in reversed(store.list_traces(current_id, limit=40))
        ]
```

Return this local `traces` variable in the state object.

- [ ] **Step 5: Run focused and full backend tests and verify GREEN**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_web tests.test_runtime tests.test_cli -v
```

Expected: all tests pass with `OK`; chat tests use `QueueLLM` and make no external request.

- [ ] **Step 6: Commit Task 4**

```powershell
git add mini_agent/web.py tests/test_web.py
git commit -m "feat: expose agent chat over local api"
```

---

### Task 5: Build the Three-Column Agent Lab Frontend

**Delegation:** This is the preferred task for Codex thread
`019fa280-8b0c-7211-9551-e054207e0ed8` using `gpt-5.3-codex-spark`, after
the preconditions in “File Structure” are satisfied.

**Files:**

- Modify: `mini_agent/web_static/index.html`
- Modify: `mini_agent/web_static/app.css`
- Modify: `mini_agent/web_static/app.js`
- Modify: `tests/test_web.py`

**Interfaces:**

- Consumes: `GET /api/state`, `POST /api/sessions`, `POST /api/chat`.
- Produces DOM IDs: `session-list`, `new-session-form`, `new-session-title`,
  `chat-list`, `chat-form`, `chat-input`, `send-button`, `quick-prompts`,
  `trace-list`, `todo-list`, `status-message`.
- Security: all model/user/tool text enters DOM through `textContent`; no `innerHTML`.
- Accessibility: semantic landmarks, labels, live status, visible focus, keyboard submit, reduced-motion support.

- [ ] **Step 1: Add failing static contract assertions**

Append to `test_static_files_are_served_but_unknown_paths_are_not()` in
`tests/test_web.py`:

```python
        for marker in [
            b'id="session-list"',
            b'id="chat-list"',
            b'id="trace-list"',
            b'id="todo-list"',
            b'id="status-message"',
        ]:
            self.assertIn(marker, body)

        _, script, _ = self.request("/app.js")
        self.assertIn(b"fetch(path", script)
        self.assertIn(b"/api/state", script)
        self.assertIn(b'textContent', script)
        self.assertNotIn(b'innerHTML', script)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_web.WebTests.test_static_files_are_served_but_unknown_paths_are_not -v
```

Expected: FAIL because the placeholder HTML lacks `session-list`.

- [ ] **Step 3: Replace the HTML with the semantic Agent lab**

Replace `mini_agent/web_static/index.html`:

```html
<!doctype html>
<html lang="zh-CN" data-app="minimal-agent">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>Minimal Agent 实验台</title>
    <link rel="stylesheet" href="/app.css">
  </head>
  <body>
    <div class="shell">
      <aside class="sessions-panel" aria-labelledby="sessions-title">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true">A</span>
          <div>
            <p class="eyebrow">Runtime / Local</p>
            <h1>Minimal Agent</h1>
          </div>
        </div>
        <div class="panel-heading">
          <h2 id="sessions-title">Sessions</h2>
          <span class="badge">SQLite</span>
        </div>
        <form id="new-session-form" class="new-session-form">
          <label for="new-session-title">新会话标题</label>
          <div class="inline-form">
            <input id="new-session-title" maxlength="80" placeholder="例如：天气">
            <button type="submit">新建</button>
          </div>
        </form>
        <nav id="session-list" class="session-list" aria-label="会话列表"></nav>
        <p class="local-note">仅运行于这台电脑 · Key 不进入浏览器</p>
      </aside>

      <main class="chat-panel">
        <header class="chat-header">
          <div>
            <p class="eyebrow">Active session</p>
            <h2 id="current-session">正在载入…</h2>
          </div>
          <div class="runtime-state"><span></span> Agent ready</div>
        </header>
        <section id="quick-prompts" class="quick-prompts" aria-label="快捷演示">
          <button type="button" data-prompt="查询杭州 2026-07-28 的天气，并添加待办带雨伞">天气 + 待办</button>
          <button type="button" data-prompt="生成一份本周工作周报，并添加待办检查周报">周报 + 待办</button>
        </section>
        <section id="chat-list" class="chat-list" aria-label="聊天记录"></section>
        <p id="status-message" class="status-message" role="status" aria-live="polite"></p>
        <form id="chat-form" class="chat-form">
          <label for="chat-input">给 Agent 发消息</label>
          <div class="composer">
            <textarea id="chat-input" rows="3" required placeholder="描述目标，让 Agent 自己决定是否调用工具"></textarea>
            <button id="send-button" type="submit">发送</button>
          </div>
        </form>
      </main>

      <aside class="inspector-panel" aria-label="Agent 执行详情">
        <section class="inspector-section trace-section">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">Live loop</p>
              <h2>Agent Trace</h2>
            </div>
            <span class="badge">最多 8 步</span>
          </div>
          <div id="trace-list" class="trace-list"></div>
        </section>
        <section class="inspector-section">
          <div class="panel-heading">
            <h2>当前待办</h2>
            <span class="badge">Session only</span>
          </div>
          <ul id="todo-list" class="todo-list"></ul>
        </section>
      </aside>
    </div>
    <script src="/app.js" defer></script>
  </body>
</html>
```

- [ ] **Step 4: Implement safe state and interaction rendering**

Replace `mini_agent/web_static/app.js`:

```javascript
"use strict";

const ui = {
  sessionList: document.querySelector("#session-list"),
  newSessionForm: document.querySelector("#new-session-form"),
  newSessionTitle: document.querySelector("#new-session-title"),
  currentSession: document.querySelector("#current-session"),
  chatList: document.querySelector("#chat-list"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  sendButton: document.querySelector("#send-button"),
  quickPrompts: document.querySelector("#quick-prompts"),
  traceList: document.querySelector("#trace-list"),
  todoList: document.querySelector("#todo-list"),
  status: document.querySelector("#status-message"),
};

const page = { sessionId: null, busy: false };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function renderSessions(state) {
  ui.sessionList.replaceChildren();
  for (const session of state.sessions) {
    const button = element("button", "session-item");
    button.type = "button";
    button.dataset.sessionId = session.id;
    button.classList.toggle("active", session.id === state.current_session_id);
    button.append(
      element("strong", "", session.title || "未命名会话"),
      element("span", "", session.id),
    );
    ui.sessionList.append(button);
  }
}

function renderMessages(messages) {
  ui.chatList.replaceChildren();
  if (!messages.length) {
    ui.chatList.append(
      element("p", "empty-state", "从一个目标开始，观察 Agent 是否调用工具。"),
    );
    return;
  }
  for (const message of messages) {
    const article = element("article", `message ${message.role}`);
    article.append(
      element("span", "message-role", message.role === "user" ? "YOU" : "AGENT"),
      element("p", "message-content", message.content),
    );
    ui.chatList.append(article);
  }
  ui.chatList.scrollTop = ui.chatList.scrollHeight;
}

function traceLabel(event) {
  return {
    model_response: "MODEL",
    tool: "TOOL",
    final: "ANSWER",
    runtime_error: "ERROR",
    loop_limit: "LIMIT",
    context_compacted: "MEMORY",
  }[event] || event.toUpperCase();
}

function renderTraces(traces) {
  ui.traceList.replaceChildren();
  if (!traces.length) {
    ui.traceList.append(element("p", "empty-state", "发送消息后，这里会出现循环步骤。"));
    return;
  }
  for (const trace of traces) {
    const card = element("article", `trace-card event-${trace.event}`);
    const heading = element("div", "trace-heading");
    heading.append(
      element("strong", "", traceLabel(trace.event)),
      element("span", "", `step ${trace.step} · ${trace.duration_ms} ms`),
    );
    const details = element("pre", "trace-data", JSON.stringify(trace.data, null, 2));
    card.append(heading, details);
    ui.traceList.append(card);
  }
}

function renderTodos(todos) {
  ui.todoList.replaceChildren();
  if (!todos.length) {
    ui.todoList.append(element("li", "empty-state", "当前 session 还没有待办。"));
    return;
  }
  for (const todo of todos) {
    const item = element("li", todo.done ? "done" : "");
    item.append(
      element("span", "todo-status", todo.done ? "完成" : "待办"),
      element("span", "", todo.text),
    );
    ui.todoList.append(item);
  }
}

function render(state) {
  page.sessionId = state.current_session_id;
  const current = state.sessions.find((item) => item.id === page.sessionId);
  ui.currentSession.textContent = current?.title || page.sessionId;
  renderSessions(state);
  renderMessages(state.messages);
  renderTraces(state.traces);
  renderTodos(state.todos);
}

function setBusy(busy, message = "") {
  page.busy = busy;
  ui.chatInput.disabled = busy;
  ui.sendButton.disabled = busy;
  ui.newSessionTitle.disabled = busy;
  ui.status.textContent = message;
  document.body.classList.toggle("is-busy", busy);
}

async function refresh(sessionId = null) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  render(await request(`/api/state${query}`));
}

ui.sessionList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-session-id]");
  if (!button || page.busy) return;
  setBusy(true, "正在切换 session…");
  try {
    await refresh(button.dataset.sessionId);
    ui.status.textContent = "";
  } catch (error) {
    ui.status.textContent = error.message;
  } finally {
    setBusy(false, ui.status.textContent);
  }
});

ui.newSessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (page.busy) return;
  setBusy(true, "正在创建 session…");
  try {
    const payload = await request("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title: ui.newSessionTitle.value }),
    });
    ui.newSessionTitle.value = "";
    render(payload.state);
    ui.status.textContent = "新 session 已创建。";
  } catch (error) {
    ui.status.textContent = error.message;
  } finally {
    setBusy(false, ui.status.textContent);
  }
});

ui.quickPrompts.addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (!button || page.busy) return;
  ui.chatInput.value = button.dataset.prompt;
  ui.chatInput.focus();
});

ui.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = ui.chatInput.value.trim();
  if (!message || !page.sessionId || page.busy) return;
  setBusy(true, "Agent 正在思考，可能会调用多个工具…");
  try {
    const payload = await request("/api/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: page.sessionId, message }),
    });
    ui.chatInput.value = "";
    render(payload.state);
    ui.status.textContent = "Agent 已完成这一轮。";
  } catch (error) {
    ui.status.textContent = error.message;
  } finally {
    setBusy(false, ui.status.textContent);
  }
});

refresh().catch((error) => {
  ui.status.textContent = `无法连接本地 Agent：${error.message}`;
});
```

- [ ] **Step 5: Implement the visual system and responsive loop rail**

Replace `mini_agent/web_static/app.css` with CSS that defines the approved
tokens and these exact behaviors:

```css
:root {
  color-scheme: light;
  --canvas: #e9eef5;
  --surface: #f8faff;
  --surface-strong: #ffffff;
  --ink: #172033;
  --muted: #69758a;
  --line: #ccd5e2;
  --blue: #315ef6;
  --blue-soft: #e5ebff;
  --green: #168a72;
  --amber: #c47a13;
  --red: #c24b43;
  --shadow: 0 18px 50px rgb(28 45 75 / 10%);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
  background:
    linear-gradient(rgb(49 94 246 / 4%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(49 94 246 / 4%) 1px, transparent 1px),
    var(--canvas);
  background-size: 28px 28px;
  color: var(--ink);
  font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
}
button, input, textarea { font: inherit; }
button { cursor: pointer; }
button:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 3px solid rgb(49 94 246 / 32%);
  outline-offset: 2px;
}
.shell {
  display: grid;
  grid-template-columns: 240px minmax(420px, 1fr) 360px;
  min-height: 100vh;
}
.sessions-panel, .chat-panel, .inspector-panel { min-width: 0; }
.sessions-panel {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 24px 18px;
  border-right: 1px solid var(--line);
  background: rgb(248 250 255 / 88%);
  backdrop-filter: blur(14px);
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  display: grid;
  width: 42px; height: 42px;
  place-items: center;
  border-radius: 12px 4px 12px 4px;
  background: var(--blue);
  color: white;
  font: 700 22px/1 "Cascadia Code", Consolas, monospace;
}
h1, h2, p { margin: 0; }
h1 { font-size: 18px; }
h2 { font-size: 17px; }
.eyebrow {
  margin-bottom: 4px;
  color: var(--muted);
  font: 700 10px/1.2 "Cascadia Code", Consolas, monospace;
  letter-spacing: .12em;
  text-transform: uppercase;
}
.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.badge {
  padding: 4px 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  font: 600 10px/1 "Cascadia Code", Consolas, monospace;
}
.new-session-form label, .chat-form label {
  display: block;
  margin-bottom: 7px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.inline-form, .composer { display: flex; gap: 8px; }
input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-strong);
  color: var(--ink);
}
input { padding: 9px 10px; }
textarea { min-height: 78px; padding: 12px; resize: vertical; }
button {
  border: 0;
  border-radius: 9px;
  padding: 9px 13px;
  background: var(--blue);
  color: white;
  font-weight: 700;
}
button:disabled { cursor: wait; opacity: .55; }
.session-list { display: grid; gap: 7px; }
.session-item {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 11px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
.session-item span {
  overflow: hidden;
  color: var(--muted);
  font: 10px/1.2 "Cascadia Code", Consolas, monospace;
  text-overflow: ellipsis;
}
.session-item.active {
  border-color: #bdc9ff;
  background: var(--blue-soft);
  color: #183bba;
}
.local-note { margin-top: auto; color: var(--muted); font-size: 11px; }
.chat-panel {
  display: grid;
  grid-template-rows: auto auto minmax(240px, 1fr) auto auto;
  gap: 16px;
  padding: 28px clamp(22px, 4vw, 52px);
}
.chat-header { display: flex; justify-content: space-between; gap: 20px; }
.runtime-state {
  align-self: center;
  color: var(--green);
  font: 700 11px/1 "Cascadia Code", Consolas, monospace;
}
.runtime-state span {
  display: inline-block;
  width: 8px; height: 8px;
  margin-right: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 5px rgb(22 138 114 / 12%);
}
.quick-prompts { display: flex; flex-wrap: wrap; gap: 8px; }
.quick-prompts button {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  font-size: 12px;
}
.chat-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: auto;
  padding: 8px 2px 18px;
}
.message {
  max-width: 78%;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 4px 16px 16px;
  background: var(--surface-strong);
  box-shadow: 0 8px 24px rgb(28 45 75 / 6%);
}
.message.user {
  align-self: flex-end;
  border-color: #b9c7ff;
  border-radius: 16px 4px 16px 16px;
  background: var(--blue-soft);
}
.message-role {
  display: block;
  margin-bottom: 7px;
  color: var(--blue);
  font: 800 10px/1 "Cascadia Code", Consolas, monospace;
  letter-spacing: .1em;
}
.message-content { white-space: pre-wrap; line-height: 1.65; }
.status-message { min-height: 20px; color: var(--amber); font-size: 13px; }
.composer { align-items: flex-end; }
.composer button { min-width: 82px; min-height: 46px; }
.inspector-panel {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  border-left: 1px solid var(--line);
  background: #111a2c;
  color: #e8eefc;
}
.inspector-section { padding: 24px 20px; }
.trace-section { min-height: 0; overflow: auto; border-bottom: 1px solid #2a3650; }
.inspector-panel .eyebrow, .inspector-panel .badge { color: #93a0b8; }
.inspector-panel .badge { border-color: #34415a; }
.trace-list { position: relative; display: grid; gap: 12px; margin-top: 22px; padding-left: 22px; }
.trace-list::before {
  position: absolute;
  inset: 4px auto 4px 6px;
  width: 1px;
  background: #3b4a68;
  content: "";
}
.trace-card {
  position: relative;
  padding: 12px;
  border: 1px solid #2f3d57;
  border-radius: 10px;
  background: #182238;
}
.trace-card::before {
  position: absolute;
  top: 16px; left: -22px;
  width: 11px; height: 11px;
  border: 3px solid #111a2c;
  border-radius: 50%;
  background: #6e83ae;
  content: "";
}
.trace-card.event-tool::before { background: #45c5a6; }
.trace-card.event-final::before { background: #7698ff; }
.trace-card.event-runtime_error::before { background: #e46d66; }
.trace-heading { display: flex; justify-content: space-between; gap: 8px; }
.trace-heading strong { color: #aebfff; font: 800 11px/1 "Cascadia Code", Consolas, monospace; }
.trace-heading span { color: #8997b2; font: 10px/1 "Cascadia Code", Consolas, monospace; }
.trace-data {
  max-height: 150px;
  overflow: auto;
  margin: 10px 0 0;
  color: #c7d2e8;
  font: 10px/1.55 "Cascadia Code", Consolas, monospace;
  white-space: pre-wrap;
}
.todo-list { display: grid; gap: 9px; margin: 18px 0 0; padding: 0; list-style: none; }
.todo-list li { display: flex; gap: 9px; align-items: center; color: #d7e0f2; }
.todo-status { color: #72d5bd; font: 800 10px/1 "Cascadia Code", Consolas, monospace; }
.todo-list li.done { opacity: .55; text-decoration: line-through; }
.empty-state { color: var(--muted); font-size: 13px; }
.inspector-panel .empty-state { color: #8997b2; }
.is-busy .runtime-state { color: var(--amber); }

@media (max-width: 980px) {
  .shell { grid-template-columns: 210px minmax(380px, 1fr); }
  .inspector-panel { grid-column: 1 / -1; grid-template-columns: 1fr 1fr; grid-template-rows: none; }
}
@media (max-width: 680px) {
  .shell { display: flex; flex-direction: column; }
  .chat-panel { order: 1; min-height: 100vh; padding: 22px 16px; }
  .sessions-panel { order: 2; border: 0; border-top: 1px solid var(--line); }
  .inspector-panel { order: 3; display: block; }
  .message { max-width: 92%; }
  .chat-header { align-items: flex-start; flex-direction: column; }
}
@media (prefers-reduced-motion: no-preference) {
  .trace-card { animation: trace-in 180ms ease-out both; }
  @keyframes trace-in {
    from { opacity: 0; transform: translateY(5px); }
  }
}
```

- [ ] **Step 6: Run static contract and full web tests**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_web -v
```

Expected: all web tests pass with `OK`.

- [ ] **Step 7: Commit Task 5**

```powershell
git add mini_agent/web_static tests/test_web.py
git commit -m "feat: build agent lab web interface"
```

---

### Task 6: Document the Web Demo and Update the Recording Guide

**Files:**

- Modify: `README.md`
- Modify: `demo.ps1`
- Modify: `tests/test_docs.py`
- Modify only if a new real issue occurs: `PROBLEM_SOLVING.md`

**Interfaces:**

- Produces: beginner-copyable web launch and fallback CLI instructions.
- Produces: web recording sequence for two sessions, follow-ups, trace, todo, and tests.
- Preserves: `.env` detection without printing or assigning the Key.

- [ ] **Step 1: Write failing documentation assertions**

Add to `tests/test_docs.py`:

```python
    def test_readme_and_demo_cover_the_local_web_ui(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        demo = Path("demo.ps1").read_text(encoding="utf-8")

        for text in [
            "python -m mini_agent.web",
            "http://127.0.0.1:8000",
            "API Key 不会进入浏览器",
            "weather-chat",
            "weekly-report",
        ]:
            self.assertIn(text, readme)
        for text in [
            "python -m mini_agent.web",
            "天气 + 待办",
            "周报 + 待办",
            "Agent Trace",
            "python -m unittest discover -s tests -v",
        ]:
            self.assertIn(text, demo)
```

- [ ] **Step 2: Run the documentation tests and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_docs -v
```

Expected: FAIL because README and `demo.ps1` do not mention the web entry point.

- [ ] **Step 3: Add a beginner web section to README**

Insert after the existing CLI startup section:

````markdown
## 网页演示：更直观地观察 Agent 循环

在项目根目录运行：

```powershell
python -m mini_agent.web
```

程序默认打开 <http://127.0.0.1:8000>。如果浏览器没有自动打开，可以手动复制这个地址。网页只监听本机，不是公网服务。

左侧用于创建和切换 `weather-chat`、`weekly-report` 等独立 session；中间发送消息；右侧同步显示 Agent Trace 和当前 session 的待办。API Key 仍由 Python 从当前进程环境或项目根目录 `.env` 读取，API Key 不会进入浏览器。

录屏时依次演示：

1. 在第一个 session 使用“天气 + 待办”。
2. 新建第二个 session，使用“周报 + 待办”。
3. 切回第一个 session 追问天气和待办。
4. 再切回第二个 session 追问周报。
5. 展示右侧不同 session 的 Agent Trace 与待办不串线。
6. 最后在终端运行 `python -m unittest discover -s tests -v`。

CLI 仍可通过 `python -m mini_agent` 使用；网页只是新增展示层，二者共用同一个 SQLite 数据库和 Agent Runtime。
````

- [ ] **Step 4: Replace only the recording steps in demo.ps1**

Keep the tested environment/`.env` detection block unchanged. Replace the
numbered output with:

```powershell
Write-Host "1. 启动本地网页" ;
Write-Host "   python -m mini_agent.web" ;
Write-Host "2. 在 weather-chat 演示“天气 + 待办”" ;
Write-Host "3. 在 weekly-report 演示“周报 + 待办”" ;
Write-Host "4. 分别切回两个 session 继续追问" ;
Write-Host "5. 展示右侧 Agent Trace 与当前 Session 待办" ;
Write-Host "6. 运行完整 unittest" ;
Write-Host "   python -m unittest discover -s tests -v" ;
```

Preserve the UTF-8 BOM required by Windows PowerShell 5.1.

- [ ] **Step 5: Run documentation and full offline tests**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest tests.test_docs -v
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest discover -s tests -v
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m compileall -q mini_agent tests
git diff --check
```

Expected: all tests pass, compileall exits 0, and `git diff --check` exits 0.

- [ ] **Step 6: Commit Task 6**

```powershell
git add README.md demo.ps1 tests/test_docs.py
git add PROBLEM_SOLVING.md  # only if a new observed issue was documented
git commit -m "docs: add local web demo guide"
```

---

### Task 7: Real Browser Verification, Recording, and Submission Evidence

**Files:**

- Create after recording: `artifacts/demo.mp4`
- Modify only if new observed problems occur: `PROBLEM_SOLVING.md`

**Interfaces:**

- Consumes: real local ignored `.env`, real DeepSeek API, completed web UI.
- Produces: browser demonstration, visual QA evidence, final recording under 50 MB, clean verification evidence.

- [ ] **Step 1: Run fresh automated verification**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest discover -s tests -v
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m compileall -q mini_agent tests
git diff --check
```

Expected: all tests end with `OK`; compileall and diff check exit 0.

- [ ] **Step 2: Scan tracked source for frameworks and secret-shaped values**

Run:

```powershell
rg -n "langgraph|openhands|openclaw|sk-[A-Za-z0-9]" . `
  -g "*.py" -g "*.md" -g "*.toml" -g "*.ps1" -g "*.html" -g "*.css" -g "*.js"
git status --short
```

Expected: framework names appear only in explanatory “not used” text; no real
secret-shaped value appears; `.env` remains ignored.

- [ ] **Step 3: Start the real local web server**

Run in a visible PowerShell:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m mini_agent.web
```

Expected: terminal prints `Minimal Agent web: http://127.0.0.1:8000`, browser
opens, and neither terminal nor page displays the Key.

- [ ] **Step 4: Perform real API and session-isolation acceptance**

In the browser:

```text
weather-chat:
  查询杭州 2026-07-28 的天气，并添加待办带雨伞

weekly-report:
  生成一份本周工作周报，并添加待办检查周报

weather-chat follow-up:
  我们刚才查了什么天气？请列出这个 session 的待办

weekly-report follow-up:
  回顾刚才的周报，并列出这个 session 的待办
```

Expected:

- real DeepSeek answers are non-empty;
- trace includes MODEL, TOOL, and ANSWER nodes;
- weather and weekly todos remain isolated;
- returning to each session restores its messages;
- mock weather is not presented as live weather.

- [ ] **Step 5: Perform browser visual QA**

Use the local-browser control skill or manual browser:

```text
1280px desktop:
  three columns visible; chat readable; trace rail aligned; no horizontal overflow

680px narrow:
  chat first; sessions second; inspector third; keyboard focus visible

reduced motion:
  no required information depends on animation

error state:
  stop server or use missing-key process; input remains and a useful message appears
```

Take screenshots for inspection only; do not commit screenshots unless the
user asks.

- [ ] **Step 6: Record the final browser demo**

The recording must show:

```text
web page opens without showing the Key
weather-chat tool loop and todo
weekly-report answer and separate todo
follow-up in both sessions
right-side trace and todo isolation
terminal full unittest result
```

Save to `artifacts/demo.mp4`. Then run:

```powershell
$video = Get-Item -LiteralPath 'artifacts\demo.mp4'
"bytes=$($video.Length)"
if ($video.Length -ge 50MB) { exit 1 }
```

Expected: file exists and is below 50 MB.

- [ ] **Step 7: Commit verified recording and any real problem record**

```powershell
git add artifacts/demo.mp4
if (git status --short -- PROBLEM_SOLVING.md) {
  git add PROBLEM_SOLVING.md
}
git commit -m "chore: add web agent demo"
git status --short --branch
```

Expected: clean feature worktree.

- [ ] **Step 8: Run final whole-branch review and publish only with user approval**

Generate a whole-branch review package from the branch merge base, dispatch
the most capable reviewer, fix every Critical/Important finding in one fix
wave, re-run the full verification commands, and re-review.

After review is approved:

```text
If a remote already exists:
  push the reviewed branch.

If no remote exists:
  ask the user to choose public or private;
  create/connect the GitHub repository only after that explicit choice.
```

Never publish the ignored `.env`, `.agent-data`, review scratch files, or a
recording containing the Key.
