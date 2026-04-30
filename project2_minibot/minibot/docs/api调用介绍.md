下面按 **「工具定义 → 请求里带上 tools → 模型返回 tool_calls → 解析成统一结构 → 执行工具 → 把结果塞回 messages」** 说明 Minibot 里 **Function Calling**（OpenAI 风格的 `tools` / `tool_calls`）是怎么接上的，并引用仓库里的具体代码。

---

### 1. 工具侧：把每个工具变成 OpenAI 要求的 `type: function` 条目

每个 `Tool` 子类通过 `to_schema()` 产出标准 **function** 描述（`name`、`description`、`parameters` JSON Schema），这就是发给 API 的 **`tools`** 数组里的一项：

```234:243:e:\githubrepository\bot\minibot\minibot\agent\tools\base.py
    def to_schema(self) -> dict[str, Any]:
        """OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

`ToolRegistry.get_definitions()` 收集所有已注册工具的 `to_schema()`，排序后返回完整列表，供上层一次传给模型：

```59:77:e:\githubrepository\bot\minibot\minibot\agent\tools\registry.py
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.
        """
        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        return builtins + mcp_tools
```

**小结**：Function Calling 的「函数列表」不是手写死的，而是由 **注册表 + `to_schema()`** 动态生成。

---

### 2. Agent 循环：把 `tools` 交给 `LLMProvider.chat*`

`AgentRunner._request_model` 把当前对话 `messages` 和 **`spec.tools.get_definitions()`** 打进 `provider.chat_with_retry` / `chat_stream_with_retry`：

```558:578:e:\githubrepository\bot\minibot\minibot\agent\runner.py
    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
    ):
        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=spec.tools.get_definitions(),
        )
        if hook.wants_streaming():
            async def _stream(delta: str) -> None:
                await hook.on_stream(context, delta)

            return await self.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
            )
        return await self.provider.chat_with_retry(**kwargs)
```

`LLMProvider.chat_with_retry`（`base.py`）只是把参数转给子类的 `chat()`，并带重试策略。

---

### 3. OpenAI 兼容实现：HTTP 层真正带上 `tools` 和 `tool_choice`

对 **Chat Completions** 路径，`OpenAICompatProvider._build_kwargs` 在 `tools` 非空时设置 **`kwargs["tools"]`** 和 **`kwargs["tool_choice"]`**（默认 `"auto"`，即由模型决定是否调用）：

```399:403:e:\githubrepository\bot\minibot\minibot\providers\openai_compat_provider.py
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        return kwargs
```

`chat()` 里多数情况调用官方 SDK 的 **`chat.completions.create(kwargs)`**，也就是把上面的 `messages`、`tools`、`tool_choice` 等原样交给 OpenAI 兼容服务端： 

```901:927:e:\githubrepository\bot\minibot\minibot\providers\openai_compat_provider.py
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    return parse_response_output(await self._client.responses.create(**body))
                except Exception as responses_error:
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            return self._parse(await self._client.chat.completions.create(**kwargs))
```

**补充**：直连 OpenAI 且满足 `_should_use_responses_api` 时，会走 **Responses API**（`responses.create`），工具通过 `_build_responses_body` 里的 `convert_tools(tools)` 转换（同一文件内），返回再用 `parse_response_output` 归一成 `LLMResponse`。对调用方（runner）来说仍是统一的 `LLMResponse.tool_calls`。

---

### 4. 解析响应：把 API 的 `tool_calls` 变成内部的 `ToolCallRequest`

Chat Completions 返回里，`choices[0].message.tool_calls` 是标准结构。`_parse` 把每条里的 `function.name` 和 `function.arguments`（JSON 字符串）解出来，构造 **`ToolCallRequest`** 列表；`arguments` 用 `json_repair.loads` 容错：

```619:663:e:\githubrepository\bot\minibot\minibot\providers\openai_compat_provider.py
            raw_tool_calls: list[Any] = []
            # StepFun Plan: fallback to reasoning field when content is empty
            if not content and msg0.get("reasoning"):
                content = self._extract_text_content(msg0.get("reasoning"))
            reasoning_content = msg0.get("reasoning_content")
            if not reasoning_content and msg0.get("reasoning"):
                reasoning_content = self._extract_text_content(msg0.get("reasoning"))
            for ch in choices:
                ch_map = self._maybe_mapping(ch) or {}
                m = self._maybe_mapping(ch_map.get("message")) or {}
                tool_calls = m.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    raw_tool_calls.extend(tool_calls)
                    if ch_map.get("finish_reason") in ("tool_calls", "stop"):
                        finish_reason = str(ch_map["finish_reason"])
                if not content:
                    content = self._extract_text_content(m.get("content"))
                if not reasoning_content:
                    reasoning_content = m.get("reasoning_content")

            parsed_tool_calls = []
            for tc in raw_tool_calls:
                tc_map = self._maybe_mapping(tc) or {}
                fn = self._maybe_mapping(tc_map.get("function")) or {}
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json_repair.loads(args)
                ec, prov, fn_prov = _extract_tc_extras(tc)
                parsed_tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                    extra_content=ec,
                    provider_specific_fields=prov,
                    function_provider_specific_fields=fn_prov,
                ))

            return LLMResponse(
                content=content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                usage=self._extract_usage(response_map),
                reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            )
```

**小结**：**Function Calling API 的「调用」**在客户端体现为 **`chat.completions.create(..., tools=..., tool_choice=...)`**；**「实现」**在 Minibot 里体现为 **`_parse` → `LLMResponse` + `ToolCallRequest`**，与具体厂商字段解耦。

流式场景下，同类逻辑在 `_parse_chunks`（拼接 delta 里的 `tool_calls` 片段），最终仍是 `LLMResponse`。

---

### 5. 再喂给模型：assistant 带 `tool_calls`，tool 角色带结果

当 `response.has_tool_calls` 为真时，runner 把助手消息（含 `tool_calls`）追加进历史，再执行工具，并为每个调用追加 **`role: "tool"`** 且带 **`tool_call_id`** 的消息——这是 OpenAI 多轮 function calling 约定，下一轮模型才能对上号：

```276:323:e:\githubrepository\bot\minibot\minibot\agent\runner.py
            if response.has_tool_calls:
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                tools_used.extend(tc.name for tc in response.tool_calls)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [tc.to_openai_tool_call() for tc in response.tool_calls],
                    },
                )

                await hook.before_execute_tools(context)

                results, new_events, fatal_error = await self._execute_tools(
                    spec,
                    response.tool_calls,
                    external_lookup_counts,
                )
                tool_events.extend(new_events)
                context.tool_results = list(results)
                context.tool_events = list(new_events)
                completed_tool_results: list[dict[str, Any]] = []
                for tool_call, result in zip(response.tool_calls, results):
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": self._normalize_tool_result(
                            spec,
                            tool_call.id,
                            tool_call.name,
                            result,
                        ),
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)
```

`ToolCallRequest.to_openai_tool_call()`（在 `base.py`）负责把内部结构再序列化成 API 认识的 `tool_calls` 条目，保证下一轮请求格式正确。

---

### 6. 执行工具：`prepare_call` 校验 + `execute`

`_run_tool` 里先用注册表的 **`prepare_call(name, arguments)`** 做解析/校验，再 `await tool.execute(**params)` 或 `spec.tools.execute(...)`：

```679:704:e:\githubrepository\bot\minibot\minibot\agent\runner.py
        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            try:
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
            except Exception:
                pass
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            await _maybe_observe(event)
            return (
                prep_error + _HINT,
                event,
                RuntimeError(prep_error) if spec.fail_on_tool_error else None,
            )
        try:
            if tool is not None:
                result = await tool.execute(**params)
            else:
                result = await spec.tools.execute(tool_call.name, params)
```

**小结**：API 只负责「说要调哪个函数、参数 JSON 是什么」；**真正执行**在 runner + `ToolRegistry` + 各 `Tool.execute` 里完成，结果再以 **`tool` 消息**形式闭合这一轮循环。

---

### 7. 与 Anthropic 的关系（简要）

`AnthropicProvider` 走的是 **Claude Messages API**（不是 `chat.completions`），但上层仍统一成 `LLMResponse` / `ToolCallRequest`，runner 这段 **「有 tool_calls → 执行 → 追加 tool 消息」** 的流程不变。若你需要，可以再单独展开 `anthropic_provider.py` 里 `tools` 参数与 `tool_use` 块的转换。

---

**一句话串起来**：**`Tool.to_schema()` + `get_definitions()`** 生成 **`tools` 列表** → **`OpenAICompatProvider.chat`** 调用 **`chat.completions.create`**（或 Responses 分支）→ **`_parse`** 得到 **`ToolCallRequest`** → **`AgentRunner`** 写回 **assistant `tool_calls` + tool 结果**，并进入下一轮直到模型不再请求工具。