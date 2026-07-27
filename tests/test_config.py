import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.config import build_client, load_dotenv, safe_text


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_keeps_process_values_and_uses_last_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            path.write_text(
                "\ufeffDEEPSEEK_API_KEY=dotenv-key\n"
                "DEEPSEEK_BASE_URL=https://first.example\n"
                "DEEPSEEK_BASE_URL=https://last.example\n"
                "DEEPSEEK_MODEL=dotenv-model\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"DEEPSEEK_MODEL": "process-model"},
                clear=True,
            ):
                load_dotenv(path)
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "dotenv-key")
                self.assertEqual(
                    os.environ["DEEPSEEK_BASE_URL"],
                    "https://last.example",
                )
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "process-model")

    @patch("mini_agent.config.DeepSeekClient")
    def test_build_client_uses_supported_environment_overrides(self, client_class):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "https://example.test/api",
                "DEEPSEEK_MODEL": "custom-model",
            },
            clear=True,
        ):
            build_client("test-key")

        client_class.assert_called_once_with(
            "test-key",
            base_url="https://example.test/api",
            model="custom-model",
        )

    def test_safe_text_redacts_the_exact_key(self):
        self.assertEqual(
            safe_text("request failed for secret-key", "secret-key"),
            "request failed for [redacted]",
        )


if __name__ == "__main__":
    unittest.main()
