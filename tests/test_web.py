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
            payload={"title": "  澶╂皵  "},
        )
        payload = json.loads(body)

        self.assertEqual(status, 201)
        self.assertEqual(payload["state"]["current_session_id"], payload["session_id"])
        current = next(
            session for session in payload["state"]["sessions"]
            if session["id"] == payload["session_id"]
        )
        self.assertEqual(current["title"], "澶╂皵")

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
