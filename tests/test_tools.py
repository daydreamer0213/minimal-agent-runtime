import tempfile
import unittest
from pathlib import Path

from mini_agent.store import SessionStore
from mini_agent.tools import Tool, ToolRegistry, build_default_registry


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "agent.db")
        self.session_id = self.store.create_session("工具", "tools")
        self.registry = build_default_registry()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def execute(self, name, arguments):
        return self.registry.execute(name, arguments, self.session_id, self.store)

    def test_registry_exposes_openai_function_schemas(self):
        schemas = self.registry.schemas()
        names = {item["function"]["name"] for item in schemas}
        self.assertEqual(names, {"calculator", "search", "weather", "todo"})
        self.assertTrue(all(item["type"] == "function" for item in schemas))

    def test_calculator_evaluates_arithmetic(self):
        result = self.execute("calculator", {"expression": "25 * (18 + 2)"})
        self.assertEqual(result, {"ok": True, "result": 500})

    def test_calculator_rejects_python_code(self):
        result = self.execute("calculator", {"expression": "__import__('os').getcwd()"})
        self.assertFalse(result["ok"])
        self.assertIn("不支持", result["error"])

    def test_calculator_limits_exponents_and_accepts_allowed_operators(self):
        result = self.execute("calculator", {"expression": "-(7 // 2) + 2 ** 3 % 3"})
        self.assertEqual(result, {"ok": True, "result": -1})
        limited = self.execute("calculator", {"expression": "2 ** 21"})
        self.assertFalse(limited["ok"])
        self.assertIn("指数", limited["error"])

    def test_schema_reports_missing_argument(self):
        result = self.execute("weather", {"city": "杭州"})
        self.assertEqual(result["ok"], False)
        self.assertIn("date", result["error"])

    def test_weather_is_deterministic_and_marked_as_mock(self):
        first = self.execute("weather", {"city": "杭州", "date": "2026-07-28"})
        second = self.execute("weather", {"city": "杭州", "date": "2026-07-28"})
        self.assertEqual(first, second)
        self.assertEqual(first["source"], "mock")

    def test_search_is_mock_and_returns_at_most_five_results(self):
        result = self.execute("search", {"query": "Agent Runtime"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "mock")
        self.assertLessEqual(len(result["results"]), 5)

    def test_schema_validates_types_and_enum_values(self):
        number = self.execute("calculator", {"expression": 4})
        self.assertFalse(number["ok"])
        self.assertIn("expression", number["error"])
        action = self.execute("todo", {"action": "remove"})
        self.assertFalse(action["ok"])
        self.assertIn("action", action["error"])

    def test_todo_is_scoped_to_current_session(self):
        added = self.execute("todo", {"action": "add", "text": "带雨伞"})
        listed = self.execute("todo", {"action": "list"})
        self.assertTrue(added["ok"])
        self.assertEqual(listed["items"][0]["text"], "带雨伞")

    def test_todo_done_requires_positive_id_and_updates_current_session(self):
        added = self.execute("todo", {"action": "add", "text": "带雨伞"})
        result = self.execute("todo", {"action": "done", "id": added["item"]["id"]})
        self.assertEqual(result, {"ok": True, "done": True})
        invalid = self.execute("todo", {"action": "done", "id": 0})
        self.assertFalse(invalid["ok"])

    def test_registry_rejects_duplicate_names(self):
        registry = ToolRegistry()
        tool = Tool("sample", "sample", {"type": "object"}, lambda *_: {"ok": True})
        registry.register(tool)
        with self.assertRaisesRegex(ValueError, "工具已注册: sample"):
            registry.register(tool)

    def test_unknown_tool_returns_recoverable_error(self):
        result = self.execute("missing", {})
        self.assertFalse(result["ok"])
        self.assertIn("calculator", result["available_tools"])


if __name__ == "__main__":
    unittest.main()
