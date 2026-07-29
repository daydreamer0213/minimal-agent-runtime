# Minimal Agent Real Web Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `artifacts/demo.mp4` with a continuous recording of real Edge interactions against the existing Minimal Agent web UI and real DeepSeek API.

**Architecture:** Run the existing web server with a dedicated SQLite database, open it in a fixed-size Edge app window, and capture only that window at 30 FPS while Windows UI automation performs the approved walkthrough. Keep raw footage and diagnostic images under ignored `.agent-data/`; only the verified final MP4 is committed.

**Tech Stack:** Python 3.11, existing `mini_agent.web`, Microsoft Edge, Windows UI Automation, FFmpeg 7.1 `gdigrab`, H.264/libx264.

## Global Constraints

- Use real `AgentRuntime` and the existing `.env` configuration; never display or read the API key into output.
- Record at 30 FPS and deliver H.264 MP4, 1280×720, `yuv420p`.
- Preserve visible mouse movement, clicking, typing, sending, waiting, and page state changes.
- Use a dedicated recording database under `.agent-data/`.
- The final video must be 60–100 seconds and contain no Codex, terminal, desktop, `.env`, or API key content.
- Do not modify the Agent Runtime, tools, session behavior, or web implementation.

---

### Task 1: Calibrate Window-Only Continuous Capture

**Files:**
- Create temporarily: `.agent-data/real-recording/probe.mp4`
- Create temporarily: `.agent-data/real-recording/probe-contact-sheet.png`

**Interfaces:**
- Consumes: existing web server command and FFmpeg executable.
- Produces: confirmed Edge window title, capture geometry, readable 1280×720 test footage, and visible mouse movement.

- [ ] **Step 1: Start the existing web server with a recording-only database**

Run:

```powershell
$python = 'D:\DevData\conda-envs\asset-intel\python.exe'
$db = 'D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording\agent.db'
Start-Process -FilePath $python -ArgumentList '-m','mini_agent.web','--db',$db,'--host','127.0.0.1','--port','8010','--no-browser' -WorkingDirectory 'D:\Guo\vibe\.worktrees\minimal-agent' -WindowStyle Hidden
```

Expected: `http://127.0.0.1:8010/` returns the Minimal Agent page.

- [ ] **Step 2: Open a fixed Edge app window**

Run:

```powershell
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
Start-Process -FilePath $edge -ArgumentList '--app=http://127.0.0.1:8010/','--window-size=1280,800','--window-position=80,80'
```

Expected: a separate Edge window titled `Minimal Agent 实验台` opens without browser tabs or an address bar.

- [ ] **Step 3: Record an eight-second capture probe**

Run FFmpeg against the exact Edge window title:

```powershell
$ffmpeg = 'D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\video-tools\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe'
& $ffmpeg -y -f gdigrab -framerate 30 -draw_mouse 1 -i 'title=Minimal Agent 实验台' -t 8 -vf 'scale=1280:720' -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p '.agent-data\real-recording\probe.mp4'
```

During these eight seconds, move the mouse over the Session list and click an existing Session.

Expected: FFmpeg exits with code 0 and the file contains approximately 240 frames.

- [ ] **Step 4: Verify the probe before any DeepSeek call**

Run:

```powershell
& $ffmpeg -v error -i '.agent-data\real-recording\probe.mp4' -f null NUL
& $ffmpeg -y -v error -i '.agent-data\real-recording\probe.mp4' -vf 'fps=1/2,scale=426:240,tile=2x2' -frames:v 1 '.agent-data\real-recording\probe-contact-sheet.png'
```

Expected: full decode succeeds; contact sheet shows only the Edge app window, readable text, and changed mouse positions.

### Task 2: Record the Approved Real-API Walkthrough

**Files:**
- Create temporarily: `.agent-data/real-recording/raw.mp4`
- Create temporarily: `.agent-data/real-recording/agent.db`
- Create temporarily: `.agent-data/real-recording/server.stdout.log`
- Create temporarily: `.agent-data/real-recording/server.stderr.log`

**Interfaces:**
- Consumes: calibrated Edge window, running web server, real DeepSeek configuration, and the walkthrough from `docs/superpowers/specs/2026-07-29-real-web-recording-design.md`.
- Produces: one uninterrupted raw recording containing all required interactions.

- [ ] **Step 1: Reset only the dedicated recording database**

Stop the recording server, verify that the resolved target is exactly:

```text
D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording\agent.db
```

Delete only that database and its `-shm`/`-wal` companions, then restart the server from Task 1.

Expected: the page opens with one clean initial Session and no previous recording data.

- [ ] **Step 2: Start raw window capture**

Run:

```powershell
& $ffmpeg -y -f gdigrab -framerate 30 -draw_mouse 1 -i 'title=Minimal Agent 实验台' -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p '.agent-data\real-recording\raw.mp4'
```

Expected: FFmpeg continues running while the following UI steps execute.

- [ ] **Step 3: Record the weather Session**

Perform these visible actions with normal cursor movement and typing:

1. Type `weather-demo` into “新建会话标题” and click “创建”.
2. Type `查询杭州明天天气，并提醒我带雨伞` into the chat input.
3. Click “发送” and wait until the answer, Trace, and todo appear.
4. Type `列出这个会话当前的待办` and click “发送”.
5. Wait until the follow-up answer and new Trace appear.

Expected: the page shows a weather tool call, a todo creation/list call, a final answer, and the “带雨伞” todo in `weather-demo`.

- [ ] **Step 4: Record the weekly-report Session**

Perform:

1. Type `weekly-demo` into “新建会话标题” and click “创建”.
2. Type `帮我写一份简短周报：本周完成了 Agent 工具循环和网页界面，并添加“检查周报”待办` into the chat input.
3. Click “发送” and wait until the answer, Trace, and todo appear.
4. Click `weather-demo`, pause for two seconds, then click `weekly-demo`.
5. Scroll the Trace panel enough to show the tool call and result.

Expected: `weekly-demo` shows only “检查周报”; switching to `weather-demo` restores its own weather chat and “带雨伞” todo.

- [ ] **Step 5: Stop capture and inspect API results**

Stop FFmpeg gracefully by sending `q`; do not terminate it while it is writing the MP4 index.

Expected: `raw.mp4` closes normally, and server logs contain successful `/api/chat` responses without exposing `.env` or the API key.

### Task 3: Trim, Normalize, Verify, and Commit the Final Video

**Files:**
- Modify: `artifacts/demo.mp4`
- Create temporarily: `.agent-data/real-recording/final-contact-sheet.png`

**Interfaces:**
- Consumes: `.agent-data/real-recording/raw.mp4`.
- Produces: verified submission artifact `artifacts/demo.mp4`.

- [ ] **Step 1: Determine the exact useful start and end timestamps**

Review the raw video and note:

- Start: two seconds before the first visible mouse action.
- End: two seconds after the final `weekly-demo` Trace/todo view.
- Remove only unrelated leading and trailing frames; keep the real in-page waiting state.

Enter the measured values into PowerShell:

```powershell
$startSeconds = [double](Read-Host '输入第一个有效画面的秒数')
$endSeconds = [double](Read-Host '输入最后一个有效画面的秒数')
$durationSeconds = $endSeconds - $startSeconds
if ($durationSeconds -lt 60 -or $durationSeconds -gt 100) {
    throw "有效内容必须在 60 到 100 秒之间，当前为 $durationSeconds 秒。"
}
```

Expected: `$durationSeconds` is 60–100 and the selected interval preserves every required click and input.

- [ ] **Step 2: Encode the final artifact**

```powershell
& $ffmpeg -y -ss $startSeconds -i '.agent-data\real-recording\raw.mp4' -t $durationSeconds -vf 'scale=1280:720:flags=lanczos,fps=30' -an -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -movflags +faststart 'artifacts\demo.mp4'
```

Expected: FFmpeg exits with code 0 and replaces the screenshot-based artifact.

- [ ] **Step 3: Verify the complete video and inspect representative frames**

Run:

```powershell
& $ffmpeg -v error -i 'artifacts\demo.mp4' -f null NUL
& $ffmpeg -y -v error -i 'artifacts\demo.mp4' -vf 'fps=1/15,scale=426:240,tile=3x2' -frames:v 1 '.agent-data\real-recording\final-contact-sheet.png'
```

Confirm:

- 1280×720, 30 FPS, H.264, `yuv420p`.
- 60–100 seconds.
- Mouse, typing, Session switching, weather/todo tools, follow-up, Trace, and isolated todos are visible.
- No terminal, Codex, desktop, `.env`, or API key appears.

Expected: decode exits with code 0 and every contact-sheet frame is relevant and readable.

- [ ] **Step 4: Commit only the verified final artifact**

Run:

```powershell
git status --short
git add -- artifacts/demo.mp4
git commit -m "docs: replace demo with real web recording"
```

Expected: `.agent-data/` remains ignored, only `artifacts/demo.mp4` is committed, and `git status --short` is empty.
