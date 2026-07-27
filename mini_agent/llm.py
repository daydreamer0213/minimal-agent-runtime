"""Dependency-free client for DeepSeek's OpenAI-compatible chat API."""

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMError(Exception):
    """A request or response error from the language model."""


class LLMAuthError(LLMError):
    """Authentication was rejected by the language model service."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_api_dict(self) -> dict[str, Any]:
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


def parse_response(payload: dict[str, Any]) -> LLMResponse:
    """Parse the first completion choice returned by DeepSeek."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMError("模型响应缺少 choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMError("模型响应格式无效")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMError("模型响应缺少 message")

    content = _optional_text(message, "content")
    reasoning_content = _optional_text(message, "reasoning_content")
    tool_calls = _parse_tool_calls(message.get("tool_calls"))
    if not content and not tool_calls:
        raise LLMError("模型没有返回答案或工具调用")

    return LLMResponse(content, reasoning_content, tool_calls)


def _optional_text(message: dict[str, Any], field: str) -> str:
    value = message.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LLMError(f"模型响应的 {field} 必须是字符串")
    return value


def _parse_tool_calls(value: Any) -> list[ToolCall]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LLMError("模型响应的 tool_calls 必须是列表")

    calls = []
    for item in value:
        if not isinstance(item, dict):
            raise LLMError("模型响应包含无效工具调用")
        function = item.get("function")
        call_id = item.get("id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not function["name"]
            or not isinstance(function.get("arguments"), str)
        ):
            raise LLMError("模型响应包含无效工具调用")
        calls.append(ToolCall(call_id, function["name"], function["arguments"]))
    return calls


class DeepSeekClient:
    """Call DeepSeek's OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-pro",
        timeout: float = 60,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "stream": False,
        }
        if tools:
            body["tools"] = tools

        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        return self._send(request)

    def _send(self, request: Request) -> LLMResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    try:
                        payload = json.loads(response.read().decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise LLMError("DeepSeek 返回了无效 JSON") from error
                return parse_response(payload)
            except HTTPError as error:
                if error.code in (401, 403):
                    raise LLMAuthError("DeepSeek authentication failed; check API key configuration") from error
                if error.code not in (429, 500, 502, 503, 504):
                    raise LLMError(f"DeepSeek request failed with HTTP {error.code}") from error
                last_error = error
            except URLError as error:
                last_error = error
            except LLMError:
                raise
            except Exception as error:
                raise LLMError("DeepSeek request failed") from error

            if attempt == self.max_retries:
                raise LLMError("DeepSeek request failed after retries") from last_error
            time.sleep(0.5 * (2**attempt))

        raise AssertionError("unreachable")
