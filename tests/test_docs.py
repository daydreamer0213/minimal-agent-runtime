import os
from pathlib import Path
import subprocess
import unittest


class DocumentationTests(unittest.TestCase):
    def test_dotenv_template_and_automatic_loading_are_documented(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        example = Path(".env.example").read_text(encoding="utf-8")

        self.assertIn("自动读取项目根目录中的 `.env`", readme)
        self.assertEqual(
            example.splitlines(),
            [
                "DEEPSEEK_API_KEY=",
                "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                "DEEPSEEK_MODEL=deepseek-v4-pro",
            ],
        )

    def test_required_submission_documents_exist_and_cover_memory(self):
        required = [
            "README.md",
            "PROMPTS.md",
            "PROBLEM_SOLVING.md",
            ".env.example",
            "demo.ps1",
        ]
        for name in required:
            self.assertTrue(Path(name).is_file(), name)

        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("memory 什么时候召回", readme)
        self.assertIn("memory 放在 context 的什么位置", readme)
        self.assertIn("deepseek-v4-pro", readme)
        self.assertIn("python -m unittest discover", readme)

    def test_demo_script_only_checks_for_a_key_and_lists_recording_steps(self):
        environment = os.environ.copy()
        environment.pop("DEEPSEEK_API_KEY", None)
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "demo.ps1",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("未检测到 DEEPSEEK_API_KEY", result.stdout)
        script = Path("demo.ps1").read_text(encoding="utf-8")
        self.assertNotIn("$env:DEEPSEEK_API_KEY", script)
        self.assertNotIn("GetEnvironmentVariable", script)


if __name__ == "__main__":
    unittest.main()
