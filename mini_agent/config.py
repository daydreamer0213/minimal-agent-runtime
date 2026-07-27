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
