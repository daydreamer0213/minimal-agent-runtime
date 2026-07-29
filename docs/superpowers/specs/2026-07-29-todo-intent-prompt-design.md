# 待办意图识别 Prompt 修复设计

日期：2026-07-29

## 1. 问题与证据

用户发送“查询杭州明天天气，并提醒我带雨伞”后，DeepSeek 只调用了
`weather`，然后在最终回答中用文字提醒带伞，没有调用 `todo add`。
下一轮调用 `todo list` 时，工具正确返回空列表。

因此问题不在 SQLite、Session 隔离或 todo 工具，而在“提醒我”存在两种语义：

- 只在当前回答里提醒一句。
- 创建一条可以在后续对话中继续查询和修改的待办。

当前 System Prompt 没有明确区分这两种语义。

## 2. 方案选择

采用“增强 System Prompt + 明确网页快捷示例”：

- System Prompt 明确规定：当用户要求“提醒、记住或之后处理某件事”，并且语义上需要持续保存时，应调用 `todo add`，不能只在回答中口头提醒。
- 网页“天气 + 待办”快捷提示直接写明调用 todo 创建“带雨伞”待办。
- 网页“周报 + 待办”快捷提示直接写明创建“检查周报”待办。
- README 和 `PROMPTS.md` 与实际运行时 Prompt 保持一致。

不在 Runtime 中扫描关键词或强制生成工具调用。LLM 仍根据 System Prompt、
用户语义和工具 Schema 自主决定。

## 3. 文件范围

- 修改 `mini_agent/prompts.py`：增加一条无歧义的待办规则。
- 修改 `mini_agent/web_static/index.html`：把两个英文快捷提示改成明确的中文任务。
- 修改 `PROMPTS.md`：逐字同步新的运行时 Prompt，并解释新增规则。
- 修改 `README.md`：把录屏天气示例改成明确的 todo 请求。
- 修改 `tests/test_docs.py`：检查 Prompt 文档和 README 中的新规则。
- 修改 `tests/test_web.py`：检查网页快捷提示包含明确的中文待办操作。

不修改：

- `AgentRuntime`
- todo 工具实现
- SessionStore
- 数据库 Schema
- LLM API 解析与重试

## 4. 精确行为

System Prompt 新增：

```text
用户要求“提醒我”“记住要做”或之后处理某件事时，如果语义上需要持续保存，应调用 todo 的 add 操作创建待办，不能只在回答中口头提醒。
```

网页天气快捷提示改为：

```text
查询杭州明天天气，并使用 todo 工具添加一条“带雨伞”待办。
```

网页周报快捷提示改为：

```text
根据“本周完成 Agent 工具循环和网页界面”生成简短周报，并使用 todo 工具添加一条“检查周报”待办。
```

## 5. 测试与验收

自动化测试：

1. 先增加测试，确认旧 System Prompt 缺少“不能只在回答中口头提醒”，测试应失败。
2. 确认旧网页快捷提示不包含 `todo 工具` 和具体待办内容，测试应失败。
3. 完成最小修改后运行相关测试。
4. 最后运行全部测试。

真实 API 验收：

1. 清空录屏专用数据库并重启本地服务。
2. 在全新 Session 发送新的天气快捷提示。
3. Trace 中必须出现 `weather` 和 `todo` 的 `add` 调用。
4. 继续发送“列出这个会话当前的待办”。
5. 返回结果中必须包含“带雨伞”。

Prompt 只能降低模型歧义，不能从技术上保证任何真实模型每次都遵循指令。
因此正式录屏仍使用明确写出 `todo 工具` 和 `添加待办` 的示例，并在录制前
通过一次真实 API 冒烟测试验证。

## 6. 数据清理

代码和测试通过后：

1. 停止监听 `127.0.0.1:8010` 的录屏专用服务。
2. 核对目标目录是
   `D:\Guo\vibe\.worktrees\minimal-agent\.agent-data\real-recording`。
3. 只删除该目录中的 `agent.db`、`agent.db-shm` 和 `agent.db-wal`。
4. 保留用户已经录制的 MP4 文件。
5. 重启服务并刷新网页，确认只剩新的空白 Session。
