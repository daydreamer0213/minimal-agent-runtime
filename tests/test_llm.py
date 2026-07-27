import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from mini_agent.llm import DeepSeekClient, LLMAuthError, LLMError, ToolCall, parse_response


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class LLMTests(unittest.TestCase):
    def test_parser_extracts_reasoning_tool_calls_and_content(self):
        payload = {
            "choices": [{
                "message": {
                    "reasoning_content": "需要精确计算",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calculator",
                            "arguments": '{"expression":"2+2"}',
                        },
                    }],
                }
            }]
        }

        result = parse_response(payload)

        self.assertEqual(result.reasoning_content, "需要精确计算")
        self.assertEqual(result.tool_calls[0].name, "calculator")
        self.assertEqual(result.tool_calls[0].arguments, '{"expression":"2+2"}')

    def test_tool_call_converts_to_openai_api_shape(self):
        tool_call = ToolCall("call_1", "calculator", '{"expression":"2+2"}')

        self.assertEqual(
            tool_call.as_api_dict(),
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'},
            },
        )

    def test_parser_normalizes_missing_optional_text(self):
        result = parse_response({
            "choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call_1",
                "function": {"name": "calculator", "arguments": "{}"},
            }]}}]
        })

        self.assertEqual(result.content, "")
        self.assertEqual(result.reasoning_content, "")

    def test_parser_rejects_empty_message(self):
        with self.assertRaisesRegex(LLMError, "模型没有返回答案或工具调用"):
            parse_response({"choices": [{"message": {}}]})

    def test_parser_rejects_malformed_choices_and_tool_calls(self):
        invalid_payloads = [
            {},
            {"choices": []},
            {"choices": [{}]},
            {"choices": [{"message": {"content": "ok", "tool_calls": [{
                "id": "call_1", "function": {"name": "calculator", "arguments": {}}
            }]}}]},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(LLMError):
                    parse_response(payload)

    def test_parser_rejects_duplicate_tool_call_ids(self):
        payload = {
            "choices": [{"message": {"tool_calls": [
                {"id": "call_1", "function": {"name": "calculator", "arguments": "{}"}},
                {"id": "call_1", "function": {"name": "weather", "arguments": "{}"}},
            ]}}]
        }

        with self.assertRaisesRegex(LLMError, "重复"):
            parse_response(payload)

    @patch("mini_agent.llm.urlopen")
    def test_client_sends_deepseek_configuration(self, urlopen):
        urlopen.return_value = FakeHTTPResponse({
            "choices": [{"message": {"content": "你好"}}]
        })
        client = DeepSeekClient("secret")

        result = client.chat([{"role": "user", "content": "你好"}], [])

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 60)
        self.assertEqual(result.content, "你好")

    @patch("mini_agent.llm.urlopen")
    def test_client_includes_tools_only_when_present(self, urlopen):
        urlopen.return_value = FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]})
        tools = [{"type": "function", "function": {"name": "calculator"}}]

        DeepSeekClient("secret", base_url="https://example.test/").chat([], tools)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/chat/completions")
        self.assertEqual(json.loads(request.data)["tools"], tools)

    @patch("mini_agent.llm.time.sleep")
    @patch("mini_agent.llm.urlopen")
    def test_client_retries_transient_http_error(self, urlopen, sleep):
        urlopen.side_effect = [
            HTTPError("https://api.deepseek.com/chat/completions", 429, "busy", None, None),
            FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]}),
        ]

        result = DeepSeekClient("secret").chat([], [])

        self.assertEqual(result.content, "ok")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("mini_agent.llm.time.sleep")
    @patch("mini_agent.llm.urlopen")
    def test_client_retries_url_errors_at_most_twice(self, urlopen, sleep):
        urlopen.side_effect = [
            URLError("offline"),
            URLError("offline"),
            FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]}),
        ]

        DeepSeekClient("secret").chat([], [])

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_args_list[0].args, (0.5,))
        self.assertEqual(sleep.call_args_list[1].args, (1.0,))

    @patch("mini_agent.llm.time.sleep")
    @patch("mini_agent.llm.urlopen")
    def test_client_raises_llm_error_after_retry_limit(self, urlopen, sleep):
        urlopen.side_effect = [URLError("offline")] * 3

        with self.assertRaises(LLMError):
            DeepSeekClient("secret").chat([], [])

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("mini_agent.llm.urlopen")
    def test_client_auth_error_does_not_leak_key(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "unauthorized", None, None
        )

        with self.assertRaises(LLMAuthError) as caught:
            DeepSeekClient("extremely-secret-key").chat([], [])

        self.assertNotIn("extremely-secret-key", str(caught.exception))
        self.assertEqual(urlopen.call_count, 1)

    @patch("mini_agent.llm.urlopen")
    def test_client_does_not_retry_non_transient_http_errors(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://api.deepseek.com/chat/completions", 400, "bad request", None, None
        )

        with self.assertRaises(LLMError):
            DeepSeekClient("secret").chat([], [])

        self.assertEqual(urlopen.call_count, 1)

    def test_client_wraps_request_serialization_errors(self):
        with self.assertRaises(LLMError):
            DeepSeekClient("secret").chat([{"role": "user", "content": object()}], [])

    def test_client_rejects_negative_retry_limits(self):
        with self.assertRaises(ValueError):
            DeepSeekClient("secret", max_retries=-1)


if __name__ == "__main__":
    unittest.main()
