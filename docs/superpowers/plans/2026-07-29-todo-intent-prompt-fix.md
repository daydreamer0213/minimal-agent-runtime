# Todo Intent Prompt Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make persistent reminder requests clearly select `todo add` while preserving LLM-driven tool choice, then reset the recording-only database for a clean take.

**Architecture:** Add one narrow rule to the existing System Prompt and replace the two web quick prompts with explicit Chinese tool requests. Keep runtime, tool execution, schemas, storage, and session behavior unchanged; protect the change with documentation and static-page tests.

**Tech Stack:** Python 3.11 standard library, `unittest`, HTML, SQLite, existing DeepSeek-compatible client.

## Global Constraints

- Do not add keyword scanning or forced tool calls to `AgentRuntime`.
- Do not modify todo, SessionStore, database Schema, or LLM parsing.
- Keep `PROMPTS.md` synchronized word-for-word with `AGENT_SYSTEM_PROMPT`.
- Use no new dependencies.
- Never read, print, or commit `.env` or the API key.
- Delete only the dedicated recording database files explicitly listed in Task 3.

---

### Task 1: Add Failing Prompt and Web Example Tests

**Files:**
- Modify: `tests/test_docs.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `mini_agent.prompts.AGENT_SYSTEM_PROMPT`, `PROMPTS.md`, `README.md`, and the HTML served by the existing test web server.
- Produces: two regression checks that fail against the current ambiguous prompt and English quick examples.

- [ ] **Step 1: Add the System Prompt and documentation regression test**

Add this import to `tests/test_docs.py`:

```python
from mini_agent.prompts import AGENT_SYSTEM_PROMPT
```

Add this method to `DocumentationTests`:

```python
def test_persistent_reminders_are_explicit_todo_intent(self):
    prompt_document = Path("PROMPTS.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    rule = (
        "用户要求“提醒我”“记住要做”或之后处理某件事时，"
        "如果语义上需要持续保存，应调用 todo 的 add 操作创建待办，"
        "不能只在回答中口头提醒。"
    )

    self.assertIn(rule, AGENT_SYSTEM_PROMPT)
    self.assertIn(rule, prompt_document)
    self.assertIn("使用 todo 工具添加一条“带雨伞”待办", readme)
```

- [ ] **Step 2: Tighten the web quick-prompt assertions**

Replace the two old English-prefix assertions in
`WebTests.test_static_files_are_whitelisted_and_reject_queries` with:

```python
self.assertIn(
    'data-prompt="查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。"',
    html,
)
self.assertIn(
    'data-prompt="根据“本周完成 Agent 工具循环和网页界面”生成简短周报，'
    '并使用 todo 工具添加一条“检查周报”待办。"',
    html,
)
```

- [ ] **Step 3: Run the two tests and verify RED**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest `
  tests.test_docs.DocumentationTests.test_persistent_reminders_are_explicit_todo_intent `
  tests.test_web.WebTests.test_static_files_are_whitelisted_and_reject_queries -v
```

Expected: both tests fail because the new rule and Chinese quick prompts do not exist yet.

### Task 2: Apply the Minimal Prompt and Documentation Fix

**Files:**
- Modify: `mini_agent/prompts.py`
- Modify: `mini_agent/web_static/index.html`
- Modify: `PROMPTS.md`
- Modify: `README.md`
- Test: `tests/test_docs.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: the failing assertions from Task 1.
- Produces: a clearer System Prompt and deterministic recording examples without changing runtime control flow.

- [ ] **Step 1: Add the persistent-reminder rule to the runtime Prompt**

Insert this line after the existing “天气或待办操作” rule in
`mini_agent/prompts.py`:

```python
用户要求“提醒我”“记住要做”或之后处理某件事时，如果语义上需要持续保存，应调用 todo 的 add 操作创建待办，不能只在回答中口头提醒。
```

- [ ] **Step 2: Replace both web quick prompts**

Set the two `data-prompt` attributes in
`mini_agent/web_static/index.html` to:

```html
<button type="button" data-prompt="查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。">查询天气 + 待办</button>
<button type="button" data-prompt="根据“本周完成 Agent 工具循环和网页界面”生成简短周报，并使用 todo 工具添加一条“检查周报”待办。">周报 + 待办</button>
```

- [ ] **Step 3: Synchronize `PROMPTS.md`**

Add the exact new rule to the documented `AGENT_SYSTEM_PROMPT` block and
change the explanation to state that persistent reminders require `todo add`
rather than a text-only reminder.

- [ ] **Step 4: Update both README weather examples**

Use this exact request in the web recording and two-terminal examples:

```text
查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest `
  tests.test_docs.DocumentationTests.test_persistent_reminders_are_explicit_todo_intent `
  tests.test_web.WebTests.test_static_files_are_whitelisted_and_reject_queries -v
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 6: Run the full offline test suite**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m unittest discover -s tests -v
```

Expected: all tests pass and no real network request is made.

- [ ] **Step 7: Commit the tested fix**

Run:

```powershell
git add -- mini_agent/prompts.py mini_agent/web_static/index.html PROMPTS.md README.md tests/test_docs.py tests/test_web.py
git commit -m "fix: clarify persistent todo intent"
```

Expected: one focused commit containing only the prompt, examples, docs, and tests.

### Task 3: Smoke-Test and Reset the Recording Environment

**Files:**
- Create temporarily: `.agent-data/todo-prompt-smoke.db`
- Delete after verification: `.agent-data/todo-prompt-smoke.db`
- Delete: `.agent-data/real-recording/agent.db`
- Delete if present: `.agent-data/real-recording/agent.db-shm`
- Delete if present: `.agent-data/real-recording/agent.db-wal`

**Interfaces:**
- Consumes: the tested prompt fix and existing real DeepSeek configuration.
- Produces: evidence that the real model calls both tools and a clean recording service at `http://127.0.0.1:8010/`.

- [ ] **Step 1: Run one real-API smoke prompt in a separate database**

Run:

```powershell
& 'D:\DevData\conda-envs\asset-intel\python.exe' -m mini_agent `
  --db '.agent-data\todo-prompt-smoke.db' `
  --session 'todo-smoke' `
  --once '查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。'
```

Expected: the command returns a final answer without displaying the API key.

- [ ] **Step 2: Verify smoke-test tool records read-only**

Query `.agent-data/todo-prompt-smoke.db` and confirm:

- Trace contains one `weather` call.
- Trace contains `todo` with `{"action":"add","text":"带雨伞"}`.
- The `todos` table contains exactly one `todo-smoke` item named “带雨伞”.

Expected: all three conditions are true.

- [ ] **Step 3: Stop only the recording server**

Resolve the process that owns local port `8010`, confirm its executable is
`D:\DevData\conda-envs\asset-intel\python.exe`, then stop that exact PID.

Expected: no process listens on `127.0.0.1:8010`.

- [ ] **Step 4: Delete only verified temporary database targets**

Resolve and verify that every target is inside:

```text
D:\Guo\vibe\.worktrees\minimal-agent\.agent-data
```

Delete:

```text
.agent-data\todo-prompt-smoke.db
.agent-data\todo-prompt-smoke.db-shm
.agent-data\todo-prompt-smoke.db-wal
.agent-data\real-recording\agent.db
.agent-data\real-recording\agent.db-shm
.agent-data\real-recording\agent.db-wal
```

Do not delete any MP4, PNG, or log files.

Expected: the six database paths do not exist.

- [ ] **Step 5: Restart and verify the clean recording server**

Start:

```powershell
Start-Process -FilePath 'D:\DevData\conda-envs\asset-intel\python.exe' `
  -ArgumentList '-m','mini_agent.web','--db','D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording\agent.db','--host','127.0.0.1','--port','8010','--no-browser' `
  -WorkingDirectory 'D:\Guo\vibe\.worktrees\minimal-agent' `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording\server.stdout.log' `
  -RedirectStandardError 'D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording\server.stderr.log'
```

Open `http://127.0.0.1:8010/` and confirm HTTP 200. Query the new database and
confirm it contains no previous `weather-demo` or `weekly-demo` Session.

Expected: the user can refresh the page and begin a clean recording.
