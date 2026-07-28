import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DocumentationTests(unittest.TestCase):
    def test_problem_solving_records_web_error_recovery_in_utf8(self):
        document = Path("PROBLEM_SOLVING.md").read_text(encoding="utf-8")
        old_title = "## 8. GBK 窗口不能直接输出含 emoji 的中文周报"
        new_title = "## 任务 7 错误态修复记录"

        self.assertIn(old_title, document)
        self.assertIn(new_title, document)
        self.assertIn(f"\n\n{new_title}", document)
        self.assertNotIn("寮€", document)
        self.assertNotIn("鍒濆", document)

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

    def test_readme_and_demo_cover_the_local_web_ui(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        demo = Path("demo.ps1").read_text(encoding="utf-8")

        for text in [
            "python -m mini_agent.web",
            "http://127.0.0.1:8000",
            "本机服务",
            "API Key 不会进入浏览器",
            "weather-chat",
            "weekly-report",
            "仍可通过 `python -m mini_agent` 使用 CLI",
        ]:
            self.assertIn(text, readme)
        for text in [
            "python -m mini_agent.web",
            "天气和待办",
            "周报和待办",
            "Agent Trace",
            "python -m unittest discover -s tests -v",
        ]:
            self.assertIn(text, demo)

    def test_web_recording_guide_keeps_sessions_and_fallback_unambiguous(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        demo = Path("demo.ps1").read_text(encoding="utf-8")

        self.assertIn(
            "& 'D:\\DevData\\conda-envs\\asset-intel\\python.exe' -m mini_agent.web",
            readme,
        )
        self.assertIn("不同 session 的 Agent Trace 和待办不会串在一起", readme)
        self.assertIn("两个 session 的 Agent Trace 与待办互不串线", demo)
        self.assertNotIn("首版不包含网页界面", readme)
        self.assertIn("运行 demo.ps1 后，请按“网页演示”章节", readme)
        self.assertNotIn("按“两个终端验证 session 隔离”的命令操作", readme)

    def test_demo_script_accepts_environment_or_local_dotenv_without_exposing_key(self):
        environment = os.environ.copy()
        environment.pop("DEEPSEEK_API_KEY", None)
        script = Path("demo.ps1").read_text(encoding="utf-8")
        self.assertEqual(Path("demo.ps1").read_bytes()[:3], b"\xef\xbb\xbf")
        self.assertNotIn("$env:DEEPSEEK_API_KEY", script)
        self.assertNotIn("GetEnvironmentVariable", script)
        for guide_text in [
            "未检测到 DEEPSEEK_API_KEY",
            "准备就绪",
            "天气和待办",
            "周报和待办",
            "分别切回两个 session 继续追问",
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
                b"python -m mini_agent.web",
                b"Agent Trace",
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
