# Sandbox 工具原生 Function Call 改造方案

## 1. 目标

消除 `computer_use` 模式下自定义工具通过 `bash → sandbox VM → dify-cli → HTTP 回调` 的层级调用链，
改为通过 LLM 原生 function call 机制直接调用，提升效率并降低延迟。

### 改造前后对比

```
改造前 (5 层):
LLM → function_call("bash", {bash: "tool_name --args"})
    → SandboxBashTool._invoke()
    → bash -c 命令 (sandbox VM)
    → dify-cli 二进制 (HTTP 回调 Dify API)
    → ToolEngine → 实际执行

改造后:
LLM → function_call("tool_name", {arg1: val1})          ← LLM 看到原生 JSON Schema
    → SandboxNativeToolWrapper._invoke()                 ← 适配层
        → 构造 bash: "tool_name_uuid --arg1 val1"
        → SandboxBashTool._invoke({"bash": command})     ← 仍走沙箱
            → bash -c 命令 (sandbox VM)
            → dify-cli 二进制 (HTTP 回调 Dify API)       ← 文件传输保留
            → ToolEngine → 实际执行
```

## 2. 影响范围评估

### 改动及风险评估

| 文件 | 改动性质 | 影响范围 | 风险 |
|------|----------|----------|------|
| `api/dify_graph/nodes/llm/node.py` | 新增方法 + 修改调用 | 仅 `computer_use` 路径 | 低 |
| `api/core/skill/assembler/replacers.py` | 修改输出格式 | 所有 skill 文档处理 | 中 |
| `api/core/sandbox/bash/session.py` | 条件跳过 diff-cli init | 仅 `computer_use` 路径 | 低 |
| `api/core/sandbox/bash/bash_tool.py` | 修改 LLM 描述 | 仅 `computer_use` 路径 | 低 |

### 不改动的组件

| 组件 | 保留原因 |
|------|---------|
| `DifyCliInitializer` | 全局沙箱初始化仍需要 (代码执行/文件操作) |
| `DifyCliConfig` | `DifyCliInitializer` 仍需要 |
| `CliApiSession` (全局) | 全局沙箱工具访问鉴权 |
| CLI API 端点 | 全局沙箱路径仍需要 |
| 前端 `computer_use` 开关 | 功能开关不变，只是内部调用方式优化 |

## 3. 详细实施步骤

### 3.1 `replacers.py` — 移除 Executable 占位符

**文件**: `api/core/skill/assembler/replacers.py`

**变更**:

`ToolReplacer._replace_match()` (line 80):
```python
# 改前
return f"[Executable: {tool_ref.tool_name}_{tool_ref.uuid} --help command]"

# 改后
return f"[Tool: {tool_ref.tool_name}]"
```

`ToolGroupReplacer._replace_match()` (line 104):
```python
# 改前
enabled_renders.append(f"[Executable: {tool_ref.tool_name}_{tool_ref.uuid} --help command]")

# 改后
enabled_renders.append(f"[Tool: {tool_ref.tool_name}]")
```

**原因**: LLM 通过 function call schema 获取工具描述，prompt 中只需标记工具可用，
不再需要 bash 命令行提示。修改为 `[Tool: name]` 对 sandbox/非sandbox 路径均无害。

### 3.2 `bash_tool.py` — 更新 LLM 描述

**文件**: `api/core/sandbox/bash/bash_tool.py`

**变更**: 修改 `ToolDescription.llm` (line 69-74):
```python
llm="Execute bash commands in the sandbox environment. "
"Use bash for file operations, code execution, and system commands. "
"For data-fetching or API tools, prefer using the registered tools directly "
"instead of bash. "
"IMPORTANT: Save output files to the 'output/' directory for collection."
```

### 3.3 `session.py` — 无需修改

`SandboxBashSession` 保持原始行为（`tools=tool_dependencies`），完整的
dify-cli init、CliApiSession、ToolAccessPolicy 均保留。Wrapper 路由的
bash 命令仍通过 dify-cli 执行，依赖完整的沙箱初始化。

### 3.4 `node.py` — 核心改造：新增 SandboxNativeToolWrapper

**文件**: `api/dify_graph/nodes/llm/node.py`

#### 3.4.1 新增 `SandboxNativeToolWrapper(Tool)` 类

位于 LLM 看到的工具（JSON Schema）和实际执行（沙箱 dify-cli）之间的适配层：

```python
class SandboxNativeToolWrapper(Tool):
    """
    LLM 层: to_prompt_message_tool() → 原生 JSON Schema (来自 real_tool)
    执行层: _invoke() → 构造 CLI 命令 → bash_tool._invoke() → 沙箱 dify-cli
    """
    def __init__(self, tool_ref, real_tool, bash_tool):
        # 使用 real_tool 的 entity (identity/description/parameters) 注册给 LLM
        super().__init__(entity=real_tool.entity, runtime=real_tool.runtime)

    def _invoke(self, user_id, tool_parameters, ...):
        # JSON → CLI args: --key value
        command = f"{tool_name}_{uuid} --arg1 val1 --arg2 val2"
        # 直接调 _invoke (绕过 invoke() 的类型转换)
        yield from self._bash_tool._invoke(
            tool_parameters={"bash": command}
        )
```

#### 3.4.2 新增 `_build_sandbox_native_wrappers()`

遍历 `tool_dependencies.dependencies`（已去重），对每个启用的工具：
1. 通过 `ToolManager.get_tool_runtime()` 获取真实 Tool 实例
2. 构建 `SandboxNativeToolWrapper`（包装 tool_ref + real_tool + bash_tool）
3. ToolManager 异常时跳过（记录 warning）

#### 3.4.3 修改 `_invoke_llm_with_sandbox()`

```python
# 恢复传入 tool_dependencies，保留完整的 dify-cli init
with SandboxBashSession(sandbox=sandbox, node_id=self.id, tools=tool_dependencies) as session:
    # 为每个工具构建原生 function call wrapper
    native_wrappers = self._build_sandbox_native_wrappers(tool_dependencies, session.bash_tool)

    strategy = StrategyFactory.create_strategy(
        tools=native_wrappers + [session.bash_tool],  # 原生 wrappers + bash 后备
        agent_strategy=AgentEntity.Strategy.FUNCTION_CALLING,
        ...
    )
```

## 4. 调用链对比

### 改造前
```
LLM 调用 "bash" → SandboxBashTool._invoke(bash="tool_name_uuid --args")
  → 沙箱 VM 执行 bash -c "export PATH=...; dify run tool_name_uuid --args"
  → dify-cli HTTP 回调 /cli-api/invoke/tool
  → PluginToolBackwardsInvocation → ToolEngine.generic_invoke()
  → 实际执行
```
- LLM 看到 1 个 bash 工具 + prompt 中 `[Executable: ...]` 文字提示
- 首次调用常有参数名错误（"command" vs "bash"）

### 改造后
```
LLM 调用 "tool_name" → SandboxNativeToolWrapper._invoke({json args})
  → 构造 bash: "tool_name_uuid --arg1 val1"
  → SandboxBashTool._invoke({"bash": command})
    → 沙箱 VM bash -c → dify-cli → 执行 + 文件传输
```
- LLM 看到 N 个原生工具（JSON Schema），无 `[Executable: ...]` 混淆
- 仍走沙箱 dify-cli，文件传输和 credential 解析均保留
- 收益：消除参数名错误、减少 LLM 探索轮次

## 5. Prompt 内容对比

### 改造前
```
system prompt: "...使用工具 [Executable: getCityCoordinates_bc611610-... --help command] 进行查询"
tool_calls: [{"function": {"name": "bash", "arguments": {"bash": "getCityCoordinates_bc611610-... --help"}}}]
```

### 改造后
```
system prompt: "...使用工具 [Tool: getCityCoordinates] 进行查询"
tool_calls: [{"function": {"name": "getCityCoordinates", "arguments": {"name": "深圳"}}}]
```

### 改造前 E2E 基线数据

| 指标 | 值 |
|------|-----|
| 总耗时 | 27.25s |
| LLM 轮次 | 4 次 |
| Tool 轮次 | 3 次 (全部走 bash) |
| 首次调用参数错误 | ✅ (用了 "command" 而非 "bash") |
| Tool 每次耗时 | ~0.28s (bash + dify-cli + HTTP) |
| prompt token | 144 |

## 6. 部署 (Docker)

```bash
# 文件同步至 3 个容器
for container in docker-api-1 docker-worker-1 docker-worker_beat-1; do
  docker cp api/dify_graph/nodes/llm/node.py           $container:/app/api/dify_graph/nodes/llm/node.py
  docker cp api/core/skill/assembler/replacers.py       $container:/app/api/core/skill/assembler/replacers.py
  docker cp api/core/sandbox/bash/bash_tool.py          $container:/app/api/core/sandbox/bash/bash_tool.py
done

# 重启生效
docker restart docker-api-1 docker-worker-1 docker-worker_beat-1

# E2E 验证
python3 api/tests/e2e/test_sandbox_tool_native_call_e2e.py
```

## 7. 回滚方案

如需回滚:
1. `replacers.py` — 恢复 `[Executable: ...]` 输出
2. `node.py` — 恢复 `tools=[session.bash_tool]` + 传原始 `tool_dependencies` 给 Session
3. `bash_tool.py` — 恢复原始 LLM 描述
4. `session.py` — 如未修改则无需回滚
