import tempfile
import unittest
from pathlib import Path

from mini_agent.store import SessionStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "agent.db"
        self.store = SessionStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sessions_keep_messages_and_todos_separate(self):
        first = self.store.create_session("天气", "weather-chat")
        second = self.store.create_session("周报", "weekly-report")
        self.store.add_message(first, 1, "user", "杭州天气如何？")
        self.store.add_message(second, 1, "user", "帮我写周报")
        self.store.add_todo(first, "带雨伞")
        self.store.add_todo(second, "整理数据")

        self.assertEqual(
            [message["content"] for message in self.store.context_messages(first)],
            ["杭州天气如何？"],
        )
        self.assertEqual(
            [item["text"] for item in self.store.list_todos(first)],
            ["带雨伞"],
        )
        self.assertEqual(
            [item["text"] for item in self.store.list_todos(second)],
            ["整理数据"],
        )

    def test_data_survives_reopening_database(self):
        session_id = self.store.create_session("持久化", "persistent")
        self.store.add_message(session_id, 1, "assistant", "记住这句话")
        self.store.close()
        self.store = SessionStore(self.db_path)

        messages = self.store.context_messages(session_id)

        self.assertEqual(messages[0]["content"], "记住这句话")

    def test_compaction_marks_whole_old_turns(self):
        session_id = self.store.create_session("长对话", "long-chat")
        for turn in range(1, 5):
            self.store.add_message(session_id, turn, "user", f"问题 {turn}")
            self.store.add_message(session_id, turn, "assistant", f"回答 {turn}")
        self.store.add_message(session_id, 5, "assistant", "后台事件")

        rows = self.store.compactable_messages(session_id, keep_user_turns=2)
        self.store.save_summary_and_compact(
            session_id,
            "前两轮摘要",
            [row["id"] for row in rows],
        )

        self.assertEqual(self.store.get_summary(session_id), "前两轮摘要")
        self.assertEqual(
            [message["content"] for message in self.store.context_messages(session_id)],
            ["问题 4", "回答 4", "后台事件"],
        )

    def test_trace_is_filtered_by_session(self):
        first = self.store.create_session("一", "one")
        second = self.store.create_session("二", "two")
        self.store.add_trace(first, 1, "tool", {"name": "calculator"})
        self.store.add_trace(second, 1, "tool", {"name": "weather"})

        traces = self.store.list_traces(first)

        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["data"]["name"], "calculator")


if __name__ == "__main__":
    unittest.main()
