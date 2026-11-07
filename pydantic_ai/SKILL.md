---
name: pydantic_ai
description: Pydantic AI — building LLM agents with Python. Use when working with Agent, AgentSpec, tools, capabilities, hooks, output modes, MCP, streaming, multi-agent, or message history.
---

## pydantic_ai

Build LLM-powered agents with Pydantic AI. Core concepts: agents, dependencies, tools, output, capabilities, hooks, specs, message history, direct API, MCP.

### Custom Provider Pattern (always use this)

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    "model-name",
    provider=OpenAIProvider(
        base_url=config.base_url,
        api_key=config.api_key,
    ),
)
```
Applies to all providers (OpenAI, Anthropic, etc.). Use model-level settings for defaults, agent-level for overrides, run-time for per-call.

### Agent Construction & Running

```python
from pydantic_ai import Agent

agent = Agent(
    'openai:gpt-5.2',           # model (or OpenAIChatModel instance)
    deps_type=MyDeps,            # dependency dataclass type
    output_type=MyOutput,        # structured output (PydanticModel, dataclass, TypedDict)
    system_prompt='...',         # static or @agent.system_prompt decorator
    instructions='...',          # dynamic instructions
    capabilities=[...],          # composable behavior bundles
    name='my_agent',
    retries={'tools': 3, 'output': 1},
    end_strategy='early',        # 'early' | 'graceful' | 'exhaustive'
)
```

Five run modes:
- `agent.run(prompt, deps=...)` — async, returns `AgentRunResult`
- `agent.run_sync(prompt, deps=...)` — sync wrapper
- `agent.run_stream(prompt, deps=...)` — async context manager, returns `StreamedRunResult`
- `agent.run_stream_events(prompt, deps=...)` — yields `AgentStreamEvent`s + `AgentRunResultEvent`
- `agent.iter(prompt, deps=...)` — async iterable over graph nodes (`UserPromptNode`, `ModelRequestNode`, `CallToolsNode`, `End`)

Streaming:
```python
async with agent.run_stream(prompt) as result:
    async for text in result.stream_text():    # text deltas
        ...
    async for output in result.stream_output(): # structured output deltas
        ...
    async for response in result.stream_response(): # full ModelResponse deltas
        ...
    final = await result.get_output()           # blocking final output
```

Usage limits and model settings (3-level precedence: model defaults < agent defaults < run-time overrides):
```python
from pydantic_ai import UsageLimits, ModelSettings

result = agent.run_sync(
    prompt,
    usage_limits=UsageLimits(response_tokens_limit=100, request_limit=5, tool_calls_limit=3),
    model_settings=ModelSettings(temperature=0.5, max_tokens=2048, timeout=30),
)
```

### Dependencies & Context

```python
from dataclasses import dataclass
from pydantic_ai import RunContext

@dataclass
class MyDeps:
    api_key: str
    http_client: httpx.AsyncClient

@agent.tool
async def my_tool(ctx: RunContext[MyDeps], query: str) -> str:
    return await ctx.deps.http_client.get(query)

# Access: ctx.deps, ctx.agent, ctx.messages, ctx.run_step, ctx.usage, ctx.enqueue, ctx.partial_output
```

Override for testing:
```python
class TestDeps(MyDeps): ...
test_deps = TestDeps('test-key', None)
with agent.override(deps=test_deps):
    result = agent.run_sync('hello')  # uses test_deps
```

### Function Tools

```python
@agent.tool_plain                    # no context needed
def roll_dice() -> str:
    """Roll a six-sided die."""
    return str(random.randint(1, 6))

@agent.tool                          # receives RunContext as first arg
async def lookup(ctx: RunContext[MyDeps], name: str) -> str:
    return await ctx.deps.db.lookup(name)

# Via constructor: tools=[roll_dice, Tool(lookup, takes_ctx=True)]
```
Docstring param descriptions extracted from google/numpy/sphinx styles. Single-parameter tools with object types (dataclass/Pydantic/TypedDict) get flattened schemas.

### Tools & Toolsets

```python
from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

toolset = FunctionToolset()
@toolset.tool_plain
def search(q: str) -> str:
    return "results"

agent = Agent('openai:gpt-5.2', toolsets=[toolset])

# Wrapper for cross-cutting behavior
class LoggingToolset(WrapperToolset):
    async def call_tool(self, name, args, *a, **kw):
        print(f'Calling {name}')
        return await super().call_tool(name, args, *a, **kw)
```

Advanced tools: deferred (`requires_approval=True`), native tools, common tools, third-party tools. Dynamic tool choice via `prepare_tools` / `PrepareTools` capability.

### Output Modes

Plain text (default), structured output, or output functions:
```python
from pydantic import BaseModel
from pydantic_ai import Agent, ToolOutput, NativeOutput, PromptedOutput, TextOutput

# Structured
agent = Agent('openai:gpt-5.2', output_type=CityLocation)

# Multiple output types (registered as separate output tools)
agent = Agent('openai:gpt-5.2', output_type=[Fruit, Vehicle])

# Custom tool names
agent = Agent('openai:gpt-5.2', output_type=[
    ToolOutput(Fruit, name='return_fruit'),
    ToolOutput(Vehicle, name='return_vehicle', max_retries=2),
])

# Native output (model's native structured output / JSON schema)
agent = Agent('openai:gpt-5.2', output_type=NativeOutput([Fruit, Vehicle]))

# Prompted output (schema in instructions, parse text response)
agent = Agent('openai:gpt-5.2', output_type=PromptedOutput([Vehicle, Device]))

# Text output function (model returns plain text, function processes it)
agent = Agent('openai:gpt-5.2', output_type=TextOutput(split_into_words))

# Output functions (model calls function to end run)
agent = Agent('openai:gpt-5.2', output_type=[run_sql_query, SQLFailure])

# StructuredDict for external JSON schemas
HumanDict = StructuredDict({
    'type': 'object',
    'properties': {'name': {'type': 'string'}, 'age': {'type': 'integer'}},
    'required': ['name', 'age']
}, name='Human', description='A human with a name and age')
```

Output validators:
```python
@agent.output_validator
async def validate_sql(ctx: RunContext[DatabaseConn], output: Output) -> Output:
    if isinstance(output, InvalidRequest):
        return output
    try:
        await ctx.deps.execute(f'EXPLAIN {output.sql_query}')
    except QueryError as e:
        raise ModelRetry(f'Invalid query: {e}') from e
    return output
```
Check `ctx.partial_output` in streaming to skip side effects on partials.

### Capabilities

Reusable composable bundles of tools, hooks, instructions, model settings:
```python
from pydantic_ai.capabilities import Thinking, WebSearch, Hooks, MCP, ProcessHistory

agent = Agent(
    'anthropic:claude-opus-4-6',
    capabilities=[
        Thinking(effort='high'),
        WebSearch(local='duckduckgo'),
        WebFetch(local=True),
        ImageGeneration(fallback_model='openai-responses:gpt-5.4'),
        XSearch(fallback_model='xai:grok-4-1-fast-non-reasoning'),
        MCP(url='https://mcp.example.com/api', native=True),
        Hooks(before_model_request=log_request),
        ProcessHistory(keep_recent_messages),
    ],
)
```

Native capabilities: `Thinking`, `Hooks`, `Instrumentation`, `WebSearch`, `WebFetch`, `ImageGeneration`, `XSearch`, `MCP`, `ToolSearch`, `PrepareTools`, `PrepareOutputTools`, `PrefixTools`, `NativeTool`, `Toolset`, `IncludeToolReturnSchemas`, `SetToolMetadata`, `ReinjectSystemPrompt`, `ProcessHistory`, `ProcessEventStream`, `ThreadExecutor`, `HandleDeferredToolCalls`, `OpenAICompaction`, `AnthropicCompaction`.

Building custom capabilities:
```python
from dataclasses import dataclass
from typing import Any
from pydantic_ai.capabilities import AbstractCapability

@dataclass
class MathTools(AbstractCapability[Any]):
    def get_toolset(self) -> AgentToolset[Any] | None:
        return math_toolset

    def get_native_tools(self) -> Sequence[AgentNativeTool]:
        return [...]

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        return LoggingToolset(wrapped=toolset)

    def get_instructions(self):
        def _get_time(ctx: RunContext[Any]) -> str:
            return f'The time is {datetime.now().isoformat()}.'
        return _get_time

    def get_model_settings(self):
        def resolve(ctx: RunContext[None]) -> ModelSettings:
            if ctx.run_step > 1:
                return ModelSettings(thinking='high')
            return ModelSettings()
        return resolve

    # Lifecycle hooks: before_run, after_run, before_node_run, after_node_run,
    # before_model_request, after_model_request, before_tool_validate,
    # after_tool_validate, before_tool_execute, after_tool_execute,
    # before_output_validate, after_output_validate, before_output_process,
    # after_output_process, prepare_tools, prepare_output_tools,
    # deferred_tool_calls, run_event_stream, on_*_error
```

### Hooks

Decorator or constructor registration:
```python
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse
from pydantic_ai.exceptions import ModelRetry

hooks = Hooks()

@hooks.on.before_model_request
async def log_request(ctx, request_context):
    print(f'Sending {len(request_context.messages)} messages')
    return request_context

@hooks.on.after_model_request(timeout=5.0, tools=['weather_forecast'])
async def check_response(ctx, request_context, response: ModelResponse):
    if 'PLACEHOLDER' in str(response.parts):
        raise ModelRetry('Response contains placeholder text.')
    return response

@hooks.on.before_tool_execute(tools=['send_email'])
async def audit_tool(ctx, call, tool_def, args):
    print(f'Auditing: {call.tool_name}')
    return args

@hooks.on.model_request  # wrap hook
async def timed_request(ctx, request_context, handler):
    start = time.time()
    response = await handler(request_context)
    print(f'Request took {time.time() - start:.2f}s')
    return response

agent = Agent('test', capabilities=[hooks])
```

Hook types: `before_*`, `after_*`, `wrap_*`, `*_error`. Wrap hooks nest as middleware. `after_*` fires in reverse order. Raise-to-propagate / return-to-recover for error hooks.

### Agent Specs (YAML/JSON)

```yaml
# agent.yaml
model: anthropic:claude-opus-4-6
name: research-assistant
instructions: You are a helpful research assistant.
model_settings:
  max_tokens: 8192
capabilities:
  - WebSearch:
      local: duckduckgo
  - Thinking:
      effort: high
```

```python
from pydantic_ai import Agent

agent = Agent.from_file('agent.yaml')

# Or with additional kwargs
agent = Agent.from_spec(spec_dict, deps_type=UserContext, model=my_model)

# Save with JSON schema for editor autocompletion
from pydantic_ai import AgentSpec
spec = AgentSpec(
    model='anthropic:claude-opus-4-6',
    instructions='You are a helpful assistant.',
    capabilities=[{'WebSearch': {'local': 'duckduckgo'}}],
)
spec.to_file('agent.yaml')
```

Spec fields: `model`, `name`, `description`, `instructions`, `model_settings`, `capabilities`, `deps_schema`, `output_schema`, `retries`, `end_strategy`, `tool_timeout`, `instrument`, `metadata`.

Template strings in YAML: `"You are assisting {{name}}, who is a {{role}}."` — rendered against deps fields at runtime.

Capability spec syntax: `'MyCapability'`, `{'MyCapability': value}`, `{'MyCapability': {k: v}}`.

### Your Agent Loading Pattern

```python
from pathlib import Path
from typing import Any
from pydantic_ai import Agent, AgentSpec
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

_AGENT_CACHE: dict[str, Agent] = {}
CUSTOM_MODEL_PREFIX = "custom:"

def get_model(model_name: str | None = None) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name or config.model_name,
        provider=OpenAIProvider(base_url=config.base_url, api_key=config.api_key),
    )

def resolve_model(model_ref: str) -> OpenAIChatModel | str:
    if model_ref.startswith(CUSTOM_MODEL_PREFIX):
        model_name = model_ref.removeprefix(CUSTOM_MODEL_PREFIX).strip()
        if not model_name:
            raise ValueError(f"Invalid custom model reference: {model_ref!r}. Expected format: custom:model_name")
        return get_model(model_name)
    return model_ref

def load_agent_from_file(spec_name: str, **agent_kwargs: Any) -> Agent:
    spec_path = Path(__file__).parent.parent / 'models' / 'llm' / spec_name
    if not spec_path.suffix:
        spec_path = spec_path.with_suffix('.yaml')
    spec = AgentSpec.from_file(spec_path)
    return Agent.from_spec(
        spec,
        model=resolve_model(spec.model or config.model_name),
        custom_capability_types=list(CAPABILITY_REGISTRY.values()),
        **agent_kwargs,
    )

def load_agent(profile_name: str, **agent_kwargs: Any) -> Agent:
    if profile_name not in _AGENT_CACHE:
        _AGENT_CACHE[profile_name] = load_agent_from_file(profile_name, **agent_kwargs)
    return _AGENT_CACHE[profile_name]
```

### Message History

```python
# Access messages
result = agent.run_sync('hello')
all_msgs = result.all_messages()     # all including prior runs
new_msgs = result.new_messages()     # only current run
all_json = result.all_messages_json()

# Continue conversation
result2 = agent.run_sync('follow up', message_history=result.new_messages())

# Correlate runs
assert result.conversation_id == result2.conversation_id  # shared across message_history

# Fork conversation
result3 = agent.run_sync('different path', message_history=result.all_messages(), conversation_id='new')

# Serialize/deserialize
from pydantic_ai import ModelMessagesTypeAdapter
as_python = to_jsonable_python(result.all_messages())
same_history = ModelMessagesTypeAdapter.validate_python(as_python)

# Enqueue messages mid-run (from tool/hook)
ctx.enqueue('Alert: production is degraded.')  # priority='asap' (default)
ctx.enqueue('Handle this when idle', priority='when_idle')

# Enqueue from external code driving iter()
async with agent.iter(prompt) as agent_run:
    agent_run.enqueue('new task', priority='when_idle')
    while not isinstance(agent_run.next_node, End):
        agent_run = await agent_run.next(agent_run.next_node)
```

History processing:
```python
from pydantic_ai.capabilities import ProcessHistory

async def keep_recent(messages):
    return messages[-5:] if len(messages) > 5 else messages

async def context_aware_processor(ctx, messages):
    if ctx.usage.total_tokens > 1000:
        return messages[-3:]
    return messages

agent = Agent('openai:gpt-5.2', capabilities=[
    ProcessHistory(keep_recent),
    ProcessHistory(context_aware_processor),
])
```

### Direct Model API

Low-level model access without agent abstraction:
```python
from pydantic_ai import ModelRequest, ToolDefinition
from pydantic_ai.direct import model_request, model_request_sync
from pydantic_ai.models import ModelRequestParameters

# Basic
response = model_request_sync(
    'anthropic:claude-haiku-4-5',
    [ModelRequest.user_text_prompt('What is the capital of France?')]
)

# With tools
response = await model_request(
    'openai:gpt-5-nano',
    [ModelRequest.user_text_prompt('What is 123 / 456?')],
    model_request_parameters=ModelRequestParameters(
        function_tools=[ToolDefinition(
            name='divide',
            description='Divide two numbers.',
            parameters_json_schema=Divide.model_json_schema(),
        )],
        allow_text_output=True,
    ),
)

# Instrumented
response = model_request_sync('anthropic:claude-haiku-4-5', [...], instrument=True)
```

### MCP Integration

Standard MCP:
```python
from pydantic_ai.capabilities import MCP

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[MCP(url='https://mcp.example.com/api', native=True)],
)
```

FastMCP Client (preferred for local/dev):
```python
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

# From Streamable HTTP URL
toolset = FastMCPToolset('http://localhost:8000/mcp')

# From FastMCP server instance (same codebase, no network round-trip)
from fastmcp import FastMCP
fastmcp_server = FastMCP('my_server')
@fastmcp_server.tool()
async def add(a: int, b: int) -> int:
    return a + b
toolset = FastMCPToolset(fastmcp_server)

# From MCP config JSON
toolset = FastMCPToolset({
    'mcpServers': {
        'time_server': {'command': 'uvx', 'args': ['mcp-run-python', 'stdio']},
        'weather_server': {'command': 'python', 'args': ['mcp_server.py']},
    }
})

# From Python/Node.js script
toolset = FastMCPToolset('my_server.py')
toolset = FastMCPToolset('my_server.js')

# From FastMCP Client or Transport
toolset = FastMCPToolset(fastmcp.Client(...))
toolset = FastMCPToolset(fastmcp.StdioTransport(command='python', args=['mcp_server.py']))

agent = Agent('openai:gpt-5.2', toolsets=[toolset])
```
FastMCPToolset does not support integration elicitation or sampling (use standard `MCPServer` for those).

### Multi-Agent Patterns

Router with output function handoff:
```python
from pydantic_ai import Agent, RunContext, ModelRetry, UnexpectedModelBehavior

sql_agent = Agent('openai:gpt-5.2', output_type=[Row, SQLFailure])

async def hand_off_to_sql_agent(ctx: RunContext, query: str) -> list[Row]:
    messages = ctx.messages[:-1]  # drop output tool call
    try:
        result = await sql_agent.run(query, message_history=messages)
        output = result.output
        if isinstance(output, SQLFailure):
            raise ModelRetry(f'SQL agent failed: {output.explanation}')
        return output
    except UnexpectedModelBehavior as e:
        if (cause := e.__cause__) and isinstance(cause, ModelRetry):
            raise ModelRetry(f'SQL agent failed: {cause.message}') from e
        raise

router_agent = Agent('openai:gpt-5.2', output_type=[hand_off_to_sql_agent, RouterFailure])
result = router_agent.run_sync('Select all users')
```

### Testing

```python
from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

# FunctionModel: capture model requests
received: list[ModelMessage] = []

def capture_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    received.clear()
    received.extend(messages)
    return ModelResponse(parts=[TextPart('test response')])

agent = Agent(FunctionModel(capture_model))
agent.run_sync('hello')
assert received[0].parts[0].content == 'hello'

# TestModel: deterministic, inspect parameters
test_model = TestModel()
agent = Agent(test_model)
agent.run_sync('hello')
params = test_model.last_model_request_parameters
assert params is not None
assert params.function_tools[0].name == 'expected_tool'

# Override deps for testing
class TestDeps(MyDeps): ...
with agent.override(deps=TestDeps('test', None)):
    ...
```

### Installation

```bash
# Full install
pip install pydantic-ai

# Slim install (recommended — pick extras you need)
pip install "pydantic-ai-slim[openai]"
pip install "pydantic-ai-slim[anthropic]"
pip install "pydantic-ai-slim[fastmcp]"    # for FastMCPToolset
pip install "pydantic-ai-slim[web-search]" # for WebSearch DuckDuckGo fallback
pip install "pydantic-ai-slim[web-fetch]"  # for WebFetch local fallback
```

### Common Patterns
- Agent as global singleton (like FastAPI App)
- YAML-driven agent profiles with caching
- Custom OpenAI-compatible providers via `OpenAIProvider(base_url=..., api_key=...)`
- `RunContext.enqueue()` for mid-run steering
- `ProcessHistory` for token budget management
- `ModelRetry` for self-correction loops
- `WrapperToolset` for cross-cutting tool behavior
- `ReinjectSystemPrompt` for UI/database conversation resumption
