import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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

    def test_demo_script_accepts_environment_or_local_dotenv_without_exposing_key(self):
        environment = os.environ.copy()
        environment.pop("DEEPSEEK_API_KEY", None)
        script = Path("demo.ps1").read_text(encoding="utf-8")
        self.assertNotIn("$env:DEEPSEEK_API_KEY", script)
        self.assertNotIn("GetEnvironmentVariable", script)
        for guide_text in [
            "未检测到 DEEPSEEK_API_KEY",
            "准备就绪",
            "查询杭州明天天气；如有雨，添加待办带雨伞",
            "生成一份本周工作周报，并添加待办检查周报",
            "重新打开 weather-chat",
            "重新打开 weekly-report",
        ]:
            self.assertIn(guide_text, script)

        with tempfile.TemporaryDirectory() as directory:
            temporary_directory = Path(directory)
            script_path = temporary_directory / "demo.ps1"
            shutil.copyfile("demo.ps1", script_path)

            missing_result = self.run_demo(script_path, environment)
            self.assertEqual(missing_result.returncode, 1)
            self.assertNotIn(b"configured", missing_result.stdout)

            dotenv_path = temporary_directory / ".env"
            dotenv_path.write_text("DEEPSEEK_API_KEY=configured\n", encoding="utf-8")
            dotenv_result = self.run_demo(script_path, environment)
            self.assertEqual(dotenv_result.returncode, 0)
            self.assertNotIn(b"configured", dotenv_result.stdout)
            for command_fragment in [
                b"python -m mini_agent --session weather-chat",
                b"python -m mini_agent --session weekly-report",
                b"/trace",
                b"python -m unittest discover -s tests -v",
            ]:
                self.assertIn(command_fragment, dotenv_result.stdout)

            dotenv_path.unlink()
            environment_result = self.run_demo(
                script_path,
                {**environment, "DEEPSEEK_API_KEY": "configured"},
            )
            self.assertEqual(environment_result.returncode, 0)
            self.assertNotIn(b"configured", environment_result.stdout)

    @staticmethod
    def run_demo(script_path, environment):
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            check=False,
            env=environment,
            cwd=script_path.parent,
        )


if __name__ == "__main__":
    unittest.main()
