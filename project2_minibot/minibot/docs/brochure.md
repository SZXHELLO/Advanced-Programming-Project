# Minibot 功能实现说明书
---

## 第一部分：核心框架要求

### 1. 大模型接入与对话能力

#### 需求

- 支持接入至少一种大模型 API（如 OpenAI GPT / Claude / DeepSeek 等）
- 能够接收用户输入的自然语言指令
- 能够与大模型进行多轮对话（维护对话历史上下文）

#### 实现方式

**多提供商抽象层**

- **路径**：`minibot/providers/` 目录
- **核心设计**：实现统一的 `LLMProvider` 基类，所有具体提供商继承该基类
- **支持的提供商**：
  - `openai_provider.py` - OpenAI GPT 系列
  - `anthropic_provider.py` - Claude 系列
  - `azure_openai_provider.py` - Azure OpenAI
  - `openai_compat_provider.py` - 兼容 OpenAI 协议的其他服务（如 DeepSeek）
  - `github_copilot_provider.py` - GitHub Copilot

**用到的主要工具**

- **提供商注册表** (`registry.py`)：根据配置自动实例化对应的 LLM 提供商
- **标准化接口** (`base.py`)：定义统一的调用、流式处理、停止序列等接口

**原理与技术**

- **策略模式**：通过提供商抽象，实现 AI 模型的即插即用
- **异步 I/O**：所有 LLM 调用采用异步实现，支持并发请求
- **请求规范化**：将用户输入转换为提供商特定的 API 请求格式

**多轮对话上下文管理**

- **消息历史维护**：在 `AgentRunSpec` 中维护 `initial_messages` 列表，每轮对话前追加用户消息
- **上下文窗口限制**：配置 `context_window_tokens` 控制历史深度，`context_block_limit` 控制块大小
- **自动压缩机制** (`autocompact.py`)：当上下文超限时自动归纳早期对话，保留近期交互

#### 代码示例

来源文件：`minibot/providers/registry.py`（文档示例，含简化调用）

```python
# 多提供商支持示例
provider = ProviderRegistry.get_provider(
    model="gpt-4",
    api_key=os.getenv("OPENAI_API_KEY")
)

# 或使用 Claude
provider = ProviderRegistry.get_provider(
    model="claude-3-opus",
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

# 多轮对话
messages = [
    {"role": "user", "content": "今天天气如何？"},
    {"role": "assistant", "content": "我需要查询天气信息..."},
    {"role": "user", "content": "帮我查一下北京的天气"}
]
response = await provider.complete(messages)
```

---

### 2. 工具调用（Function Calling）能力

#### 需求

- 至少实现 **3** 个自定义工具函数，每个工具能够完成一项具体的本地操作
- 大模型能够根据用户意图，自主判断是否需要调用工具、调用哪个工具、传入什么参数
- 工具执行结果能够被正确获取并返回给大模型

#### 实现方式

**工具系统架构**

**1. 工具基类与注册机制** (`minibot/agent/tools/base.py`)

- 每个工具继承 `Tool` 基类，实现统一的 `execute()` 方法
- 通过装饰器 `@tool_parameters()` 定义工具的入参 JSON Schema
- `ToolRegistry` 类管理所有注册的工具

**2. 核心工具集** （超过 3 个）


| 工具名          | 功能          | 文件              | 参数                                              |
| ------------ | ----------- | --------------- | ----------------------------------------------- |
| `exec`       | 执行 Shell 命令 | `shell.py`      | `command`, `working_dir`, `timeout`             |
| `read_file`  | 读取文件内容      | `filesystem.py` | `path`, `start_line`, `end_line`                |
| `write_file` | 写入文件        | `filesystem.py` | `path`, `content`                               |
| `edit_file`  | 编辑文件        | `filesystem.py` | `path`, `start_line`, `end_line`, `new_content` |
| `glob`       | 文件匹配查询      | `filesystem.py` | `pattern`, `recursive`                          |
| `list_dir`   | 列出目录内容      | `filesystem.py` | `path`                                          |
| `web_fetch`  | 抓取网页内容      | `web.py`        | `url`, `query`, `timeout`                       |
| `web_search` | 网络搜索        | `web.py`        | `query`, `max_results`                          |


**工具参数schema示例**
来源文件：`minibot/agent/tools/shell.py`

```python
@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("Shell命令"),
        working_dir=StringSchema("工作目录"),
        timeout=IntegerSchema(
            default=60,
            minimum=1,
            maximum=600,
            description="超时时间（秒）"
        ),
        required=["command"]
    )
)
class ExecTool(Tool):
    @property
    def name(self) -> str:
        return "exec"
    
    async def execute(self, command: str, **kwargs) -> str:
        # 执行逻辑
        pass
```

**原理与技术**

- **JSON Schema 定义**：通过 `tool_parameters_schema()` 精确定义每个工具的入参格式，LLM 据此自动生成正确的函数调用
- **类型系统**：`StringSchema`, `IntegerSchema` 等确保参数类型安全
- **动态注册**：运行时通过 `register()` 方法动态添加自定义工具，无需修改核心代码
- **异步执行**：所有工具实现异步 `execute()` 方法，支持并发执行

**LLM 自主判断工具调用**

- 大模型通过 **Function Calling API**（如 OpenAI 的 `tools` 参数）接收工具定义
- 大模型根据用户意图自动判断：
  - **是否需要调用工具**：根据用户问题的性质
  - **调用哪个工具**：根据工具的功能描述
  - **传入什么参数**：根据工具的 schema 和上下文
- 示例：用户说"查一下 `/tmp` 目录有什么文件"，大模型自动调用 `list_dir` 工具

#### 代码示例

来源文件：`minibot/agent/tools/registry.py`（文档示例，含简化字段）

```python
# 工具执行流程示例
registry = ToolRegistry()
registry.register(ExecTool())
registry.register(ReadFileTool())
registry.register(WebFetchTool())

# 大模型返回工具调用
tool_call = {
    "tool_name": "read_file",
    "arguments": {
        "path": "config.json",
        "start_line": 1,
        "end_line": 50
    }
}

# 执行工具并获取结果
tool = registry.get(tool_call["tool_name"])
result = await tool.execute(**tool_call["arguments"])

# 结果返回给大模型进行下一轮推理
```

---

### 3. ReAct 推理循环

#### 需求

- 实现一个基础的消息处理循环（推荐采用 ReAct 模式：Thought → Action → Observation）
- 支持多轮工具调用（一个复杂任务可能需要多次调用工具）
- 设置合理的终止条件（如 LLM 给出 final_answer 或达到最大轮数）

#### 实现方式

**ReAct 循环核心**（`minibot/agent/react_loop.py` + `minibot/agent/runner.py`）

ReAct（**Reasoning + Acting**，推理 + 行动）将一次任务拆成多步：每步先推理再决定是否调用工具，环境返回观察结果后再进入下一步，直到可以给出最终答案。与前端技术栈 **React.js** 无关；Minibot 在系统提示中也会明确这一点，避免模型混淆。

来源文件：`minibot/agent/react_loop.py` + `minibot/agent/runner.py`（ReAct 流程示意）

```
┌─────────────────────────────────────────┐
│  用户输入                                │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  1. Thought（思考）                      │
│  大模型分析问题，规划解决方案           │
│  输出：推理过程文本                     │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  2. Action（行动）                       │
│  大模型决定调用哪个工具及参数           │
│  输出：工具名 + 参数                    │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│  3. Observation（观察）                  │
│  系统执行工具，获取结果                 │
│  输出：工具执行结果                     │
└────────────┬────────────────────────────┘
             │
             ▼
   ┌─────────────────────────┐
   │ 是否需要继续循环？      │
   │ - final_answer = 是    │
   │ - 达到最大轮数        │
   │ - 出错？              │
   └─────────┬───────────────┘
         是  │  否
            ▼  │
          结束  │
              ▼
        返回最终答案
```

**关键实现细节**

**1. 循环初始化** (`AgentRunSpec`)
来源文件：`minibot/agent/runner.py`

```python
@dataclass
class AgentRunSpec:
    initial_messages: list[dict[str, Any]]  # 初始消息
    tools: ToolRegistry                      # 工具注册表
    model: str                               # 大模型标识
    max_iterations: int                      # 最大循环次数
    temperature: float | None                # 生成温度（控制多样性）
```

**2. 循环执行** (`runner.py` 中的 `run_agent()` 方法)

每次循环迭代：

1. **调用大模型**：
  - 发送当前消息历史 + 工具定义
  - 大模型返回思考过程 + 工具调用或最终答案
2. **判断大模型响应**：
  - 如果返回 `final_answer`：停止循环，返回答案
  - 如果返回工具调用：执行工具
3. **执行工具**：
  - 获取工具执行结果
  - 将结果作为 `observation` 附加到消息历史
4. **循环条件判断**：
  - `iterations < max_iterations`：继续循环
  - `iterations >= max_iterations`：强制退出，返回消息

**3. 终止条件**


| 条件                             | 说明                                | 优先级 |
| ------------------------------ | --------------------------------- | --- |
| 大模型返回 `final_answer`           | LLM 认为问题已解决                       | 最高  |
| `iterations >= max_iterations` | 达到最大轮数限制                          | 高   |
| 大模型返回空响应                       | LLM 无法继续推理                        | 中   |
| 工具执行错误                         | 工具调用失败且 `fail_on_tool_error=True` | 中   |


**4. 两种 ReAct 模式**

**函数调用模式**（推荐）

- 使用大模型的原生 Function Calling API（如 OpenAI tools）
- 大模型直接返回结构化的工具调用信息
- 优点：准确性高，延迟低

**文本模式**（`react_loop.py` + `react_prompt_template.py`）

- 适用于不支持或关闭原生 Function Calling 的模型
- 通过**系统提示词**约束模型按固定结构输出；解析侧用正则等方式从文本中提取 `<action>` 并执行工具，再把真实结果以 `<observation>` 写回对话
- 优点：兼容面广，行为由提示词与运行时规则共同约束

**5. 文本模式的 XML 标签与提示词模板**（`minibot/agent/react_prompt_template.py`）

文本模式下，默认系统提示要求模型**严格使用 XML 标签**，与循环中的「思考 → 行动 → 观察」一一对应：


| 标签               | 含义                                                   |
| ---------------- | ---------------------------------------------------- |
| `<question>`     | 用户问题（可选展示用）                                          |
| `<thought>`      | 本步推理与计划                                              |
| `<action>`       | 单次工具调用，语法为**合法 Python 调用**（见下）                       |
| `<observation>`  | **仅由环境/宿主注入**：工具执行后的真实返回；模型不得在输出 `<action>` 之后自行续写伪造 |
| `<final_answer>` | 面向用户的最终答复；通常出现在最后一轮                                  |


模板中的占位符在运行时被替换：

- `${tool_list}`：当前注册工具的 JSON Schema 列表（工具 `**name` 字段**为唯一合法工具名）
- `${operating_system}`：运行平台信息
- `${file_list}`：工作区目录预览（默认由 `workspace_file_list_preview()` 生成，可传入自定义字符串）

`build_react_prompt_from_template(tool_list=..., operating_system=..., file_list=..., workspace_root=...)` 会完成上述替换，并在正文末尾**追加** `MINIBOT_REACT_RUNTIME_RULES`（Minibot 专用运行时约定，与用户可覆盖的 `REACT_SYSTEM_PROMPT_TEMPLATE` 分离）。用户若需在仓库内覆盖默认中文模板，可在 `minibot/agent/prompt_template.py` 中提供 `react_system_prompt_template` 字符串；未配置或导入失败时回退到 `react_prompt_template.py` 内的默认模板。

**模型输出硬性约定（摘自默认模板与运行时规则）**

- 每一轮助手输出必须以 `<thought>` 开头，第二个块只能是 `<action>` **或** `<final_answer>`。
- 输出 `<action>` 后**立即停止生成**，等待宿主执行工具并注入真实 `<observation>`；擅自编造 observation 会导致状态错误。
- `<action>` 内多行字符串参数使用 `\n` 转义（例如 `content="a\nb"`）。
- 涉及路径的参数须使用**绝对路径或工作区相对路径并落到具体文件**，避免仅给出裸文件名。
- `<action>` 内优先使用**关键字参数**，以匹配工具 schema；示例里的虚构工具名仅作格式演示，实际必须以当前 JSON 中的 `name` 为准。
- **只回答对话中最新的那条用户消息**；更早消息仅作背景。
- **用户可见结论**应集中在 `<final_answer>`；不得在未真实执行工具的情况下在 `<final_answer>` 中编造「文件已写入/已删除」等结果。
- ReAct 文本模式下 `**message` 工具不可用**；与用户沟通用 `<final_answer>` 或工具说明中的结束语义；需要子代理时使用 `**spawn`**，不要用 `glob`/`list_dir` 代替完整协作请求。

**任务类型 A / B（运行时规则摘要）**

- **(A) 纯知识类**（问候、翻译、基于已有知识的解释等）：**不**调用工具，可在单轮内用 `<final_answer>` 结束；文本解析路径也支持 `<action>finish(...)</action>` 作为结束回合（见 `react_loop.py`）。
- **(B) 有副作用或依赖外部环境**（读写删文件、执行命令、联网检索新信息、`spawn` 等）：**必须**真实调用对应工具并得到 observation 后，再在末轮用 `<final_answer>` 汇报；禁止用自然语言或 shell one-liner **冒充**已执行的操作，禁止谎称无权限或已创建未实际调用的文件。

**用到的主要工具**

- **提供商接口**：调用 LLM 获取响应
- **工具执行器**：`runner.py` 中的工具执行逻辑
- **消息管理**：维护对话历史的消息列表
- **文本 ReAct 提示词**：`react_prompt_template.build_react_prompt_from_template()` 组装系统提示；`react_loop.py` 解析 `<action>` / `finish` 并驱动循环
- **反馈展示**：实时显示推理过程（见下文）

#### 代码示例

来源文件：`minibot/agent/runner.py`（文档示例，含简化流程）

```python
# ReAct 循环执行示例
async def run_agent(spec: AgentRunSpec) -> AgentRunResult:
    messages = spec.initial_messages.copy()
    
    for iteration in range(spec.max_iterations):
        # 1. 调用大模型
        response = await provider.complete(
            messages=messages,
            tools=spec.tools.to_openai_schema(),  # 发送工具定义
            max_tokens=spec.max_tokens,
            temperature=spec.temperature
        )
        
        # 2. 处理响应
        if response.finish_reason == "end_turn":
            # 大模型认为任务完成
            return AgentRunResult(message=response.content)
        
        elif response.finish_reason == "tool_calls":
            # 大模型请求调用工具
            for tool_call in response.tool_calls:
                # 3. 执行工具
                tool = spec.tools.get(tool_call.name)
                result = await tool.execute(**tool_call.arguments)
                
                # 4. 构建观察消息
                messages.append({
                    "role": "assistant",
                    "content": response.content
                })
                messages.append({
                    "role": "user",
                    "content": f"Tool result: {result}"
                })
```

**多轮调用示例**
来源文件：`minibot/agent/runner.py` + `minibot/agent/tools/search.py`（调用链示意）

```
用户："帮我统计 /data 目录中有多少个 .txt 文件，并输出文件列表"

第 1 轮：
  Thought：需要列出 /data 目录的所有文件
  Action：调用 list_dir('/data')
  Observation：获得文件列表

第 2 轮：
  Thought：需要过滤出 .txt 文件
  Action：调用 glob('/data/**/*.txt', recursive=True)
  Observation：获得 .txt 文件列表

第 3 轮：
  Thought：已获得所有 .txt 文件，可以计算数量和生成列表
  final_answer："共有 25 个 .txt 文件，列表如下..."
  （停止循环）
```

---

### 4. 本地执行与安全隔离

#### 需求

- 工具执行时应有基本的安全检查（如操作范围限定在指定目录内）
- 需要对工具调用过程中的异常进行捕获和友好提示

#### 实现方式

**安全隔离机制**

**1. 命令沙箱** (`minibot/agent/tools/sandbox.py`)

对所有 Shell 命令执行进行多层防护：

**第一层：命令黑名单** (Deny Patterns)
来源文件：`minibot/agent/tools/shell.py`

```python
deny_patterns = [
    r"\brm\s+-[rf]{1,2}\b",              # rm -r, rm -rf, rm -fr
    r"\bdel\s+/[fq]\b",                  # del /f, del /q
    r"\brmdir\s+/s\b",                   # rmdir /s
    r"(?:^|[;&|]\s*)format\b",           # format 磁盘格式化
    r"\b(mkfs|diskpart)\b",              # 磁盘操作
    r"\bdd\s+if=",                       # dd 命令
    r">\s*/dev/sd",                      # 直接写入磁盘
    r"\b(shutdown|reboot|poweroff)\b",   # 系统关机
    r":\(\)\s*\{.*\};\s*:",              # fork 炸弹
]
```

**第二层：命令白名单** (Allow Patterns)
来源文件：`minibot/agent/tools/shell.py`（默认允许模式）

```python
allow_patterns = [
    r"\b(?:echo|cat|head|tail|wc|grep|ls|pwd|printenv|python|curl|wget)\b"
]
```

- 默认只允许安全的读取/诊断命令
- 可通过配置 `allowed_commands` 动态扩展白名单

**第三层：操作范围限制**
来源文件：`minibot/agent/loop.py`（工具注册时的目录限制）

```python
restrict_to_workspace = True  # 只允许在工作目录内操作
workspace: Path = Path("/app/workspace")  # 指定安全工作目录
```

**2. 文件系统隔离** (`minibot/agent/tools/filesystem.py`)

- **读写权限检查**：检查目标路径是否在允许的工作目录内
- **符号链接防护**：防止通过符号链接逃离沙箱
- **绝对路径规范化**：将所有相对路径转换为规范化的绝对路径

**3. 异常捕获与处理**

所有工具执行都用 try-catch 包装：

来源文件：`minibot/agent/tools/registry.py` + `minibot/agent/tools/shell.py` + `minibot/agent/tools/filesystem.py`（异常处理模式汇总）

```python
try:
    result = await tool.execute(**params)
except Exception as e:
    return f"Error executing {name}: {str(e)}"
# 工具内部通常也会捕获 PermissionError/TimeoutError 并返回 Error 字符串
```

**原理与技术**

- **白名单 + 黑名单结合**：既允许常见操作，又阻止危险命令
- **正则表达式匹配**：高效检测模式匹配
- **路径规范化**：使用 `Path.resolve()` 获得真实路径，防止相对路径绕过
- **最小权限原则**：默认最严格，用户可根据需要配置

**友好的错误提示**


| 错误类型     | 用户提示                               |
| -------- | ---------------------------------- |
| 命令被黑名单阻止 | `❌ 安全限制：该命令包含危险操作（如 rm -rf）`       |
| 路径超出工作目录 | `❌ 安全限制：只能访问工作目录内的文件（{workspace})` |
| 工具超时     | `⏱️ 命令执行超时（60秒），请检查命令或增加超时时间`      |
| 权限不足     | `🔒 权限不足：无法访问该文件或目录`               |
| 文件不存在    | `📂 文件不存在：{path}`                  |


#### 代码示例

来源文件：`minibot/agent/tools/shell.py`（文档示例，参数名已简化）

```python
# 安全配置示例
from minibot.agent.tools.shell import ExecTool

exec_tool = ExecTool(
    timeout=60,                           # 最大执行时间
    restrict_to_workspace=True,           # 限制在工作目录内
    workspace="/app/workspace",           # 工作目录
    allowed_commands={                    # 自定义允许的命令
        "python": ["--version", "-m", "pip"],
        "pip": ["install", "list"]
    },
    deny_patterns=[...]                   # 额外的黑名单
)

# 安全执行示例
try:
    result = await exec_tool.execute(
        command="python script.py",
        working_dir="/app/workspace"
    )
except SecurityError as e:
    print(f"⚠️ {e.message}")
```

---

## 第二部分：扩展要求

### 5. 对话历史持久化

#### 需求

将对话记录保存到本地 JSON 文件，下次启动时可加载历史

#### 实现方式

会话记录保存地址：`C:\Users\25283\.minibot\workspace\sessions`

长期记忆保存地址：`"C:\Users\25283\.minibot\workspace\memory\history.jsonl"`

会话历史持久化结构：`minibot/session/manager.py`

相关辅助函数： `minibot/utils/helpers.py`（真实结构，已简化）

```json
{
  "_type": "metadata",
  "key": "cli:direct",
  "created_at": "2026-04-26T22:00:00",
  "updated_at": "2026-04-26T22:05:12",
  "metadata": {},
  "last_consolidated": 0
}
```

**实现原理**

**1. 自动保存** (`maybe_persist_tool_result()`)

- 每次工具执行后自动将结果保存到 `history.jsonl`
- 使用 JSONL 格式（每行一条 JSON 记录），支持流式追加

**2. 会话管理** (`minibot/session/`)

- 每个会话有唯一的 `session_key`
- 支持多个并发会话，互不干扰
- 会话文件统一存储在 `~/.minibot/sessions/` 目录

**3. 加载历史**
来源文件：`minibot/session/manager.py`（真实接口）

```python
manager = SessionManager(workspace=Path("."))
session = manager.get_or_create("cli:direct")
history_for_llm = session.get_history(max_messages=500)
```

**4. 隐私与清理**

- 支持手动删除特定会话
- 可配置历史保留时间（默认 30 天自动清理）
- 敏感信息可标记为 `[REDACTED]`

#### 代码示例

来源文件：`minibot/session/manager.py`（真实实现，伪代码化简）

```python
class SessionManager:
    def save(self, session: Session) -> None:
        path = self._get_session_path(session.key)
        json_path = self._get_session_json_path(session.key)
        # 1) 写 JSONL（metadata + messages）
        # 2) 再写 JSON（便于外部读取）
        ...
```

---

### 6. 工具插件化

#### 需求

支持通过配置文件或装饰器动态注册新工具，无需修改核心代码

#### 实现方式

**插件系统架构**

**1. 装饰器方式** （最简洁）

来源文件：`minibot/agent/tools/plugin_loader.py`（真实装饰器）

```python
from minibot.agent.tools.base import Tool
from minibot.agent.tools.plugin_loader import tool_plugin

@tool_plugin
class MyTool(Tool):
    ...
```

**2. 配置文件方式** (`minibot/config/tools.yml`)

来源文件：`minibot/config/schema.py`（真实配置项，示意 YAML）

```yaml
tools:
  tool_plugins:
    - plugin_tools_test.myTools
```

**3. 插件加载器** (`minibot/agent/tools/plugin_loader.py`)

来源文件：`minibot/agent/tools/plugin_loader.py`（真实实现，伪代码化简）

```python
def load_tool_plugins(module_paths, registry, workspace, allowed_dir, extra_allowed_dirs=None):
    for module_path in module_paths:
        module = importlib.import_module(module_path)
        if callable(getattr(module, "register_tools", None)):
            module.register_tools(registry=registry, workspace=workspace, allowed_dir=allowed_dir)
        for tool_cls in _iter_marked_tool_classes(module):
            tool = _instantiate_plugin_tool(tool_cls, workspace=workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_allowed_dirs)
            if tool:
                registry.register(tool)
```

**原理与技术**

- **动态导入**：使用 `__import__()` 和反射在运行时加载类
- **配置驱动**：从 YAML 文件读取工具配置，支持环境变量替换
- **接口规范**：所有工具必须继承 `Tool` 基类，保证接口一致性
- **零修改**：新工具可完全独立开发，只需在配置中注册

#### 代码示例

来源文件：`plugin_tools_test/myTools.py` + `minibot/agent/loop.py`（项目内可运行示例）

```python
# 1) 在 plugin_tools_test/myTools.py 中声明 @tool_plugin 工具
# 2) 在配置里设置 tools.tool_plugins=["plugin_tools_test.myTools"]
# 3) AgentLoop._register_default_tools() 调用 load_tool_plugins(...)
# 4) 插件工具自动进入 ToolRegistry，可被模型调用
```

- 具体示例
1.用装饰器注册的工具所在文件：
`E:\githubrepository\bot\minibot\plugin_tools_test\myTools.py`
2.在对话框中说：请用 hello_plugin 工具，参数 msg 为 manual-test, 告诉我调用工具后完整的返回信息；
3.结果检查:若返回[hello_plugin] manual-test，则插入成功

---

### 7. 流式输出

#### 需求

支持 LLM 的流式响应，逐字输出（而不是一次性返回）

#### 实现方式

**流式响应实现**

**1. 大模型端** (`minibot/providers/`)

所有提供商都支持流式模式：

来源文件：`minibot/providers/base.py`（真实接口）

```python
class LLMProvider(ABC):
    async def chat_stream(..., on_content_delta=None) -> LLMResponse:
        # 默认实现：用假流式流式输出兜底，各具体provider里通过覆盖同名的方法实现真流式输出
        response = await self.chat(...)
        if on_content_delta and response.content:
            await on_content_delta(response.content)
        return response

    async def chat_stream_with_retry(..., on_content_delta=None, retry_mode="standard") -> LLMResponse:
        return await self._run_with_retry(self._safe_chat_stream, kw, messages, retry_mode=retry_mode)
```

- **真流式输出（以openai_combat为例）**
文件：`E:\githubrepository\bot\minibot\minibot\providers\openai_compat_provider.py`
函数：chat_stream()    对base.py中的chat_stream()进行重写

```python
stream_iter = stream.__aiter__()
while True:
    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=90)
    # 90秒超时自动断开
    if on_content_delta and chunk.choices:
        # 从第一条 choice 里拿到本次增量文本（delta）
        text = chunk.choices[0].delta.content
        if text:
            await on_content_delta(text)  
            # 每个新字都实时发送
```

**2. 实时展示** (`minibot/utils/react_display.py`)

在控制台逐字显示响应：

来源文件：`minibot/agent/loop.py` + `minibot/providers/base.py`（真实调用链，伪代码化简）

```python
async def _on_delta(chunk: str) -> None:
    print(chunk, end="", flush=True)

response = await provider.chat_stream_with_retry(
    messages=messages_for_model,
    tools=tools,
    model=model,
    on_content_delta=_on_delta,
)
```

**3. 集成到 ReAct 循环**

来源文件：`minibot/agent/runner.py`（真实循环，伪代码化简）

```python
async def run(self, spec: AgentRunSpec) -> AgentRunResult:
    messages = list(spec.initial_messages)
    for iteration in range(spec.max_iterations):
        response = await self._request_model(spec, messages_for_model, hook, context)
        if response.has_tool_calls:
            # 执行工具并把 tool 结果追加到 messages
            ...
        else:
            # 生成最终答案并退出
            ...
```

**4. 通道集成** （支持多个 UI）

- **终端输出**：直接 print，带彩色格式
- **Web 接口**：通过 WebSocket 发送 SSE（Server-Sent Events）
- **聊天应用**：集成到 WeChat、QQ、Feishu 等

**原理与技术**

- **异步生成器**：使用 `async for` 逐个消费流式数据
- **立即刷新**：`flush=True` 确保每个字符立即显示，不等待缓冲区满
- **非阻塞 I/O**：异步实现保证 UI 不冻结
- **编码处理**：正确处理 Unicode，支持中文、Emoji 等

#### 代码示例

来源文件：`minibot/providers/base.py`（真实可运行调用方式）

```python
import asyncio

async def main():
    async def on_delta(text: str) -> None:
        print(text, end="", flush=True)

    response = await provider.chat_stream_with_retry(
        messages=[{"role": "user", "content": "用 Python 写一个快速排序"}],
        on_content_delta=on_delta,
    )
    print("\nfinish_reason:", response.finish_reason)

asyncio.run(main())
```

---

### 8. 多轮规划展示

#### 需求

在控制台清晰展示每轮的"思考→行动→观察"过程

#### 实现方式

**ReAct 过程可视化** (`minibot/utils/react_display.py`)

**完整展示流程**

来源文件：`minibot/utils/react_display.py` + `minibot/agent/runner.py`（显示流程示意）

```
╔════════════════════════════════════════════════════════════╗
║  🤖 Minibot ReAct Agent                                   ║
╚════════════════════════════════════════════════════════════╝

📥 用户输入：帮我查一下当前时间和天气

─────────────────────────────────────────────────────────────
⚡ 第 1 轮 (Iteration 1/200)
─────────────────────────────────────────────────────────────

🧠 思考 (Thought):
我需要获取当前时间和查询天气。可以：
1. 使用 `exec` 工具获取系统时间
2. 使用 `web_search` 工具查询天气

✅ 行动 (Action):
┌─ 工具调用 1: exec
│  └─ 参数: command="date", working_dir="."
└─ 工具调用 2: web_search
   └─ 参数: query="当前天气北京"

📊 观察 (Observation):
┌─ 工具 #1 结果:
│  2024-04-24 14:35:42 UTC
└─ 工具 #2 结果:
   北京 25°C 晴转多云，风向西南风

─────────────────────────────────────────────────────────────
⚡ 第 2 轮 (Iteration 2/200)
─────────────────────────────────────────────────────────────

🧠 思考 (Thought):
我已经获得了当前时间和天气信息，可以给出最终答案。

📋 最终答案 (Final Answer):
✅ 当前时间：2024-04-24 14:35:42 UTC
✅ 北京天气：25°C，晴转多云
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 执行统计:
  - 总轮数: 2 轮
  - 工具调用: 2 次
  - 执行时间: 2.34s
  - 消息令牌: 156 / 4096
```

**实现原理**

**1. 每轮记录结构**
来源文件：`minibot/agent/runner.py`（真实数据结构）

```python
@dataclass
class AgentRunResult:
    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
```

**2. 彩色格式化输出**
来源文件：`minibot/utils/react_display.py`（真实格式化逻辑）

```python
def format_dialogue_block(role: str, label: str | None, text: str) -> str:
    header = "main agent: " if role == "main agent" else "subagent: "
    if role == "subagent" and label:
        header += f"({label}) "
    hang = " " * len(header)
    lines = text.split("\n")
    return "\n".join([header + lines[0]] + [(hang + ln) for ln in lines[1:]])
```

**3. 进度跟踪**
来源文件：`minibot/agent/runner.py`（真实循环骨架）

```python
for iteration in range(spec.max_iterations):
    context = AgentHookContext(iteration=iteration, messages=messages)
    await hook.before_iteration(context)
    response = await self._request_model(spec, messages_for_model, hook, context)
    if response.has_tool_calls:
        results, new_events, fatal_error = await self._execute_tools(...)
        ...
```

**4. 最终统计** (`minibot/utils/runtime.py`)
来源文件：`minibot/agent/runner.py`（真实统计字段）

```python
usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
tools_used: list[str] = []
# 每轮调用 self._accumulate_usage(usage, raw_usage)
# 工具调用时 tools_used.extend(tc.name for tc in response.tool_calls)
# 结束时写入 AgentRunResult(..., usage=usage, tools_used=tools_used)
```

#### 代码示例

来源文件：`minibot/utils/react_display.py` + `minibot/agent/runner.py`（真实能力组合，伪代码化简）

```python
raw = "Thought: ...\nAction: read_file\nObservation: ..."
pretty = format_react_text(raw)
line = format_dialogue_block("main agent", None, pretty)
print(line)
```

---

### 9. 安全的 Shell 执行沙箱

#### 需求

对 `run_shell_command` 实现更严格的命令白名单和参数校验

#### 实现方式

**分层防护系统**

**第一层：命令级别的白名单**

来源文件：`minibot/agent/tools/shell.py`（真实配置结构）

```python
ExecTool(
    allow_patterns=[r"\b(?:echo|cat|head|tail|wc|grep|ls|pwd|printenv|python|curl|wget)\b"],
    deny_patterns=[
        r"\brm\s+-[rf]{1,2}\b",
        r"\bdel\s+/[fq]\b",
        r"\brmdir\s+/s\b",
        r"\b(mkfs|diskpart)\b",
    ],
    allowed_commands={"python": [r"(?i)\b-m\b", r"(?i)\b--version\b"]},  # 可选
)
```

**第二层：参数解析与验证**

来源文件：`minibot/agent/tools/shell.py`（真实校验流程，伪代码化简）

```python
class CommandValidator:
    def validate_command(self, command: str) -> tuple[bool, str]:
        # 1) 命中 deny_patterns -> 拒绝
        # 2) allowed_commands 开启时，解析子命令并校验参数模式
        # 3) allow_patterns 开启时，要求命中安全白名单
        # 4) 检测内网 URL / 路径穿越 / 越界绝对路径
        return self._guard_command(command, cwd)
```

**第三层：执行环境隔离**

来源文件：`minibot/agent/tools/sandbox.py` + `minibot/agent/tools/shell.py`（真实实现）

```python
if self.sandbox:
    command = wrap_command(self.sandbox, command, workspace, cwd)  # bwrap backend
process = await self._spawn(command, cwd, env)
stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
```

#### 代码示例

来源文件：`minibot/agent/tools/shell.py` + `minibot/agent/tools/sandbox.py`（真实调用示例，伪代码化简）

```python
exec_tool = ExecTool(
    working_dir=str(workspace),
    timeout=60,
    restrict_to_workspace=True,
    sandbox="bwrap",  # Unix 可选
    allowed_commands={"python": [r"(?i)\b-m\b", r"(?i)\b--version\b"]},
)
result = await exec_tool.execute(command="python -m pip list")
```

---

### 10. 支持多 Agent 协作雏形
[资料]:https://zhuanlan.zhihu.com/p/2018265716139237578
[subagent存储地址]:C:\Users\25283\.minibot\workspace\.minibot\persistent_subagents.json

#### 需求

实现一个简单的"主 Agent + 子 Agent"模式（如主 Agent 可将文件分析任务委托给专门的分析子 Agent）

#### 实现方式

**多 Agent 协作架构** (`minibot/agent/subagent.py`)

来源文件：`minibot/agent/subagent.py` + `minibot/agent/tools/spawn.py`（架构示意）

```
┌──────────────────────────────┐
│  主 Agent (Main Agent)        │
│  - 任务路由器                 │
│  - 结果汇聚                   │
│  - 最终决策                   │
└──────────────────┬─────────────┘
                   │
        ┌──────────┼──────────┐
        │          │          │
        ▼          ▼          ▼
   ┌────────┐  ┌────────┐  ┌────────┐
   │ 子 A1  │  │ 子 A2  │  │ 子 A3  │
   │ 文件   │  │ 代码   │  │ 网络   │
   │ 分析   │  │ 审查   │  │ 查询   │
   └────────┘  └────────┘  └────────┘
```

**1. 主 Agent 实现**

来源文件：`minibot/agent/subagent.py`（真实实现结构，伪代码化简）

```python
class SubagentManager:
    async def spawn(...):
        # 创建 task_id，登记 _running_tasks / _session_tasks
        # 后台运行 self._run_subagent(...)
        return "Subagent [...] started"

    async def _run_subagent(...):
        # 1) 构建子 agent 专属 ToolRegistry（无 message / 无 spawn）
        # 2) 用 AgentRunner.run(AgentRunSpec(...)) 执行
        # 3) 通过 bus 回传进度与最终结果
        ...
```

**2. 子 Agent 实现**

来源文件：`minibot/agent/tools/spawn.py`（真实入口）

```python
class SpawnTool(Tool):
    async def execute(self, task=None, label=None, command=None, from_persisted_label=None, **kwargs):
        # 参数校验：task / command / from_persisted_label 三选组合
        # 自动推断职责标签 infer_subagent_responsibilities(...)
        return await self._manager.spawn(...)
```

**3. 具体场景示例：文件分析 + 代码审查 + 网络查询**

来源文件：`minibot/agent/loop.py` + `minibot/agent/tools/spawn.py`（真实使用方式）

```python
# AgentLoop 初始化时注册 SpawnTool / SubagentRosterTool
# LLM 触发:
#   Action: spawn
#   Observation: {"task":"分析 README 并输出结论","label":"readme-analyzer"}
# SpawnTool -> SubagentManager.spawn(...) -> 后台运行并回传结果
```

**4. 任务分配策略**


| 策略                  | 场景       | 实现               |
| ------------------- | -------- | ---------------- |
| **Sequential** (顺序) | 子任务有依赖关系 | 按顺序执行，后一个用前一个的结果 |
| **Parallel** (并行)   | 子任务独立    | 异步并发执行，然后汇聚结果    |
| **Pipeline** (流水线)  | 任务可分阶段   | 上一阶段的输出作为下一阶段的输入 |


**5. 通信机制**

来源文件：`minibot/bus/events.py` + `minibot/bus/queue.py` + `minibot/agent/subagent.py`（真实通信通道）

```python
# SubagentManager 通过 MessageBus.publish_outbound(...) 发布进度与最终结果
# 入站消息通过 InboundMessage 注入主流程，保证主/子 agent 同会话串联
```

#### 代码示例

来源文件：`minibot/agent/subagent.py`（真实可执行路径，伪代码化简）

```python
# 1) 用户请求复杂任务 -> 模型调用 spawn
# 2) SubagentManager.spawn() 启动后台 _run_subagent()
# 3) _run_subagent() 内部 runner.run(AgentRunSpec(...))
# 4) 完成后 _announce_subagent_result() 把结果发回原会话
```

---

## 总结

Minibot 通过以下核心设计完整实现了所有项目需求：


| 需求         | 实现方式                       | 关键文件                                                           |
| ---------- | -------------------------- | -------------------------------------------------------------- |
| 大模型接入      | 多提供商抽象层 + 统一接口             | `providers/base.py`                                            |
| 工具调用       | ToolRegistry + JSON Schema | `agent/tools/registry.py`                                      |
| ReAct循环    | 推理-行动-观察循环；文本模式 XML 提示词    | `agent/runner.py`, `react_loop.py`, `react_prompt_template.py` |
| 安全隔离       | 黑白名单 + 沙箱执行                | `agent/tools/sandbox.py`                                       |
| 历史持久化      | JSONL 流式存储                 | `session/`                                                     |
| 工具插件化      | 装饰器 + 配置加载                 | `agent/tools/plugin_loader.py`                                 |
| 流式输出       | 异步生成器 + 实时展示               | `providers/`                                                   |
| 多轮展示       | Rich 彩色输出                  | `utils/react_display.py`                                       |
| Shell 沙箱   | 分层防护系统                     | `agent/tools/shell.py`                                         |
| 多 Agent 协作 | 主子 Agent 委托                | `agent/subagent.py`                                            |


所有实现都遵循**超轻量级**原则，用最少代码提供最大功能。

# minibot web启动启动

1.后端

```bash
cd e:\githubrepository\bot\minibot\minibot-web\backend
npm run dev
```

2.前端

```bash
cd e:\githubrepository\bot\minibot\minibot-web\frontend
npm run dev
```

3.网关

```bash
minibot gateway
```


# QQ群聊信息整理
[代码]:E:\githubrepository\bot\minibot\minibot\channels\qq_collector.py
通过napcat获得被代理的账号名称和qq号，并通过正则匹配@“我”或@所有人
