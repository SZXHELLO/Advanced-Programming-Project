总共 114 个测试文件，下面是功能说明：

### `tests/` 目录的作用

这是**开发测试套件**，用 `pytest` 框架写的，专门在开发阶段验证代码逻辑正确性，对运行本身没有任何作用。各子目录分别测试对应模块：

| 子目录 | 测试内容 |
|---|---|
| `tests/agent/` | Agent 核心逻辑：ReAct 循环、记忆、Dream、会话、技能加载、自动压缩等（最多，约 28 个文件） |
| `tests/channels/` | 各聊天平台接入：飞书、Telegram、QQ、Slack、Discord、微信、邮件等 |
| `tests/tools/` | 工具层：文件读写编辑、exec 执行、Web 搜索、MCP、沙箱安全等 |
| `tests/providers/` | LLM 提供商适配：Anthropic、OpenAI、Azure、Mistral、Gemini 等 |
| `tests/config/` | 配置加载和迁移（包括昨天刚加的 `.nanobot` → `.minibot` 迁移测试） |
| `tests/cli/` | 命令行命令：onboard、restart、历史记录等 |
| `tests/cron/` | 定时任务服务 |
| `tests/security/` | 网络安全（SSRF 防护等） |
| `tests/utils/` | 工具函数 |
| `tests/command/` | 内置命令（如 dream） |

---

### 对本地部署来说

**可以完全删掉**，不影响 minibot 任何功能。测试目录只在开发时有用：

- 改了源码后跑 `pytest tests/` 验证没有破坏已有功能
- CI 流水线（就是上面说的 `ci.yml`）里自动运行

如果不打算修改 minibot 源码，删掉也完全没问题：

```powershell
Remove-Item -Recurse -Force E:\githubrepository\bot\minibot\tests
```