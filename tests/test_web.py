import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mini_agent.llm import LLMError, LLMResponse, ToolCall
from mini_agent.runtime import AgentRuntime
from mini_agent.store import SessionStore
from mini_agent.tools import build_default_registry
from mini_agent.web import AgentRequestHandler, create_server, main


class TrackingLock:
    def __init__(self):
        self.depth = 0

    @property
    def held(self):
        return self.depth > 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *_):
        self.depth -= 1


class QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class WebTests(unittest.TestCase):
    def test_web_request_error_contract_and_send_input_order(self):
        source = Path("mini_agent/web_static/app.js").read_text(encoding="utf-8")

        request_start = source.index("async function request")
        request_end = source.index("\nfunction ", request_start)
        request_block = source[request_start:request_end]
        for marker in [
            "await fetch",
            "TypeError",
            "NETWORK_ERROR_MESSAGE",
            "if (!response.ok)",
            "payload.error || bodyText",
        ]:
            self.assertIn(marker, request_block)

        send_start = source.index("async function sendMessage")
        send_end = source.index("\nfunction ", send_start)
        send_block = source[send_start:send_end]
        self.assertLess(
            send_block.index("render(state)"),
            send_block.index('ui.chatInput.value = ""'),
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "agent.db"
        self.responses = []
        self.runtime_calls = 0

        def runtime_factory(store, _api_key):
            self.runtime_calls += 1
            return AgentRuntime(
                store,
                QueueLLM(self.responses),
                build_default_registry(),
            )

        self.runtime_factory = runtime_factory
        self.server = create_server(
            ("127.0.0.1", 0),
            self.db_path,
            runtime_factory=self.runtime_factory,
            environment={"DEEPSEEK_API_KEY": "test-key"},
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
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def request(self, path, *, method="GET", payload=None, headers=None):
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        with urlopen(request, timeout=2) as response:
            return response.status, response.read(), response.headers

    def raw_post(self, path, body, headers):
        host, port = self.server.server_address
        request_headers = {
            "Host": f"{host}:{port}",
            "Connection": "close",
            **headers,
        }
        lines = [f"POST {path} HTTP/1.1"]
        lines.extend(f"{name}: {value}" for name, value in request_headers.items())
        payload = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n" + body
        with socket.create_connection((host, port), timeout=2) as connection:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            response = b""
            while chunk := connection.recv(4096):
                response += chunk
        return int(response.split(b" ", 2)[1]), response

    def test_static_files_are_whitelisted_and_reject_queries(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'data-app="minimal-agent"', body)
        for marker in [
            b'id="session-list"',
            b'id="chat-list"',
            b'id="trace-list"',
            b'id="todo-list"',
            b'id="status-message"',
            b'id="quick-prompts"',
        ]:
            self.assertIn(marker, body)
        self.assertIn("text/html", headers["Content-Type"])

        _, script, _ = self.request("/app.js")
        self.assertIn(b"fetch(path", script)
        self.assertIn(b"/api/state", script)
        self.assertIn(b"textContent", script)
        self.assertNotIn(b"innerHTML", script)

        script_text = script.decode("utf-8")
        html = body.decode("utf-8")
        self.assertIn(
            'data-prompt="查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。"',
            html,
        )
        self.assertIn(
            'data-prompt="根据“本周完成 Agent 工具循环和网页界面”生成简短周报，'
            '并使用 todo 工具添加一条“检查周报”待办。"',
            html,
        )
        self.assertIn('role === "assistant"', script_text)
        self.assertIn('return "AGENT";', script_text)
        self.assertIn("aria-current", script_text)
        self.assertIn("/api/sessions", script_text)
        self.assertIn("/api/chat", script_text)
        self.assertIn("function ensureStatePayload(state, actionLabel)", script_text)
        self.assertIn('const state = ensureStatePayload(payload, "状态刷新");', script_text)
        self.assertIn('const state = ensureStatePayload(payload.state, "会话创建");', script_text)
        self.assertIn('const state = ensureStatePayload(payload.state, "聊天");', script_text)
        self.assertIn("无法连接本地服务，请确认服务器仍在运行，然后重试。", script_text)
        self.assertIn('const requiredArrays = ["sessions", "messages", "traces", "todos"]', script_text)
        self.assertIn("Array.isArray(state[key])", script_text)
        self.assertIn("typeof state.current_session_id !== \"string\"", script_text)
        self.assertIn("已完成：收到回复。", script_text)

        status, body, headers = self.request("/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"font-family", body)
        self.assertIn("text/css", headers["Content-Type"])
        css = body.decode("utf-8")
        self.assertIn("@media (max-width: 1199px) and (min-width: 768px)", css)
        self.assertIn("@media (max-width: 767px)", css)
        self.assertIn("@media (max-width: 375px)", css)
        self.assertIn("@media (prefers-reduced-motion: no-preference)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 375px)", mobile_start)
        mobile_block = css[mobile_start:mobile_end]
        order = [
            ".chat-panel { grid-area: chat; }",
            ".sessions-panel { grid-area: sessions; }",
            ".inspector-panel { grid-area: inspector; }",
        ]
        cursor = 0
        for marker in order:
            marker_index = mobile_block.index(marker, cursor)
            cursor = marker_index + len(marker)

        status, body, headers = self.request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b'"use strict"', body)
        self.assertIn("text/javascript", headers["Content-Type"])

        for path in ("/../../README.md", "/app.css?cache=1"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as unknown:
                self.request(path)
            self.assertEqual(unknown.exception.code, 404)

    def test_state_creates_first_session_without_constructing_runtime(self):
        status, body, _ = self.request("/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(state["sessions"]), 1)
        self.assertEqual(
            state["current_session_id"],
            state["sessions"][0]["id"],
        )
        self.assertEqual(self.runtime_calls, 0)

    def test_chat_runs_real_runtime_tools_and_returns_refreshed_state(self):
        _, created, _ = self.request(
            "/api/sessions",
            method="POST",
            payload={"title": "天气"},
        )
        session_id = json.loads(created)["session_id"]
        self.responses.extend(
            [
                LLMResponse(
                    "",
                    "先查天气",
                    [
                        ToolCall(
                            "weather-call",
                            "weather",
                            '{"city":"杭州","date":"2026-07-28"}',
                        )
                    ],
                ),
                LLMResponse(
                    "",
                    "添加待办",
                    [
                        ToolCall(
                            "todo-call",
                            "todo",
                            '{"action":"add","text":"带雨伞"}',
                        )
                    ],
                ),
                LLMResponse("已查询模拟天气并添加待办。", "完成", []),
            ]
        )

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
        self.assertEqual(self.runtime_calls, 0)

        body = b"{}"
        status, _ = self.raw_post(
            "/api/chat",
            body,
            {
                "Content-Type": "application/json",
                "Content-Length": str(32 * 1024 + 1),
            },
        )
        self.assertEqual(status, 413)

    def test_chat_rejects_invalid_values_without_running_runtime(self):
        invalid_payloads = [
            {"session_id": "", "message": "你好"},
            {"session_id": 1, "message": "你好"},
            {"session_id": "missing", "message": 1},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as invalid:
                self.request("/api/chat", method="POST", payload=payload)
            self.assertEqual(invalid.exception.code, 400)

        self.assertEqual(self.runtime_calls, 0)

    def test_chat_rejects_unknown_session_without_running_runtime(self):
        with self.assertRaises(HTTPError) as unknown:
            self.request(
                "/api/chat",
                method="POST",
                payload={"session_id": "missing", "message": "你好"},
            )

        self.assertEqual(unknown.exception.code, 404)
        self.assertEqual(self.runtime_calls, 0)

    def test_chat_requires_json_content_type_without_running_runtime(self):
        body = b'{"session_id":"missing","message":"hello"}'
        status, _ = self.raw_post(
            "/api/chat",
            body,
            {
                "Content-Type": "text/plain",
                "Content-Length": str(len(body)),
            },
        )

        self.assertEqual(status, 415)
        self.assertEqual(self.runtime_calls, 0)

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

    def test_unexpected_factory_errors_are_sanitized(self):
        _, body, _ = self.request("/api/state")
        session_id = json.loads(body)["current_session_id"]
        self.server.environment = {"DEEPSEEK_API_KEY": "factory-key"}

        def unexpected_factory(*_):
            raise RuntimeError("factory-key must not reach browser")

        self.server.runtime_factory = unexpected_factory
        with self.assertRaises(HTTPError) as failed:
            self.request(
                "/api/chat",
                method="POST",
                payload={"session_id": session_id, "message": "触发错误"},
            )

        error_body = failed.exception.read().decode("utf-8")
        self.assertEqual(failed.exception.code, 500)
        self.assertNotIn("factory-key", error_body)
        self.assertNotIn("Traceback", error_body)

    def test_state_recursively_redacts_trace_keys_and_values(self):
        _, body, _ = self.request("/api/state")
        session_id = json.loads(body)["current_session_id"]
        store = SessionStore(self.db_path)
        try:
            store.add_trace(
                session_id,
                1,
                "tool",
                {
                    "test-key": "first value",
                    "[redacted]": "second value",
                    "nested": [
                        "test-key in a list",
                        {"nested-test-key": "test-key in a value"},
                    ],
                    "long": "x" * 2001,
                    "count": 7,
                },
            )
        finally:
            store.close()

        _, state_body, _ = self.request(
            f"/api/state?session_id={session_id}"
        )
        trace_data = json.loads(state_body)["traces"][0]["data"]

        self.assertNotIn("test-key", state_body.decode("utf-8"))
        self.assertEqual(trace_data["[redacted]"], "first value")
        self.assertEqual(trace_data["nested"][0], "[redacted] in a list")
        self.assertEqual(
            trace_data["nested"][1]["nested-[redacted]"],
            "[redacted] in a value",
        )
        self.assertEqual(len(trace_data["long"]), 2000)
        self.assertEqual(trace_data["count"], 7)

    def test_state_rejects_unknown_session(self):
        with self.assertRaises(HTTPError) as unknown:
            self.request("/api/state?session_id=missing")
        self.assertEqual(unknown.exception.code, 404)

    def test_state_rejects_blank_session_id(self):
        with self.assertRaises(HTTPError) as blank:
            self.request("/api/state?session_id=")
        self.assertEqual(blank.exception.code, 404)

    def test_state_rejects_repeated_session_id(self):
        with self.assertRaises(HTTPError) as repeated:
            self.request("/api/state?session_id=first&session_id=second")
        self.assertEqual(repeated.exception.code, 400)

    def test_session_creation_trims_title_and_returns_new_state(self):
        status, body, _ = self.request(
            "/api/sessions",
            method="POST",
            payload={"title": "  weather  "},
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        payload = json.loads(body)

        self.assertEqual(status, 201)
        self.assertEqual(payload["state"]["current_session_id"], payload["session_id"])
        current = next(
            session for session in payload["state"]["sessions"]
            if session["id"] == payload["session_id"]
        )
        self.assertEqual(current["title"], "weather")

    def test_session_creation_rejects_non_string_or_long_titles(self):
        for title in (42, "x" * 81):
            with self.subTest(title=title), self.assertRaises(HTTPError) as invalid:
                self.request(
                    "/api/sessions",
                    method="POST",
                    payload={"title": title},
                )
            self.assertEqual(invalid.exception.code, 400)

    def test_session_creation_requires_json_content_type(self):
        body = b'{"title": "blocked"}'
        status, _ = self.raw_post(
            "/api/sessions",
            body,
            {
                "Content-Type": "text/plain",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 415)

    def test_local_host_is_required(self):
        with self.assertRaises(HTTPError) as response:
            self.request("/api/state", headers={"Host": "evil.invalid"})
        self.assertEqual(response.exception.code, 403)
        self.assertNotIn(b"evil.invalid", response.exception.read())

    def test_configured_host_and_port_are_accepted(self):
        self.server.configured_host = "agent.example"
        authority = f"agent.example:{self.server.server_port}"

        status, _, _ = self.request(
            "/api/state",
            headers={
                "Host": authority,
                "Origin": f"http://{authority}",
            },
        )

        self.assertEqual(status, 200)

    def test_wildcard_binding_accepts_connected_target_authority_only(self):
        original_address = self.server.server_address
        try:
            self.server.configured_host = "0.0.0.0"
            self.server.server_address = ("0.0.0.0", self.server.server_port)
            port = self.server.server_port
            authority = f"127.0.0.1:{port}"
            request = Request(
                f"http://{authority}/api/state",
                headers={"Origin": f"http://{authority}"},
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)

            with self.assertRaises(HTTPError) as hostile:
                request = Request(
                    f"http://{authority}/api/state",
                    headers={"Host": "evil.invalid"},
                )
                urlopen(request, timeout=2)
            self.assertEqual(hostile.exception.code, 403)
        finally:
            self.server.server_address = original_address

    def test_default_http_port_authority_may_omit_port(self):
        self.server.configured_host = "agent.example"
        self.server.server_port = 80

        authorities = self.server.allowed_authorities()

        self.assertIn("agent.example", authorities)
        self.assertIn("agent.example:80", authorities)

    def test_mismatched_origin_is_rejected(self):
        with self.assertRaises(HTTPError) as response:
            self.request(
                "/api/state",
                headers={"Origin": "http://evil.invalid"},
            )
        self.assertEqual(response.exception.code, 403)
        self.assertNotIn(b"evil.invalid", response.exception.read())

    def test_session_creation_rejects_malformed_json(self):
        status, _ = self.raw_post(
            "/api/sessions",
            b"not-json",
            {
                "Content-Type": "application/json",
                "Content-Length": "8",
            },
        )
        self.assertEqual(status, 400)

    def test_session_creation_rejects_negative_content_length_before_reading(self):
        body = b'{"title": "negative"}'
        status, _ = self.raw_post(
            "/api/sessions",
            body,
            {
                "Content-Type": "application/json",
                "Content-Length": "-1",
            },
        )
        self.assertEqual(status, 400)

    def test_session_creation_rejects_oversized_content_length(self):
        status, _ = self.raw_post(
            "/api/sessions",
            b"{}",
            {
                "Content-Type": "application/json",
                "Content-Length": str(32 * 1024 + 1),
            },
        )
        self.assertEqual(status, 413)

    def test_llm_errors_redact_configured_key(self):
        secret = "not-a-real-secret"
        self.server.environment = {"DEEPSEEK_API_KEY": secret}
        with patch.object(
            AgentRequestHandler,
            "_get",
            side_effect=LLMError(f"failed with {secret}"),
        ):
            with self.assertRaises(HTTPError) as response:
                self.request("/")
        self.assertEqual(response.exception.code, 502)
        body = response.exception.read()
        self.assertIn(b"[redacted]", body)
        self.assertNotIn(secret.encode("utf-8"), body)

    def test_concurrent_state_initialization_creates_one_session(self):
        workers = 8
        start = threading.Barrier(workers)
        statuses = []
        failures = []
        results_lock = threading.Lock()
        original_list_sessions = SessionStore.list_sessions

        def slow_empty_list(store):
            sessions = original_list_sessions(store)
            if not sessions:
                time.sleep(0.05)
            return sessions

        def request_state():
            try:
                start.wait(timeout=2)
                status, _, _ = self.request("/api/state")
                with results_lock:
                    statuses.append(status)
            except Exception as error:
                with results_lock:
                    failures.append(error)

        with patch.object(SessionStore, "list_sessions", new=slow_empty_list):
            threads = [threading.Thread(target=request_state) for _ in range(workers)]
            for worker in threads:
                worker.start()
            for worker in threads:
                worker.join(timeout=3)

        self.assertEqual(failures, [])
        self.assertTrue(all(not worker.is_alive() for worker in threads))
        self.assertEqual(statuses, [200] * workers)
        store = SessionStore(self.db_path)
        try:
            self.assertEqual(len(store.list_sessions()), 1)
        finally:
            store.close()

    def test_state_releases_lock_before_writing_response(self):
        tracking_lock = TrackingLock()
        writes_while_locked = []
        original_json = AgentRequestHandler._json
        self.server.state_lock = tracking_lock

        def record_json(handler, status, payload):
            writes_while_locked.append(tracking_lock.held)
            return original_json(handler, status, payload)

        with patch.object(AgentRequestHandler, "_json", new=record_json):
            status, _, _ = self.request("/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(writes_while_locked, [False])

    @unittest.skipUnless(socket.has_ipv6, "IPv6 is unavailable")
    def test_ipv6_loopback_server_uses_ipv6_and_bracketed_authority(self):
        server = None
        thread = None
        try:
            try:
                server = create_server(
                    ("::1", 0),
                    Path(self.temp.name) / "ipv6.db",
                    environment={},
                )
            except OSError as error:
                self.skipTest(f"IPv6 loopback is unavailable: {error}")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address[:2]
            authority = f"[{host}]:{port}"
            wire = (
                "GET /api/state HTTP/1.1\r\n"
                f"Host: {authority}\r\n"
                f"Origin: http://{authority}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            with socket.create_connection((host, port), timeout=2) as connection:
                connection.sendall(wire)
                connection.shutdown(socket.SHUT_WR)
                response = connection.recv(4096)

            self.assertEqual(server.address_family, socket.AF_INET6)
            self.assertTrue(response.startswith(b"HTTP/1.0 200"))
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

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

    @patch("mini_agent.web.load_dotenv")
    @patch("mini_agent.web.create_server")
    def test_main_keeps_explicit_host_and_port(
        self,
        server_factory,
        _load_dotenv,
    ):
        server = Mock()
        server.server_port = 9012
        server.serve_forever.side_effect = KeyboardInterrupt
        server_factory.return_value = server

        exit_code = main(
            [
                "--db",
                str(self.db_path),
                "--host",
                "0.0.0.0",
                "--port",
                "9012",
                "--no-browser",
            ]
        )

        self.assertEqual(exit_code, 0)
        server_factory.assert_called_once_with(
            ("0.0.0.0", 9012),
            str(self.db_path),
        )
        server.server_close.assert_called_once_with()

    @patch("mini_agent.web.load_dotenv")
    @patch("mini_agent.web.threading.Timer")
    @patch("mini_agent.web.create_server")
    def test_main_brackets_ipv6_browser_url(
        self,
        server_factory,
        timer_class,
        _load_dotenv,
    ):
        server = Mock()
        server.server_port = 8123
        server.serve_forever.side_effect = KeyboardInterrupt
        server_factory.return_value = server

        exit_code = main(["--db", str(self.db_path), "--host", "::1"])

        self.assertEqual(exit_code, 0)
        server_factory.assert_called_once_with(("::1", 8000), str(self.db_path))
        timer_class.assert_called_once_with(
            0.25,
            __import__("webbrowser").open,
            args=("http://[::1]:8123",),
        )
        server.server_close.assert_called_once_with()

    @patch("builtins.print")
    @patch("mini_agent.web.load_dotenv")
    @patch("mini_agent.web.threading.Timer", side_effect=RuntimeError("timer failed"))
    @patch("mini_agent.web.create_server")
    def test_main_serves_when_browser_timer_construction_fails(
        self,
        server_factory,
        _timer_class,
        _load_dotenv,
        print_mock,
    ):
        server = Mock()
        server.server_port = 8123
        server.serve_forever.side_effect = KeyboardInterrupt
        server_factory.return_value = server

        exit_code = main(["--db", str(self.db_path)])

        self.assertEqual(exit_code, 0)
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()
        messages = [str(call.args[0]) for call in print_mock.call_args_list if call.args]
        self.assertIn("Warning: browser could not be opened.", messages)
        self.assertNotIn("timer failed", "\n".join(messages))

    @patch("builtins.print")
    @patch("mini_agent.web.load_dotenv")
    @patch("mini_agent.web.threading.Timer")
    @patch("mini_agent.web.create_server")
    def test_main_serves_when_browser_timer_start_fails(
        self,
        server_factory,
        timer_class,
        _load_dotenv,
        print_mock,
    ):
        server = Mock()
        server.server_port = 8123
        server.serve_forever.side_effect = KeyboardInterrupt
        timer_class.return_value.start.side_effect = RuntimeError("timer failed")
        server_factory.return_value = server

        exit_code = main(["--db", str(self.db_path)])

        self.assertEqual(exit_code, 0)
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()
        messages = [str(call.args[0]) for call in print_mock.call_args_list if call.args]
        self.assertIn("Warning: browser could not be opened.", messages)
        self.assertNotIn("timer failed", "\n".join(messages))


if __name__ == "__main__":
    unittest.main()
