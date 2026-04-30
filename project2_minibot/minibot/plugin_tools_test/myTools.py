from minibot.agent.tools.base import Tool, tool_parameters
from minibot.agent.tools.plugin_loader import tool_plugin
from minibot.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_plugin
@tool_parameters(
    tool_parameters_schema(
        msg=StringSchema("Test message"),
        required=["msg"],
    )
)
class HelloPluginTool(Tool):
    @property
    def name(self) -> str:
        return "hello_plugin"

    @property
    def description(self) -> str:
        return "Manual test plugin: echoes msg."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }

    async def execute(self, msg: str, **kwargs):
        return f"[hello_plugin] {msg}"