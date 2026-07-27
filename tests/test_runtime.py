import tempfile
import traceback
import unittest
from pathlib import Path

from mini_agent.llm import LLMResponse, ToolCall
from mini_agent.runtime import AgentError, AgentLoopLimitError, AgentRuntime
from mini_agent.store import SessionStore
from mini_agent.tools import build_default_registry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "agent.db")
        self.session = self.store.create_session("测试", "test-session")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def runtime(self, responses, **options):
        fake = FakeLLM(responses)
        runtime = AgentRuntime(
            self.store,
            fake,
            build_default_registry(),
            **options,
        )
        return runtime, fake

    def test_returns_direct_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("你好，我能帮你使用工具。", "直接回答即可", [])
        ])

        answer = runtime.run(self.session, "你好")

        self.assertEqual(answer, "你好，我能帮你使用工具。")
        self.assertEqual(fake.calls[0]["messages"][-1]["content"], "你好")

    def test_final_reasoning_is_traced_but_not_reused_as_context(self):
        runtime, fake = self.runtime([
            LLMResponse("第一次回答", "不应回传的最终推理", []),
            LLMResponse("第二次回答", "第二次推理", []),
        ])
        runtime.run(self.session, "第一次")

        runtime.run(self.session, "第二次")

        previous_answer = next(
            message for message in fake.calls[1]["messages"]
            if message.get("content") == "第一次回答"
        )
        self.assertNotIn("reasoning_content", previous_answer)
        traces = self.store.list_traces(self.session)
        first_model_trace = next(
            trace for trace in reversed(traces)
            if trace["event"] == "model_response"
        )
        self.assertEqual(
            first_model_trace["data"]["reasoning_content"],
            "不应回传的最终推理",
        )

    def test_weather_then_todo_then_final_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("", "先查天气", [
                ToolCall("call_weather", "weather", '{"city":"杭州","date":"2026-07-28"}')
            ]),
            LLMResponse("", "根据天气记待办", [
                ToolCall("call_todo", "todo", '{"action":"add","text":"带雨伞"}')
            ]),
            LLMResponse("杭州天气为模拟小雨，已添加“带雨伞”。", "结果齐全", []),
        ])

        answer = runtime.run(self.session, "查杭州明天天气，如果下雨就提醒我带伞")

        self.assertIn("已添加", answer)
        self.assertEqual(self.store.list_todos(self.session)[0]["text"], "带雨伞")
        second_messages = fake.calls[1]["messages"]
        assistant = next(message for message in second_messages if message.get("tool_calls"))
        self.assertEqual(assistant["reasoning_content"], "先查天气")
        self.assertTrue(any(message["role"] == "tool" for message in second_messages))

    def test_multiple_tool_calls_are_saved_and_executed_in_order(self):
        runtime, _ = self.runtime([
            LLMResponse("", "依次计算", [
                ToolCall("first", "calculator", '{"expression":"1+1"}'),
                ToolCall("second", "calculator", '{"expression":"2+2"}'),
            ]),
            LLMResponse("结果是 2 和 4。", "汇总结果", []),
        ])

        runtime.run(self.session, "计算两个表达式")

        messages = self.store.context_messages(self.session)
        tool_assistant_index = next(
            index for index, message in enumerate(messages)
            if message.get("tool_calls")
        )
        tool_message_indexes = [
            index for index, message in enumerate(messages)
            if message["role"] == "tool"
        ]
        self.assertEqual(
            [messages[index]["tool_call_id"] for index in tool_message_indexes],
            ["first", "second"],
        )
        self.assertTrue(all(
            tool_assistant_index < index for index in tool_message_indexes
        ))
        self.assertEqual(
            [messages[index]["content"] for index in tool_message_indexes],
            ['{"ok": true, "result": 2}', '{"ok": true, "result": 4}'],
        )

    def test_unexpected_tool_execution_error_is_sanitized_and_traced(self):
        secret = "tool-secret-must-not-escape"
        runtime, _ = self.runtime([
            LLMResponse("", "调用工具", [
                ToolCall("broken", "calculator", '{"expression":"1+1"}')
            ]),
        ])

        def fail_execute(*_):
            raise RuntimeError(secret)

        runtime.registry.execute = fail_execute

        with self.assertRaises(AgentError) as raised:
            runtime.run(self.session, "触发工具错误")

        self.assertNotIn(secret, str(raised.exception))
        trace = self.store.list_traces(self.session)[0]
        self.assertEqual(trace["event"], "runtime_error")
        self.assertEqual(
            trace["data"],
            {"error_type": "RuntimeError", "stage": "tool"},
        )
        self.assertNotIn(secret, str(trace))

    def test_unserializable_tool_result_error_is_sanitized_and_traced(self):
        runtime, _ = self.runtime([
            LLMResponse("", "调用工具", [
                ToolCall("broken", "calculator", '{"expression":"1+1"}')
            ]),
        ])
        runtime.registry.execute = lambda *_: {
            "ok": True,
            "result": object(),
        }

        with self.assertRaises(AgentError) as raised:
            runtime.run(self.session, "触发序列化错误")

        self.assertEqual(str(raised.exception), "工具执行失败: TypeError")
        trace = self.store.list_traces(self.session)[0]
        self.assertEqual(trace["event"], "runtime_error")
        self.assertEqual(
            trace["data"],
            {"error_type": "TypeError", "stage": "tool"},
        )

    def test_bad_tool_arguments_are_returned_to_model(self):
        runtime, fake = self.runtime([
            LLMResponse("", "参数写错了", [
                ToolCall("bad", "calculator", "{not-json")
            ]),
            LLMResponse("工具参数无效，请重新描述。", "解释错误", []),
        ])

        answer = runtime.run(self.session, "计算")

        self.assertIn("参数无效", answer)
        tool_message = next(
            message for message in fake.calls[1]["messages"]
            if message["role"] == "tool"
        )
        self.assertIn('"ok": false', tool_message["content"])

    def test_stops_after_maximum_steps(self):
        calls = [
            LLMResponse("", "继续", [
                ToolCall(f"call_{index}", "calculator", '{"expression":"1+1"}')
            ])
            for index in range(3)
        ]
        runtime, fake = self.runtime(calls, max_steps=3)

        with self.assertRaises(AgentLoopLimitError):
            runtime.run(self.session, "一直计算")

        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(
            self.store.list_traces(self.session)[0]["event"],
            "loop_limit",
        )

    def test_follow_up_receives_previous_answer(self):
        runtime, fake = self.runtime([
            LLMResponse("杭州明天可能下雨。", "回答天气", []),
            LLMResponse("我刚才说杭州明天可能下雨。", "读取历史", []),
        ])
        runtime.run(self.session, "杭州明天天气呢？")

        answer = runtime.run(self.session, "你刚才说什么？")

        self.assertIn("可能下雨", answer)
        contents = [
            message.get("content", "")
            for message in fake.calls[1]["messages"]
        ]
        self.assertIn("杭州明天可能下雨。", contents)

    def test_two_sessions_do_not_share_context(self):
        other = self.store.create_session("另一个", "other")
        runtime, fake = self.runtime([
            LLMResponse("窗口一回答", "回答", []),
            LLMResponse("窗口二回答", "回答", []),
        ])
        runtime.run(self.session, "窗口一秘密")
        runtime.run(other, "窗口二问题")

        second_contents = " ".join(
            message.get("content", "")
            for message in fake.calls[1]["messages"]
        )
        self.assertNotIn("窗口一秘密", second_contents)

    def test_long_context_is_summarized_without_splitting_recent_turns(self):
        for turn in range(1, 4):
            self.store.add_message(self.session, turn, "user", "很长问题" * 20)
            self.store.add_message(self.session, turn, "assistant", "很长回答" * 20)
        runtime, fake = self.runtime([
            LLMResponse("早期对话摘要", "总结", []),
            LLMResponse("已接着回答", "使用摘要", []),
        ], context_chars=100, context_messages=2)

        answer = runtime.run(self.session, "继续")

        self.assertEqual(answer, "已接着回答")
        self.assertEqual(self.store.get_summary(self.session), "早期对话摘要")
        final_messages = fake.calls[-1]["messages"]
        self.assertTrue(any(
            message["role"] == "system" and "早期对话摘要" in message["content"]
            for message in final_messages
        ))

    def test_invalid_summary_does_not_mark_messages_compacted(self):
        for turn in range(1, 4):
            self.store.add_message(self.session, turn, "user", "旧问题" * 20)
            self.store.add_message(self.session, turn, "assistant", "旧回答" * 20)
        runtime, _ = self.runtime([
            LLMResponse("", "错误地调用工具", [
                ToolCall("summary_tool", "calculator", '{"expression":"1+1"}')
            ]),
            LLMResponse("仍然完成主回答", "回答", []),
        ], context_chars=100)

        answer = runtime.run(self.session, "继续")

        self.assertEqual(answer, "仍然完成主回答")
        self.assertEqual(self.store.get_summary(self.session), "")
        contents = [
            message.get("content", "")
            for message in self.store.context_messages(self.session)
        ]
        self.assertIn("旧问题" * 20, contents)
        self.assertTrue(any(
            trace["event"] == "context_compression_failed"
            for trace in self.store.list_traces(self.session)
        ))

    def test_model_errors_are_traced_without_secret_details(self):
        secret = "secret-token-must-not-be-traced"
        runtime, _ = self.runtime([RuntimeError(secret)])

        try:
            runtime.run(self.session, "触发错误")
        except AgentError as error:
            formatted_traceback = "".join(traceback.format_exception(error))
            self.assertEqual(str(error), "模型调用失败: RuntimeError")
            self.assertNotIn(secret, str(error))
        else:
            self.fail("AgentError not raised")

        trace = self.store.list_traces(self.session)[0]
        self.assertEqual(trace["event"], "runtime_error")
        self.assertEqual(trace["data"]["stage"], "model")
        self.assertNotIn(secret, str(trace["data"]))
        self.assertNotIn(secret, formatted_traceback)


if __name__ == "__main__":
    unittest.main()
