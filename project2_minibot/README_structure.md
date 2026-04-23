# minibot 项目结构速览

本文档面向人类与 AI 助手：快速定位源码包、测试与 **Agent 工具（`function calling`）** 相关文件。  
生成时**刻意省略**本地/环境目录：`.venv/`、`__pycache__/`、`.pytest_cache/`、`.git/` 等。

## 仓库根目录（`minibot/`）

```
minibot/
├── README.md                 # 主文档
├── README_structure.md       # 本文件：目录树与工具子树说明
├── pyproject.toml            # 打包与依赖
├── entrypoint.sh             # 容器/入口脚本（若使用）
├── bridge/                   # Node/TS：WhatsApp 等桥接服务
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts
│       ├── server.ts
│       ├── types.d.ts
│       └── whatsapp.ts
├── minibot/                  # Python 包（与项目同名）
│   ├── __init__.py
│   ├── minibot.py            # 门面 / 主入口
│   ├── agent/                # Agent 循环、Runner、记忆、子 Agent
│   ├── api/                  # HTTP API
│   ├── bus/                  # 事件/消息总线
│   ├── channels/             # 各聊天渠道（飞书、Slack、Telegram…）
│   ├── cli/                  # 命令行
│   ├── command/              # 斜杠命令路由
│   ├── config/               # 配置模型与加载
│   ├── cron/                 # 定时任务服务
│   ├── heartbeat/            # 心跳服务
│   ├── providers/            # 大模型 Provider（OpenAI/Anthropic/…）
│   ├── security/             # 网络安全等
│   ├── session/              # 会话管理
│   ├── skills/               # 内置 Skill 说明与脚本
│   ├── templates/            # 系统/用户提示模板（Markdown）
│   └── utils/                # 通用工具函数
└── tests/                    # pytest 测试（按领域分子目录）
    ├── agent/
    ├── channels/
    ├── cli/
    ├── command/
    ├── config/
    ├── cron/
    ├── providers/
    ├── security/
    ├── tools/
    ├── utils/
    └── test_*.py             # 根级若干集成/杂项测试
```

### `minibot/` 包内主要子目录（展开一层）

```
minibot/minibot/
├── __init__.py
├── minibot.py
├── agent/
│   ├── __init__.py
│   ├── autocompact.py
│   ├── context.py
│   ├── hook.py
│   ├── loop.py              # AgentLoop：注册默认工具、插件、MCP
│   ├── memory.py
│   ├── runner.py            # 与模型交互、工具调用规格
│   ├── skills.py
│   ├── subagent.py
│   └── tools/               # ← 见下文「工具目录专章」
├── api/
│   ├── __init__.py
│   └── server.py
├── bus/
│   ├── __init__.py
│   ├── events.py
│   └── queue.py
├── channels/
│   ├── __init__.py
│   ├── base.py
│   ├── dingtalk.py
│   ├── discord.py
│   ├── email.py
│   ├── feishu.py
│   ├── manager.py
│   ├── matrix.py
│   ├── mochat.py
│   ├── msteams.py
│   ├── qq.py
│   ├── registry.py
│   ├── slack.py
│   ├── telegram.py
│   ├── websocket.py
│   ├── wecom.py
│   ├── weixin.py
│   └── whatsapp.py
├── cli/
│   ├── __init__.py
│   ├── commands.py
│   ├── models.py
│   ├── onboard.py
│   └── stream.py
├── command/
│   ├── __init__.py
│   ├── builtin.py
│   └── router.py
├── config/
│   ├── __init__.py
│   ├── loader.py
│   ├── paths.py
│   └── schema.py            # 含 tools.tool_plugins 等
├── cron/
│   ├── __init__.py
│   ├── service.py
│   └── types.py
├── heartbeat/
│   ├── __init__.py
│   └── service.py
├── providers/
│   ├── __init__.py
│   ├── anthropic_provider.py
│   ├── azure_openai_provider.py
│   ├── base.py
│   ├── github_copilot_provider.py
│   ├── openai_codex_provider.py
│   ├── openai_compat_provider.py
│   ├── registry.py
│   ├── transcription.py
│   └── openai_responses/
│       ├── __init__.py
│       ├── converters.py
│       └── parsing.py
├── security/
│   ├── __init__.py
│   └── network.py
├── session/
│   ├── __init__.py
│   └── manager.py
├── skills/
│   ├── README.md
│   ├── clawhub/SKILL.md
│   ├── cron/SKILL.md
│   ├── github/SKILL.md
│   ├── memory/SKILL.md
│   ├── skill-creator/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── init_skill.py
│   │       ├── package_skill.py
│   │       └── quick_validate.py
│   ├── summarize/SKILL.md
│   ├── tmux/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── find-sessions.sh
│   │       └── wait-for-text.sh
│   └── weather/SKILL.md
├── templates/
│   ├── AGENTS.md
│   ├── HEARTBEAT.md
│   ├── SOUL.md
│   ├── TOOLS.md
│   ├── USER.md
│   ├── agent/
│   │   ├── _snippets/untrusted_content.md
│   │   ├── consolidator_archive.md
│   │   ├── dream_phase1.md
│   │   ├── dream_phase2.md
│   │   ├── evaluator.md
│   │   ├── identity.md
│   │   ├── max_iterations_message.md
│   │   ├── platform_policy.md
│   │   ├── skills_section.md
│   │   ├── subagent_announce.md
│   │   └── subagent_system.md
│   └── memory/
│       └── MEMORY.md
└── utils/
    ├── __init__.py
    ├── document.py
    ├── evaluator.py
    ├── gitstore.py
    ├── helpers.py
    ├── path.py
    ├── prompt_templates.py
    ├── restart.py
    ├── runtime.py
    ├── searchusage.py
    └── tool_hints.py
```

---

## `minibot/agent/tools/` 目录树（工具 / Function Calling）

**注册入口**：默认工具在 `minibot/agent/loop.py` 的 `_register_default_tools` 中 `ToolRegistry.register(...)`；  
可选插件模块在配置 `tools.tool_plugins` 中列出，由 `plugin_loader.load_tool_plugins` 加载（`@tool_plugin` 或 `register_tools` 回调）。

```
minibot/minibot/agent/tools/
├── __init__.py           # 包初始化 / 导出（若有）
├── base.py               # Tool 抽象基类、JSON Schema 校验、to_schema()、@tool_parameters
├── registry.py           # ToolRegistry：注册、get_definitions、prepare_call、execute
├── plugin_loader.py      # @tool_plugin、按模块路径动态加载并 register
├── schema.py             # 参数 Schema 片段（与 base.Schema 配合）
├── file_state.py         # 编辑/文件状态辅助（供文件类工具使用）
├── filesystem.py         # 读/写/编辑/列目录/搜索文件等本地文件工具
├── shell.py              # 本地命令执行（Exec）相关
├── sandbox.py            # 执行沙箱/路径约束等
├── search.py             # 代码库内搜索类工具（如 grep/glob 等，与 filesystem 配合场景）
├── web.py                # 联网搜索、抓取等
├── message.py            # 向渠道发消息等
├── spawn.py              # 派生子 Agent
├── mcp.py                # MCP 服务对接，动态工具名通常带 mcp_ 前缀
├── notebook.py           # Notebook 编辑
└── cron.py               # 定时任务工具
```

### 给 AI 的简短操作提示

| 目标 | 优先查看 |
|------|----------|
| 新增内置工具 | `base.py`（继承 `Tool`）、`loop.py`（`_register_default_tools`） |
| 插件化工具（不改核心） | `plugin_loader.py`、`config/schema.py` 中 `tool_plugins` |
| 工具列表如何交给模型 | `registry.py`（`get_definitions`）、`agent/runner.py` |
| 文件路径安全/工作区 | `filesystem.py` 中解析与 `allowed_dir` 逻辑 |

`tests/` 下文件较多，主树用分目录与根级 `test_*.py` 概括；需要完整测试树时可再扩展本文件或脚本生成。
