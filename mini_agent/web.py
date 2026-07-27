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


def _authorities_for(host: str, port: int) -> set[str]:
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authorities = {f"{host}:{port}"}
    if port == 80:
        authorities.add(host)
    return authorities


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
        self.configured_host = address[0]
        self.db_path = Path(db_path)
        self.runtime_factory = runtime_factory
        self.environment = environment
        self.chat_lock = threading.Lock()
        self.state_lock = threading.RLock()
        super().__init__(address, AgentRequestHandler)

    def allowed_authorities(self) -> set[str]:
        hosts = {self.configured_host, self.server_address[0]}
        if hosts & {"127.0.0.1", "localhost", "::1"}:
            hosts.update({"127.0.0.1", "localhost", "::1"})
        return set().union(
            *(_authorities_for(host, self.server_port) for host in hosts if host)
        )


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer

    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            self._validate_local_request()
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

    def _validate_local_request(self) -> None:
        local_hosts = self.server.allowed_authorities()
        host = self.headers.get("Host", "").lower()
        if host not in local_hosts:
            raise RequestProblem(403, "请求必须使用本地 Host")

        origin = self.headers.get("Origin")
        local_origins = {f"http://{value}" for value in local_hosts}
        if origin is not None and origin.lower() not in local_origins:
            raise RequestProblem(403, "请求来源不受允许")

    def _get(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/state":
            session_ids = parse_qs(
                parsed.query,
                keep_blank_values=True,
            ).get("session_id")
            if session_ids is None:
                requested = None
            elif len(session_ids) != 1:
                raise RequestProblem(400, "session_id 只能出现一次")
            elif not session_ids[0]:
                raise RequestProblem(404, "session 不存在")
            else:
                requested = session_ids[0]
            with self.server.state_lock:
                with self._store() as store:
                    state = self._state(store, requested)
            self._json(200, state)
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
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise RequestProblem(415, "Content-Type 必须是 application/json")
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
        if length < 0:
            raise RequestProblem(400, "Content-Length 不能为负数")
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
        with self.server.state_lock:
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
    try:
        if not args.no_browser:
            try:
                opener = threading.Timer(0.25, webbrowser.open, args=(url,))
                opener.daemon = True
                opener.start()
            except Exception:
                print("Warning: browser could not be opened.", file=sys.stderr)
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
