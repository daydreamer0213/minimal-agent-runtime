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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A minimal DeepSeek agent")
    parser.add_argument("--db", default=".agent-data/agent.db", help="SQLite database path")
    parser.add_argument("--session", help="Reuse or create this session ID")
    parser.add_argument("--once", help="Run one prompt and exit")
    args = parser.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print(
            "DEEPSEEK_API_KEY is required. Set it before starting the agent.",
            file=sys.stderr,
        )
        return 2

    store: SessionStore | None = None
    try:
        db_path = Path(args.db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(db_path)
        session_id = _select_session(store, args.session)
        runtime = AgentRuntime(store, _build_client(api_key), build_default_registry())

        if args.once is not None:
            return _run_prompt(runtime, session_id, args.once, api_key)

        print(f"Current session: {session_id}")
        return _interactive_loop(store, runtime, session_id, api_key)
    except (AgentError, LLMError) as error:
        print(f"Error: {_safe_text(str(error), api_key)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Error: {_safe_text(str(error), api_key)}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def _build_client(api_key: str) -> DeepSeekClient:
    options = {}
    if base_url := os.environ.get("DEEPSEEK_BASE_URL"):
        options["base_url"] = base_url
    if model := os.environ.get("DEEPSEEK_MODEL"):
        options["model"] = model
    return DeepSeekClient(api_key, **options)


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


def _interactive_loop(
    store: SessionStore, runtime: AgentRuntime, session_id: str, api_key: str
) -> int:
    while True:
        try:
            command = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not command:
            continue
        if command.startswith("/"):
            should_exit, session_id = _handle_command(store, command, session_id, api_key)
            if should_exit:
                return 0
            continue
        _run_prompt(runtime, session_id, command, api_key)


def _handle_command(
    store: SessionStore, command: str, session_id: str, api_key: str
) -> tuple[bool, str]:
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


def _safe_text(message: str, api_key: str) -> str:
    return message.replace(api_key, "[redacted]")
