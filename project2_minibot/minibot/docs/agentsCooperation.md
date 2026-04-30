# minibot 多 Agent 协作：代码与目录说明

本文档整理本仓库中与「主 agent + 子 agent（subagent）」协作相关的实现位置、职责与数据流，便于阅读源码或二次开发。

---

## 1. 能力概览

| 能力 | 说明 |
|------|------|
| **主 agent 委派** | 主循环通过 **`spawn` 工具**创建后台子任务；子任务在独立 `AgentRunner` 中跑完，结果经 **system 入站消息** 回灌主会话。 |
| **用户/运维委派** | **`/addagent <label> \| <duty>`** 仅将子 agent 登记为 **`standby`**（持久化职责文本，**不立即执行**）。**`/runagent`** 或主 agent 的 **`spawn` + `from_persisted_label`** 才开始一轮执行。**`/agents`** 列出职责摘要；**`/deleteagent`** 删除。 |
| **ReAct 模式** | 开启推理 ReAct 时，主循环走文本协议 **`Action: spawn`**（见 `react_loop.py`）；与默认 **function calling** 下的 `spawn` 工具并列存在。 |
| **子 agent 隔离** | 子 agent **无 `message`、无 `spawn`**，避免递归刷屏或无限 spawn；工具集为文件/搜索/Web/`exec`（受配置约束）。 |

---

## 2. 端到端数据流（简图）

```
用户消息 / LLM tool_calls: spawn
        │
        ▼
SpawnTool.execute → SubagentManager.spawn
        │
        ├─ asyncio.create_task(_run_subagent)
        │
        ▼
_run_subagent: ToolRegistry（无 message/spawn）+ subagent_system.md
        │
        ▼
AgentRunner.run → 工具调用… → final_content
        │
        ▼
_announce_result: render subagent_announce.md → InboundMessage(channel=system)
        │
        ▼
AgentLoop._process_message(system 分支) → 主会话继续推理/回复用户
```

持久化 `/addagent` 时，`spawn(..., persist=True)` 会读写工作区下的 JSON 存储；进程重启后由 **`ensure_resumed`** 恢复未完成任务（见下文路径）。

---

## 3. 文件与模块索引

### 3.1 核心逻辑（Python）

| 路径 | 职责 |
|------|------|
| `minibot/agent/subagent.py` | **`SubagentManager`**：`spawn` / `_run_subagent` / 进度发布 / **结果Announce**；**`_SubagentBusHook`** 将子 agent 每步进度打到 bus；子工具注册与 **`merge_subagent_exec_allowed_commands`** 接 `ExecTool`。 |
| `minibot/agent/subagent_persistence.py` | **`SubagentPersistence`**：`{workspace}/.minibot/persistent_subagents.json` 的读写；**`ensure_store_file()`** 保证空库文件存在。 |
| `minibot/agent/tools/spawn.py` | **`SpawnTool`**：参数 `task` / `command` / `label` / `output_file` / **`from_persisted_label`**（按标签或 6–8 位 hex **id** 启动已登记子 agent；可选 `task` 作为本轮协调指令）；**`infer_subagent_responsibilities`**。 |
| `minibot/agent/loop.py` | **`AgentLoop`**：注册 **`SpawnTool`**；**`_LoopHook.before_execute_tools`** 在检测到 **`spawn`** 时将 CLI 进度标记为 **main agent**；**`_set_tool_context`** 为 `spawn` 传入 **`routing_session_key`**（与统一会话锁一致）。 |
| `minibot/agent/react_loop.py` | ReAct 循环内 **`Action: spawn`** 的执行与文案约束；**`detect_subagent_delegation_intent`**、DSML/首轮 `glob|list_dir` 升级 spawn 等委派启发式。 |
| `minibot/command/builtin.py` | **`/addagent`**、**`/agents`**、**`/deleteagent`**；**`/stop`** 内 **`cancel_by_session`**；**`build_help_text`** 中的命令说明。 |
| `minibot/agent/tools/shell.py` | **`ExecTool`**：**`merge_subagent_exec_allowed_commands`**（仅子 agent 使用）；白名单与 **`allow_patterns`** 的交互（白名单模式下不再二次拦截）。 |
| `minibot/utils/react_display.py` | ReAct 进度展示（含 spawn 相关格式化辅助）。 |
| `minibot/utils/helpers.py` | **`build_status_content`** 等：状态文案中含 **Subagents: N running**（与 `/status` 展示一致）。 |

### 3.2 提示词模板（Markdown）

| 路径 | 职责 |
|------|------|
| `minibot/templates/agent/subagent_system.md` | 子 agent **system** 提示：工作区说明、**`persistent_subagents.json`** 路径、优先 **`read_file`**、Windows **`exec`** 说明等。 |
| `minibot/templates/agent/subagent_announce.md` | 子 agent **完成后**注入主会话的 **system** 消息正文模板（`label` / `task` / `result`）。 |

### 3.3 测试（节选）

| 路径 | 内容 |
|------|------|
| `tests/agent/test_subagent_persistence.py` | 持久化 store 的 upsert/remove、**`ensure_store_file`**。 |
| `tests/agent/test_react_parse.py` | ReAct 与 **委派意图**、**spawn**、DSML 升级等。 |
| `tests/tools/test_search_tools.py` | 子 agent 工具注册、**`_build_subagent_prompt`**、disabled skills。 |
| `tests/tools/test_exec_security.py` | **`merge_subagent_exec_allowed_commands`**、白名单与 **Get-Content** / **powershell**。 |
| `tests/agent/test_runner.py` | 含 subagent 与 max_iterations 等边界（见文件内用例名）。 |
| `tests/agent/test_task_cancel.py` | subagent 取消、exec 禁用等。 |

---

## 4. 主 Agent 如何创建子 Agent

1. **Function calling（默认）**  
   - `AgentLoop._register_default_tools` 注册 **`SpawnTool(manager=subagents)`**。  
   - LLM 发起 **`spawn`** 工具调用后，由 `SpawnTool.execute` → **`SubagentManager.spawn`**（默认 **`persist=False`**）。

2. **斜杠命令**  
   - **`/addagent <label> | <duty>`** → **`register_standby`**，状态 **`standby`**，写入 **`persistent_subagents.json`**，不启动后台任务。  
   - **`/runagent <label> [| instruction]`** → **`start_persisted`**，将状态置 **`running`** 并 **`_schedule_subagent_task`**。

3. **ReAct 文本模式**  
   - `run_react_loop` 解析 **`Action: spawn`**，参数在 **`Observation:`** 的 JSON 中；同样进入 **`ToolRegistry.execute("spawn", ...)`**（ReAct 下工具集含 spawn，但 **API 请求 `tools=None`**）。

---

## 5. SubagentManager.spawn 要点

- **任务 ID**：短 id（uuid 截断或持久化 id），用于日志与 **bus metadata**（`_subagent_id`）。  
- **后台执行**：**`asyncio.create_task(_run_subagent(...))`**，不阻塞主会话当前轮（主 agent 可继续对话）。  
- **上下文**：**`origin_channel` / `origin_chat_id` / `session_key`（routing）** 用于进度与结果路由；与 **`SpawnTool.set_context`**、**`AgentLoop._routing_session_key`** 对齐。  
- **持久化**：`persist=True` 时通过 **`SubagentPersistence.upsert`** 记录状态；**`ensure_resumed`** 在启动时恢复 **running / interrupted** 记录。

---

## 6. 子 Agent 运行时配置

- **模型**：与构造 **`SubagentManager` 时传入的 `model`** 一致（通常与主 agent 默认模型相同，见 `loop.py` / CLI 初始化）。  
- **迭代上限**：**`max_iterations=15`**（`_run_subagent` 内 **`AgentRunSpec`**）。  
- **失败策略**：**`fail_on_tool_error=True`**，单次工具错误可能导致子 run 以 **`stop_reason=tool_error`** 结束并Announce失败摘要。  
- **工具列表**（`subagent.py` `_run_subagent`）：  
  `read_file`, `write_file`, `edit_file`, `delete_file`, `list_dir`, `glob`, `grep`，可选 **`exec`**、**`web_search`**、**`web_fetch`**。  
  **不包含** **`message`**、**`spawn`**。  
- **Exec 白名单**：若配置 **`tools.exec.allowedCommands`**，子 agent 侧使用 **`merge_subagent_exec_allowed_commands`** 合并只读类 Windows/shell 命令，减少误拦（见 `shell.py`）。

---

## 7. 持久化与磁盘路径

| 项目 | 路径 |
|------|------|
| 持久化子 agent 记录 | **`{workspace}/.minibot/persistent_subagents.json`** |
| 实现 | **`SubagentPersistence.default_store_path`**；**`SubagentManager` 构造时 `ensure_store_file()`** |

格式为带 **`version`** 与 **`records`** 数组的 JSON；**不存在** 名为 **`subagent_tasks.json`** 的官方文件（若模型臆造，以 `subagent_system.md` 为准）。

---

## 8. 结果如何回到主会话

1. **`_announce_result`** 使用 **`render_template("agent/subagent_announce.md", ...)`** 生成 Markdown。  
2. 构造 **`InboundMessage(channel="system", sender_id="subagent", chat_id="channel:chat_id", session_key_override=routing_session_key)`** 发布到 bus。  
3. **`AgentLoop._process_message`** 对 **`channel == "system"`** 的分支拉历史、跑 **`_run_agent_loop`**，生成对用户可见的后续回复。

---

## 9. CLI / 会话相关

- **交互式 CLI**（`minibot/cli/commands.py`）：消费 outbound 上带 **`_agent_role`** / **`_subagent_label`** 的进度，用于「main agent / subagent」分行展示（与 **`_LoopHook`**、**`_SubagentBusHook`** 配合）。  
- **`/stop`**：取消本会话 **`AgentLoop._active_tasks`** 中的任务，并 **`subagents.cancel_by_session`**。

---

## 10. 扩展或修改时建议入口

- 调整子 agent **人设/约束**：改 **`subagent_system.md`**。  
- 调整 **回主会话的语气与字段**：改 **`subagent_announce.md`** 与 **`_announce_result`**。  
- 为子 agent **增加工具**：在 **`_run_subagent`** 内 `tools.register(...)`（注意沙箱与安全）。  
- 调整 **委派话术/ReAct**：**`react_loop.py`** 中 **`build_react_appendix`**、**`detect_subagent_delegation_intent`**、spawn 后 Observation 提示等。

---

*文档随源码演进可能过时；以 `minibot/agent/subagent.py` 与 `minibot/agent/loop.py` 为准。*
