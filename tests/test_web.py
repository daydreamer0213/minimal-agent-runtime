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

from mini_agent.llm import LLMError
from mini_agent.store import SessionStore
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


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "agent.db"
        self.runtime_factory = Mock()
        self.server = create_server(
            ("127.0.0.1", 0),
            self.db_path,
            runtime_factory=self.runtime_factory,
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
        self.assertIn("text/html", headers["Content-Type"])

        status, body, headers = self.request("/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"font-family", body)
        self.assertIn("text/css", headers["Content-Type"])

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
        self.runtime_factory.assert_not_called()

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
        server = create_server(
            ("0.0.0.0", 0),
            Path(self.temp.name) / "wildcard.db",
            environment={},
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _, port = server.server_address[:2]
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
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

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
        server = create_server(
            ("::1", 0),
            Path(self.temp.name) / "ipv6.db",
            environment={},
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
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
            server.shutdown()
            server.server_close()
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
