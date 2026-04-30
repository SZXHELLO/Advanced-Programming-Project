# minibot

![agent_profile](https://github.com/SZXHELLO/Advanced-Programming-Project/raw/main/project2_minibot/minibot/photos/agent_profile.png?raw=true)

## 项目结构

```text
minibot/
├── pyproject.toml
├── README.md
├── bridge/
├── docs/
├── external/
├── minibot/
│   ├── agent/
│   ├── api/
│   ├── bus/
│   ├── channels/
│   ├── cli/
│   ├── command/
│   ├── config/
│   ├── cron/
│   ├── heartbeat/
│   ├── providers/
│   ├── security/
│   ├── session/
│   ├── skills/
│   ├── templates/
│   └── utils/
├── minibot-web/
└── tests/
```

minibot 是一个轻量级的个人 AI 助手框架，提供命令行入口、可编程 Python 门面，以及面向多渠道接入、工具调用、MCP 和定时任务的扩展能力。

它适合这些场景：

- 在本地工作区中和模型对话，直接处理文件、搜索、总结和自动化任务
- 通过命令行快速启动对话式 Agent、Gateway 或 OpenAI 兼容 API 服务
- 在现有 Python 项目中通过 `Minibot` 类集成自动化能力

## 特性

- 命令行交互：支持 `agent`、`onboard`、`serve`、`gateway`、`status` 等命令
- Python SDK：通过 `minibot.Minibot` 以代码方式运行一次 Agent
- 多 Provider：支持 OpenAI 兼容后端，并提供 Anthropic、Azure OpenAI、GitHub Copilot、OpenAI Codex 等实现
- 工具系统：内置文件、搜索、Web、Shell、Notebook、Cron、MCP 等工具扩展点
- 多渠道接入：支持 WhatsApp、Slack、Telegram、飞书、企业微信、Discord、Matrix、Teams 等渠道模块
- 工作区隔离：配置与会话数据默认放在用户目录下的独立 workspace 中
- skill支持
- QQ通讯

## 安装

项目要求 Python 3.11+。

```bash
pip install -e .
```

如果只需要部分能力，也可以安装可选依赖：

```bash
pip install -e .[api]
pip install -e .[wecom]
pip install -e .[weixin]
pip install -e .[msteams]
pip install -e .[matrix]
pip install -e .[discord]
pip install -e .[langsmith]
pip install -e .[pdf]
```

安装完成后，可以直接使用命令行入口：

```bash
minibot --help
python -m minibot --help
```

## 快速开始

先初始化配置和工作区：

```bash
minibot onboard
```

如果你希望通过交互式向导创建配置，可以使用：

```bash
minibot onboard --wizard
```

随后就可以直接对话：

```bash
minibot agent --message "Summarize this repository"
```

默认情况下，配置文件位于 `~/.minibot/config.json`。

## 常用命令

### `minibot onboard`

初始化或刷新配置文件，并可选择指定工作区。

常用参数：

- `--config, -c`: 指定配置文件路径
- `--workspace, -w`: 指定工作区目录
- `--wizard`: 使用交互式向导

### `minibot agent`

直接和 Agent 进行一次消息交互，适合脚本化调用或快速测试。

常用参数：

- `--message, -m`: 发送给 Agent 的消息
- `--session, -s`: 会话 ID，用于隔离上下文
- `--markdown / --no-markdown`: 控制输出是否按 Markdown 渲染
- `--logs / --no-logs`: 是否显示运行日志

### `minibot serve`

启动 OpenAI 兼容 API 服务，默认暴露 `/v1/chat/completions`。

常用参数：

- `--host`: 监听地址
- `--port`: 监听端口
- `--timeout`: 单次请求超时时间
- `--workspace, -w`: 指定工作区目录
- `--config, -c`: 指定配置文件路径

### `minibot gateway`

启动 minibot 网关服务，用于连接消息渠道、定时任务和心跳服务。

### `minibot status`

查看当前配置、工作区和 Provider 状态。

### `minibot provider`

Provider 管理相关命令入口。

## Python API

如果你想在代码中直接调用 minibot，可以使用 `Minibot` 门面：

```python
import asyncio

from minibot import Minibot


async def main() -> None:
    bot = Minibot.from_config()
    result = await bot.run("Summarize this repository")
    print(result.content)


asyncio.run(main())
```

`Minibot.from_config()` 会自动读取配置并构建运行所需的 AgentLoop；如果你传入 `workspace`，会覆盖配置中的工作区路径。

## 配置说明

默认配置文件：`~/.minibot/config.json`

配置支持环境变量替换，例如：

```json
{
    "providers": {
        "openai": {
            "api_key": "${OPENAI_API_KEY}"
        }
    }
}
```

如果配置中的字符串包含 `${VAR_NAME}`，minibot 会在加载时用环境变量值替换它。

## 目录结构

- `minibot/agent/`: Agent 循环、上下文、记忆、子 Agent 与工具注册
- `minibot/api/`: OpenAI 兼容 HTTP API
- `minibot/bus/`: 消息总线与事件队列
- `minibot/channels/`: 各类聊天渠道适配
- `minibot/cli/`: 命令行入口与交互逻辑
- `minibot/config/`: 配置模型、加载和路径管理
- `minibot/providers/`: 不同模型后端实现
- `minibot/skills/`: 内置 Skills 与辅助脚本
- `minibot/templates/`: Agent 使用的提示模板
- `tests/`: 测试套件

## 开发

常见开发命令：

```bash
pytest
ruff check .
```

如果你只想运行某一部分测试，可以按目录执行，例如：

```bash
pytest tests/test_minibot_facade.py
```

## 说明

当前项目仍处于 Alpha 阶段，接口和命令可能会继续调整。更详细的设计说明可以参考 `docs/` 目录中的文档。



## 特色功能展示
### 1.多agents协作
![agents_cooperation1](https://github.com/SZXHELLO/Advanced-Programming-Project/raw/main/project2_minibot/minibot/photos/agents_cooperation1.png?raw=true)

![agents_cooperation1](https://github.com/SZXHELLO/Advanced-Programming-Project/raw/main/project2_minibot/minibot/photos/agents_cooperation2.png?raw=true)

### 2.ReAct过程显示输出
![ReAct_loop](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/ReAct_loop.png?raw=true)

### 3.QQ通讯与PPT制作
![QQconnection&tools_calling](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/QQconnection&tools_calling.png?raw=true)
![file_receiving&PPT_making](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/file_receiving&PPT_making.png?raw=true)

### 4.前端GUI操控
![frontend1](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/frontend1.png?raw=true)
![frontend2](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/frontend2.png?raw=true)
![frontend3](https://github.com/SZXHELLO/Advanced-Programming-Project/blob/main/project2_minibot/minibot/photos/frontend3.png?raw=true)
