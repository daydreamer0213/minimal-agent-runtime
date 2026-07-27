"""Command-line interface for the minimal agent."""

import argparse
import json
import os
import sys
from pathlib import Path

from .llm import DeepSeekClient, LLMError
from .runtime import AgentError, AgentRuntime
from .store import SessionStore
from .tools import build_default_registry


_HELP = """Commands:
  /new [title]       Create and switch to a new session.
  /sessions          List saved sessions.
  /use <session_id>  Switch to an existing session.
  /trace             Show recent trace entries for this session.
  /help              Show this help.
  /exit              Exit the program."""

_DOTENV_VARIABLES = {
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
}


class ConfigurationError(ValueError):
    """A safe-to-display local configuration error."""


def main(argv: list[str] | None = None) -> int:
    _configure_console_streams()
    parser = argparse.ArgumentParser(description="A minimal DeepSeek agent")
    parser.add_argument("--db", default=".agent-data/agent.db", help="SQLite database path")
    parser.add_argument("--session", help="Reuse or create this session ID")
    parser.add_argument("--once", help="Run one prompt and exit")
    args = parser.parse_args(argv)
    api_key: str | None = None

    store: SessionStore | None = None
    try:
        db_path = Path(args.db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(db_path)
        session_id = _select_session(store, args.session)

        if args.once is not None:
            _load_dotenv()
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                _print_missing_api_key()
                return 2
            runtime = _build_runtime(store, api_key)
            return _run_prompt(runtime, session_id, args.once, api_key)

        print(f"Current session: {session_id}")
        return _interactive_loop(store, session_id)
    except (AgentError, LLMError) as error:
        print(f"Error: {_safe_text(str(error), api_key)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Error: {_safe_text(str(error), api_key)}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def _configure_console_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


def _load_dotenv() -> None:
    path = Path.cwd() / ".env"
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


def _build_client(api_key: str) -> DeepSeekClient:
    options = {}
    if base_url := os.environ.get("DEEPSEEK_BASE_URL"):
        options["base_url"] = base_url
    if model := os.environ.get("DEEPSEEK_MODEL"):
        options["model"] = model
    return DeepSeekClient(api_key, **options)


def _build_runtime(store: SessionStore, api_key: str) -> AgentRuntime:
    return AgentRuntime(store, _build_client(api_key), build_default_registry())


def _print_missing_api_key() -> None:
    print(
        "DEEPSEEK_API_KEY is required. Set it before starting the agent.",
        file=sys.stderr,
    )


def _select_session(store: SessionStore, requested_id: str | None) -> str:
    if requested_id is None:
        return store.create_session()
    if store.session_exists(requested_id):
        return requested_id
    return store.create_session(session_id=requested_id)


def _run_prompt(runtime: AgentRuntime, session_id: str, prompt: str, api_key: str) -> int:
    try:
        print(runtime.run(session_id, prompt))
    except (AgentError, LLMError) as error:
        print(f"Error: {_safe_text(str(error), api_key)}", file=sys.stderr)
        return 1
    return 0


def _interactive_loop(store: SessionStore, session_id: str) -> int:
    runtime: AgentRuntime | None = None
    api_key: str | None = None
    while True:
        try:
            command = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not command:
            continue
        if command.startswith("/"):
            should_exit, session_id = _handle_command(store, command, session_id)
            if should_exit:
                return 0
            continue
        if runtime is None:
            _load_dotenv()
            api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            _print_missing_api_key()
            continue
        if runtime is None:
            runtime = _build_runtime(store, api_key)
        _run_prompt(runtime, session_id, command, api_key)


def _handle_command(store: SessionStore, command: str, session_id: str) -> tuple[bool, str]:
    name, _, argument = command.partition(" ")
    argument = argument.strip()
    if name == "/new":
        session_id = store.create_session(title=argument)
        print(f"Current session: {session_id}")
    elif name == "/sessions":
        _print_sessions(store.list_sessions(), session_id)
    elif name == "/use":
        if not argument:
            print("Usage: /use <session_id>")
        elif not store.session_exists(argument):
            print(f"No session named: {argument}")
        else:
            session_id = argument
            print(f"Current session: {session_id}")
    elif name == "/trace":
        _print_traces(store.list_traces(session_id))
    elif name == "/help":
        print(_HELP)
    elif name == "/exit":
        return True, session_id
    else:
        print(f"Unknown command: {name}. Type /help for commands.")
    return False, session_id


def _print_sessions(sessions: list[dict], current_id: str) -> None:
    if not sessions:
        print("No saved sessions.")
        return
    for session in sessions:
        marker = "*" if session["id"] == current_id else " "
        title = f" — {session['title']}" if session["title"] else ""
        print(f"{marker} {session['id']}{title}")


def _print_traces(traces: list[dict]) -> None:
    if not traces:
        print("No trace entries for this session.")
        return
    for trace in reversed(traces):
        data = _truncate(json.dumps(trace["data"], ensure_ascii=False, sort_keys=True))
        print(f"step {trace['step']} {trace['event']} ({trace['duration_ms']} ms): {data}")


def _truncate(text: str, limit: int = 240) -> str:
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def _safe_text(message: str, api_key: str | None) -> str:
    return message.replace(api_key, "[redacted]") if api_key else message
