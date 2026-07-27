"""Core agent loop coordinating the model, tools, and session state."""

import json
import time
from datetime import date
from typing import Any

from .prompts import AGENT_SYSTEM_PROMPT, COMPRESSION_PROMPT
from .store import SessionStore
from .tools import ToolRegistry


class AgentError(Exception):
    """A recoverable agent runtime error."""


class AgentLoopLimitError(AgentError):
    """The model continued requesting tools beyond the configured limit."""


class AgentRuntime:
    def __init__(
        self,
        store: SessionStore,
        llm: Any,
        registry: ToolRegistry,
        *,
        max_steps: int = 8,
        context_chars: int = 12_000,
        context_messages: int = 24,
    ):
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        self.store = store
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.context_chars = context_chars
        self.context_messages = context_messages

    def run(self, session_id: str, user_input: str) -> str:
        if not self.store.session_exists(session_id):
            raise AgentError(f"Session 不存在: {session_id}")

        turn_id = self.store.next_turn_id(session_id)
        self.store.add_message(session_id, turn_id, "user", user_input)
        self._compress_if_needed(session_id)

        for step in range(1, self.max_steps + 1):
            started = time.perf_counter()
            try:
                response = self.llm.chat(
                    self._messages(session_id),
                    self.registry.schemas(),
                )
            except Exception as error:
                self._trace_error(
                    session_id,
                    step,
                    "runtime_error",
                    error,
                    started,
                    stage="model",
                )
                raise AgentError(
                    f"模型调用失败: {type(error).__name__}"
                ) from error

            self.store.add_trace(
                session_id,
                step,
                "model_response",
                {
                    "reasoning_content": response.reasoning_content,
                    "content": response.content,
                    "tool_calls": [
                        call.as_api_dict() for call in response.tool_calls
                    ],
                },
                self._elapsed_ms(started),
            )

            if response.tool_calls:
                tool_calls = [call.as_api_dict() for call in response.tool_calls]
                self.store.add_message(
                    session_id,
                    turn_id,
                    "assistant",
                    response.content,
                    response.reasoning_content,
                    tool_calls,
                )
                for call in response.tool_calls:
                    tool_started = time.perf_counter()
                    try:
                        try:
                            arguments = json.loads(call.arguments)
                        except json.JSONDecodeError as error:
                            result = {
                                "ok": False,
                                "error": f"工具参数不是合法 JSON: {error.msg}",
                            }
                        else:
                            result = self.registry.execute(
                                call.name,
                                arguments,
                                session_id,
                                self.store,
                            )
                        result_json = json.dumps(
                            result,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    except Exception as error:
                        self._trace_error(
                            session_id,
                            step,
                            "runtime_error",
                            error,
                            tool_started,
                            stage="tool",
                        )
                        raise AgentError(
                            f"工具执行失败: {type(error).__name__}"
                        ) from None
                    self.store.add_message(
                        session_id,
                        turn_id,
                        "tool",
                        result_json,
                        tool_call_id=call.id,
                    )
                    self.store.add_trace(
                        session_id,
                        step,
                        "tool",
                        {
                            "name": call.name,
                            "arguments": call.arguments,
                            "result": result,
                        },
                        self._elapsed_ms(tool_started),
                    )
                continue

            if response.content:
                self.store.add_message(
                    session_id,
                    turn_id,
                    "assistant",
                    response.content,
                )
                self.store.add_trace(
                    session_id,
                    step,
                    "final",
                    {"content": response.content},
                )
                return response.content

        self.store.add_trace(
            session_id,
            self.max_steps,
            "loop_limit",
            {"max_steps": self.max_steps},
        )
        raise AgentLoopLimitError(
            f"Agent 超过最大循环步数: {self.max_steps}"
        )

    def _compress_if_needed(self, session_id: str) -> None:
        message_count, content_chars = self.store.context_stats(session_id)
        if (
            message_count <= self.context_messages
            and content_chars <= self.context_chars
        ):
            return

        compactable = self.store.compactable_messages(
            session_id,
            keep_user_turns=2,
        )
        if not compactable:
            return

        payload = {
            "previous_summary": self.store.get_summary(session_id),
            "messages": compactable,
        }
        started = time.perf_counter()
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": COMPRESSION_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
                [],
            )
        except Exception as error:
            self._trace_error(
                session_id,
                0,
                "context_compression_failed",
                error,
                started,
            )
            return

        summary = response.content.strip()
        if not summary or response.tool_calls:
            self.store.add_trace(
                session_id,
                0,
                "context_compression_failed",
                {"error_type": "InvalidSummaryResponse"},
                self._elapsed_ms(started),
            )
            return

        self.store.save_summary_and_compact(
            session_id,
            summary,
            [message["id"] for message in compactable],
        )
        self.store.add_trace(
            session_id,
            0,
            "context_compacted",
            {"message_count": len(compactable)},
            self._elapsed_ms(started),
        )

    def _messages(self, session_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"{AGENT_SYSTEM_PROMPT}\n"
                    f"程序运行当天日期：{date.today().isoformat()}"
                ),
            }
        ]
        summary = self.store.get_summary(session_id)
        if summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"Session 记忆：\n{summary}",
                }
            )
        messages.extend(self.store.context_messages(session_id))
        return messages

    def _trace_error(
        self,
        session_id: str,
        step: int,
        event: str,
        error: Exception,
        started: float,
        *,
        stage: str | None = None,
    ) -> None:
        data = {"error_type": type(error).__name__}
        if stage:
            data["stage"] = stage
        self.store.add_trace(
            session_id,
            step,
            event,
            data,
            self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))
