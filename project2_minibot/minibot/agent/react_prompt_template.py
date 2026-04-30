"""ReAct (XML tag) system prompt template and builder.

The template uses ``${tool_list}``, ``${operating_system}``, ``${file_list}``
placeholders filled at runtime. Tool catalogs are JSON schemas from
:class:`minibot.agent.tools.registry.ToolRegistry`.
"""

from __future__ import annotations

import platform
from pathlib import Path

# Default template (used when external override is absent).
_DEFAULT_REACT_SYSTEM_PROMPT_TEMPLATE = """
你需要解决一个问题。为此，你需要将问题分解为多个步骤。对于每个步骤，首先使用 <thought> 思考要做什么，然后使用可用工具之一决定一个 <action>。接着，你将根据你的行动从环境/工具中收到一个 <observation>。持续这个思考和行动的过程，直到你有足够的信息来提供 <final_answer>。

所有步骤请严格使用以下 XML 标签格式输出：
- <question> 用户问题
- <thought> 思考
- <action> 采取的工具操作
- <observation> 工具或环境返回的结果
- <final_answer> 最终答案

⸻

例子 1:

<question>埃菲尔铁塔有多高？</question>
<thought>我需要找到埃菲尔铁塔的高度。可以使用搜索工具。</thought>
<action>get_height("埃菲尔铁塔")</action>
<observation>埃菲尔铁塔的高度约为330米（包含天线）。</observation>
<thought>搜索结果显示了高度。我已经得到答案了。</thought>
<final_answer>埃菲尔铁塔的高度约为330米。</final_answer>

⸻

例子 2:

<question>帮我找一个简单的番茄炒蛋食谱，并看看家里的冰箱里有没有西红柿。</question>
<thought>这个任务分两步。第一步，找到番茄炒蛋的食谱。第二步，检查冰箱里是否有西红柿。我先用 find_recipe 工具找食谱。</thought>
<action>find_recipe(dish="番茄炒蛋")</action>
<observation>简单的番茄炒蛋食谱：将2个鸡蛋打散，2个番茄切块。热油，先炒鸡蛋，盛出。再热油，炒番茄至软烂，加入鸡蛋，放盐调味即可。</observation>
<thought>好的，我已经有食谱了。食谱需要西红柿。现在我需要用 check_fridge 工具看看冰箱里有没有西红柿。</thought>
<action>check_fridge(item="西红柿")</action>
<observation>冰箱检查结果：有3个西红柿。</observation>
<thought>我找到了食谱，并且确认了冰箱里有西红柿。可以回答问题了。</thought>
<final_answer>简单的番茄炒蛋食谱是：鸡蛋打散，番茄切块。先炒鸡蛋，再炒番茄，混合后加盐调味。冰箱里有3个西红柿。</final_answer>

⸻

请严格遵守：
- 你每次回答都必须包括两个标签，第一个是 <thought>，第二个是 <action> 或 <final_answer>
- 输出 <action> 后立即停止生成，等待真实的 <observation>，擅自生成 <observation> 将导致错误
- 如果 <action> 中的某个工具参数有多行的话，请使用 \\n 来表示，如：<action>write_file(path="/tmp/test.txt", content="a\\nb\\nc")</action>
- 工具参数中的文件路径请使用绝对路径或工作区相对路径并具体到文件，不要只给出一个裸文件名。例如：<action>write_file(path="subdir/note.txt", content="hello")</action>

⸻

本次任务可用工具：
${tool_list}

⸻

环境信息：

操作系统：${operating_system}
当前目录下文件列表：${file_list}
"""

# External override: let users edit minibot/agent/prompt_template.py directly.
try:
    from minibot.agent.prompt_template import react_system_prompt_template as _USER_REACT_SYSTEM_PROMPT_TEMPLATE
except Exception:
    _USER_REACT_SYSTEM_PROMPT_TEMPLATE = None

REACT_SYSTEM_PROMPT_TEMPLATE = (
    _USER_REACT_SYSTEM_PROMPT_TEMPLATE
    if isinstance(_USER_REACT_SYSTEM_PROMPT_TEMPLATE, str) and _USER_REACT_SYSTEM_PROMPT_TEMPLATE.strip()
    else _DEFAULT_REACT_SYSTEM_PROMPT_TEMPLATE
)

# Appended after the user template so Minibot-specific runtime rules stay in-repo.
MINIBOT_REACT_RUNTIME_RULES = """

---

## Minibot 运行时约定（必读）

- **ReAct** 指 **Reasoning + Acting** 推理-行动循环，**不是** React.js / npm / 前端开发栈；**不要**让用户确认「前端 React」与「推理 ReAct」，也不要主动读 package.json 来「检查 React 项目」。
- 本轮 **只回答对话里最靠后的那条用户消息**；更早消息仅为背景，不要合并无关旧问题。
- 下方 JSON 中的工具 **`name` 字段** 才是合法工具名（如 `write_file`、`web_search`、`spawn`）；**必须与之一致**，示例里的 `get_height`、`find_recipe` 等仅作格式演示。
- 调用工具时，`<action>` 内请使用 **合法的 Python 调用语法**，**优先使用关键字参数** 以匹配工具 schema，例如：
  `<action>write_file(path="notes.txt", content="hi")</action>`、
  `<action>web_search(query="天气", count=3)</action>`。
- **用户可见的最终答案**只应出现在 `<final_answer>...</final_answer>`（最后一轮）；不要在未真实执行工具的情况下在 `<final_answer>` 中编造「文件已创建/已删除」等结果。
- 需要委派子代理时请使用 **`spawn`**；`message` 工具在 ReAct 文本模式下不可用，与用户对话请用 `<final_answer>` 或结束前的 `finish` 类语义（见工具说明）。

### 工具调用 vs 直接结束（category A / B）

- **(A) 纯知识类**（笑话、问候、翻译、基于自身知识的定义/解释）：**不要**调用工具，一轮内用 `<final_answer>`（或等价地 `<action>finish(...)</action>`）结束。
- **(B) 会改变工作区或需外部执行的 side-effect / state-changing 任务**（新建/写入/修改/删除文件、运行命令、联网检索新信息、`spawn` 子代理等）：**必须**真实调用目录中的工具拿到环境返回的 observation 后，再在最后一轮用 `<final_answer>` 汇报。**禁止**仅用 shell one-liner 或自然语言冒充已执行的操作；**禁止**谎称 lack permission / cannot create files；不要编造未调用 `write_file` / `edit_file` / `delete_file` / `notebook_edit` / `exec` / `spawn` 就已成功的结果。委派多 agent 时请用 `spawn`，不要用 `glob`/`list_dir` 代替整套协作请求。
- 最小示例（写入文件，category B）：先 `<action>write_file(path="测试2.txt", content="第二次写入测试")</action>`，在收到真实 observation 后，再 `<final_answer>已在 workspace 新建 测试2.txt …</final_answer>`。
"""


def workspace_file_list_preview(root: Path | None = None, *, limit: int = 80) -> str:
    """Short listing of *root* (default cwd) for the environment section of the prompt."""
    base = (root or Path.cwd()).resolve()
    try:
        names = sorted(
            p.name for p in base.iterdir() if not p.name.startswith(".")
        )
    except OSError:
        return "(无法列出目录)"
    if not names:
        return "(目录为空)"
    if len(names) > limit:
        head = "\n".join(names[:limit])
        return f"{head}\n… 共 {len(names)} 项，已截断显示前 {limit} 项"
    return "\n".join(names)


def build_react_prompt_from_template(
    *,
    tool_list: str,
    operating_system: str | None = None,
    file_list: str | None = None,
    workspace_root: Path | None = None,
) -> str:
    """Fill ``REACT_SYSTEM_PROMPT_TEMPLATE`` and append :data:`MINIBOT_REACT_RUNTIME_RULES`."""
    os_line = operating_system if operating_system is not None else platform.platform()
    files = file_list if file_list is not None else workspace_file_list_preview(workspace_root)
    body = REACT_SYSTEM_PROMPT_TEMPLATE.replace("${tool_list}", tool_list.strip())
    body = body.replace("${operating_system}", os_line.strip())
    body = body.replace("${file_list}", files.strip())
    return body.strip() + MINIBOT_REACT_RUNTIME_RULES
