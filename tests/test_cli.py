import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.cli import main
from mini_agent.store import SessionStore


class CLITests(unittest.TestCase):
    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_once_mode_loads_deepseek_settings_from_dotenv(
        self, client_class, runtime_class
    ):
        runtime_class.return_value.run.return_value = "loaded"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "\ufeff# local settings\n"
                " DEEPSEEK_API_KEY = 'dotenv-key' \n"
                'DEEPSEEK_BASE_URL = "https://dotenv.example/api"\n'
                "DEEPSEEK_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True), patch(
                "mini_agent.cli.Path.cwd", return_value=root
            ), patch("sys.stdout", io.StringIO()):
                exit_code = main(
                    ["--db", str(root / "agent.db"), "--once", "hello"]
                )

        self.assertEqual(exit_code, 0)
        client_class.assert_called_once_with(
            "dotenv-key",
            base_url="https://dotenv.example/api",
            model="dotenv-model",
        )

    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_process_environment_overrides_dotenv(
        self, client_class, _runtime_class
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "DEEPSEEK_API_KEY=dotenv-key\n"
                "DEEPSEEK_BASE_URL=https://dotenv.example\n"
                "DEEPSEEK_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            environment = {
                "DEEPSEEK_API_KEY": "process-key",
                "DEEPSEEK_BASE_URL": "https://process.example",
                "DEEPSEEK_MODEL": "process-model",
            }
            with patch.dict("os.environ", environment, clear=True), patch(
                "mini_agent.cli.Path.cwd", return_value=root
            ), patch("sys.stdout", io.StringIO()):
                exit_code = main(
                    ["--db", str(root / "agent.db"), "--once", "hello"]
                )

        self.assertEqual(exit_code, 0)
        client_class.assert_called_once_with(
            "process-key",
            base_url="https://process.example",
            model="process-model",
        )

    @patch("mini_agent.cli.DeepSeekClient")
    def test_malformed_supported_dotenv_line_is_sanitized(self, client_class):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "must-not-appear"
            (root / ".env").write_text(
                f'DEEPSEEK_API_KEY="{secret}\n', encoding="utf-8"
            )
            errors = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch(
                "mini_agent.cli.Path.cwd", return_value=root
            ), patch("sys.stderr", errors):
                exit_code = main(
                    ["--db", str(root / "agent.db"), "--once", "hello"]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("DEEPSEEK_API_KEY", errors.getvalue())
        self.assertNotIn(secret, errors.getvalue())
        client_class.assert_not_called()

    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_once_mode_creates_named_session_and_prints_answer(
        self, client_class, runtime_class
    ):
        runtime_class.return_value.run.return_value = "答案是 4"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(
                "os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True
            ), patch("mini_agent.cli.Path.cwd", return_value=root):
                output = io.StringIO()
                with patch("sys.stdout", output):
                    exit_code = main(
                        [
                            "--db",
                            str(root / "agent.db"),
                            "--session",
                            "demo",
                            "--once",
                            "2+2 等于多少？",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertIn("答案是 4", output.getvalue())
        runtime_class.return_value.run.assert_called_once_with("demo", "2+2 等于多少？")
        client_class.assert_called_once_with("test-key")

    def test_missing_api_key_has_clear_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch(
                "mini_agent.cli.Path.cwd", return_value=root
            ):
                with patch("sys.stderr", output):
                    exit_code = main(
                        ["--db", str(root / "agent.db"), "--once", "你好"]
                    )

        self.assertEqual(exit_code, 2)
        self.assertIn("DEEPSEEK_API_KEY", output.getvalue())

    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_local_commands_work_without_an_api_key(self, client_class, runtime_class):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "agent.db"
            store = SessionStore(db_path)
            store.create_session("existing", "main")
            store.add_trace("main", 1, "final", {"content": "done"})
            store.close()

            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch("sys.stdout", output), patch(
                "builtins.input", side_effect=["/sessions", "/trace", "/exit"]
            ):
                exit_code = main(["--db", str(db_path), "--session", "main"])

        self.assertEqual(exit_code, 0)
        self.assertIn("main", output.getvalue())
        self.assertIn("final", output.getvalue())
        client_class.assert_not_called()
        runtime_class.assert_not_called()

    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_interactive_local_commands_do_not_run_the_agent(
        self, _client_class, runtime_class
    ):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "agent.db"
            store = SessionStore(db_path)
            store.create_session("existing", "main")
            store.add_trace("main", 1, "final", {"content": "done"}, 7)
            store.close()

            output = io.StringIO()
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
                with patch("sys.stdout", output), patch(
                    "builtins.input",
                    side_effect=["/new plans", "/sessions", "/use main", "/trace", "/help", "/exit"],
                ):
                    exit_code = main(["--db", str(db_path), "--session", "main"])

            self.assertEqual(exit_code, 0)
            self.assertIn("plans", output.getvalue())
            self.assertIn("final", output.getvalue())
            self.assertIn("/trace", output.getvalue())
            runtime_class.return_value.run.assert_not_called()

    @patch("mini_agent.cli.AgentRuntime")
    @patch("mini_agent.cli.DeepSeekClient")
    def test_environment_overrides_configure_client(self, client_class, _runtime_class):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(
                "os.environ",
                {
                    "DEEPSEEK_API_KEY": "test-key",
                    "DEEPSEEK_BASE_URL": "https://example.test/api",
                    "DEEPSEEK_MODEL": "custom-model",
                },
                clear=True,
            ):
                with patch("sys.stdout", io.StringIO()):
                    main(["--db", str(Path(temp) / "agent.db"), "--once", "hello"])

        client_class.assert_called_once_with(
            "test-key", base_url="https://example.test/api", model="custom-model"
        )


if __name__ == "__main__":
    unittest.main()
