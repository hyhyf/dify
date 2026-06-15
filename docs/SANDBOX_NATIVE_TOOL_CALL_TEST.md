# Sandbox 工具原生 Function Call — 测试方案

## 1. 测试环境

| 参数 | 值 |
|------|-----|
| Dify 实例 | `http://100.66.1.5` |
| Console 账号 | `chuanzegao@163.com` |
| Console 密码 | `2wsx@WSX` (Base64 编码后传输) |
| 测试 App ID | `adb8757a-d992-4cf0-b79a-8667d85d5179` |
| API Key | `app-QRV6mj3wjhcrmCjSY8fuXtIa` |

### Console API 认证流程

```
1. POST /console/api/login  {"email":"...", "password":"<BASE64>"}  → cookies
2. 提取 csrf_token 从 cookies
3. 所有后续请求携带 Header: X-CSRF-Token + Cookie
```

### E2E 测试 API 调用链

```
1. POST /console/api/apps/{app-id}/api-keys {} → 获取 API Key
2. POST /v1/chat-messages (Bearer app-xxx) → 触发工作流
3. GET /console/api/apps/{app-id}/workflow-runs → 获取 run_id
4. GET /console/api/apps/{app-id}/workflow-runs/{run_id}/node-executions → 验证
```

## 2. 单元测试

### 2.1 `test_replacers.py` — 纯函数，无外部依赖

**文件**: `api/tests/unit_tests/core/skill/assembler/test_replacers.py`

| # | 用例名 | 输入 | 预期结果 |
|---|--------|------|----------|
| R1 | `test_tool_replacer_normal` | `§[tool].[provider].[tool_name].[uuid]§` | `[Tool: tool_name]` |
| R2 | `test_tool_replacer_disabled` | enabled=False 的工具 | `""` (空字符串) |
| R3 | `test_tool_replacer_not_found` | 不存在的 uuid | `[Tool not found or disabled: uuid]` |
| R4 | `test_tool_group_replacer_all_enabled` | `[§[tool]...§, §[tool]...§]` | `[Tool: n1, Tool: n2]` |
| R5 | `test_tool_group_replacer_all_disabled` | 全部 disabled | `""` |
| R6 | `test_tool_group_replacer_mixed` | 部分 disabled | 仅 enabled 的工具 |

#### 测试数据构造

```python
from core.skill.assembler.replacers import ToolReplacer, ToolGroupReplacer
from core.skill.entities.skill_metadata import SkillMetadata, ToolReference
from core.tools.entities.tool_entities import ToolProviderType

def _make_metadata(tools: list[ToolReference]) -> SkillMetadata:
    return SkillMetadata(tools={ref.uuid: ref for ref in tools})

def _make_tool_ref(uuid: str, name: str, enabled: bool = True) -> ToolReference:
    return ToolReference(
        uuid=uuid,
        type=ToolProviderType.MCP,
        provider="test_provider",
        tool_name=name,
        enabled=enabled,
    )
```

### 2.2 `test_sandbox_tools.py` — Mock ToolManager + Sandbox

**文件**: `api/tests/unit_tests/dify_graph/nodes/llm/test_sandbox_tools.py`

| # | 用例名 | 验证点 |
|---|--------|--------|
| S1 | `test_empty_dependencies` | 空 ToolDependencies → 返回 `[]` |
| S2 | `test_none_dependencies` | None → 返回 `[]` |
| S3 | `test_disabled_tools_skipped` | enabled=False → 不包含在结果中 |
| S4 | `test_mcp_tool_conversion` | MCP 类型 → ToolManager 收到正确 provider_type |
| S5 | `test_credential_id_from_ref` | ToolReference 含 credential_id → 传递给 ToolManager |
| S6 | `test_default_values_applied` | configuration.default_values() → runtime_parameters 更新 |
| S7 | `test_tool_resolution_error` | ToolManager 抛异常 → 跳过该工具，记录 warning |
| S8 | `test_native_tools_passed_to_strategy` | tool_dependencies 含 2 工具 → create_strategy 收到 tools=[t1, t2, bash_tool] |

#### Mock 策略

```python
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from core.tools.entities.tool_entities import ToolProviderType, ToolInvokeFrom
from core.skill.entities.tool_dependencies import ToolDependencies, ToolDependency, ToolReference
from core.skill.entities.skill_metadata import ToolReference as SkillToolRef

@pytest.fixture
def mock_tool_manager():
    with patch("dify_graph.nodes.llm.node.ToolManager") as mock:
        mock.get_tool_runtime.return_value = MagicMock()
        yield mock

@pytest.fixture
def mock_sandbox():
    sandbox = MagicMock()
    sandbox.vm = MagicMock()
    sandbox.vm.get_working_path.return_value = "/sandbox/test"
    sandbox.wait_ready = MagicMock()
    sandbox.tenant_id = "test-tenant"
    sandbox.user_id = "test-user"
    sandbox.app_id = "test-app"
    sandbox.assets_id = "test-assets"
    sandbox.id = "test-sandbox-id"
    return sandbox

@pytest.fixture
def llm_node_patched(mock_sandbox):
    from dify_graph.nodes.llm.node import LLMNode
    # 使用最小的 mock 构造节点
    ...
```

### 2.3 `test_session.py` — Mock Sandbox VM

**文件**: `api/tests/unit_tests/core/sandbox/bash/test_session.py`

| # | 用例名 | 验证点 |
|---|--------|--------|
| B1 | `test_skips_cli_when_tools_none` | tools=None → 使用 global_tools_path，不创建 CliApiSession |
| B2 | `test_skips_cli_when_tools_empty` | tools 的 is_empty()=True → 同上 |
| B3 | `test_sets_up_when_tools_present` | tools 含依赖 → 调用 _setup_node_tools_directory |
| B4 | `test_collect_output_files` | output/ 下有文件 → 返回 File 列表 |
| B5 | `test_session_cleanup` | __exit__ → CliApiSessionManager.delete() 被调用 |
| B6 | `test_bash_tool_accessible` | 进入上下文 → session.bash_tool 可用 |

## 3. 端到端测试

### 3.1 `test_sandbox_tool_native_call_e2e.py`

**文件**: `api/tests/e2e/test_sandbox_tool_native_call_e2e.py`

独立运行的 Python 脚本，通过 HTTP API 端到端验证。

#### 运行方式

```bash
python3 api/tests/e2e/test_sandbox_tool_native_call_e2e.py
```

#### 测试流程

```
Phase 1: 准备
  1.1 登录 Console API (POST /console/api/login)
  1.2 获取/刷新 API Key (POST /console/api/apps/{app_id}/api-keys)

Phase 2: 执行（改造后）
  2.1 触发工作流 (POST /v1/chat-messages, query="获取麦当劳门店")
  2.2 等待完成 (blocking mode)

Phase 3: 验证
  3.1 获取 workflow-runs 列表
  3.2 获取 node-executions
  3.3 解析 execution_metadata.llm_trace
  3.4 执行检查点
```

#### 验证检查点 (6 项)

| # | 检查点 | 检查目标 | 判定标准 |
|---|--------|----------|----------|
| E1 | `native_tool_calls_present` | 至少存在一个原生 tool_call | `name != "bash"` 出现在 traces 中 |
| E2 | `no_executable_hint` | Prompt 不含 Executable | `"[Executable:" not in prompt` |
| E3 | `no_command_not_found` | 无 bash 错误 | `"command not found" not in output` |
| E4 | `no_param_name_error` | 无参数名错误 | `status != "error"` |
| E5 | `correct_result` | 返回正确结果 | answer 含期望内容 |
| E6 | `latency_check` | 总耗时合理 | 与基线 27.25s 对比（注：仍走沙箱，收益来自减少 LLM 探索轮次）|

#### 脚本核心类

```python
class DifyConsoleClient:
    """Console API 客户端，处理登录 + CSRF"""
    def __init__(self, base_url, email, password):
        ...
    def login(self) -> None: ...
    def get_api_key(self, app_id: str) -> str: ...
    def get_workflow_runs(self, app_id: str) -> list: ...
    def get_node_executions(self, app_id: str, run_id: str) -> list: ...

class VerificationResult:
    """单检查点结果"""
    check_id: str
    passed: bool
    detail: str

def verify_tool_calls_are_native(executions: list) -> VerificationResult:
    """E1: 检查所有 tool_call name 不为 'bash'"""
    ...

def verify_no_executable_hint(executions: list) -> VerificationResult:
    """E2: 检查 prompt 不含 [Executable:"""
    ...

def verify_no_command_not_found(executions: list) -> VerificationResult:
    """E3: 检查输出无 command not found"""
    ...

def verify_no_param_errors(executions: list) -> VerificationResult:
    """E4: 检查无参数错误"""
    ...

def verify_correct_answer(response: dict) -> VerificationResult:
    """E5: 检查返回结果正确"""
    ...

def verify_latency(elapsed: float, baseline: float = 27.25) -> VerificationResult:
    """E6: 检查延迟降低"""
    ...
```

## 4. 测试执行顺序

```
Phase A — 快速反馈 (< 5s)
  A1. test_replacers.py       # 纯函数，无依赖

Phase B — 单元集成 (< 30s)
  B1. test_session.py          # mock 沙箱
  B2. test_sandbox_tools.py    # mock ToolManager + Sandbox

Phase C — 回归保护 (< 2min)
  C1. 现有 test_llm.py         # 确认未破坏现有 LLM 节点

Phase D — 端到端验证 (需运行中的 Dify)
  D1. test_sandbox_tool_native_call_e2e.py

Phase E — 质量检查
  E1. make lint
  E2. make type-check
```

## 5. 预期结果

### 改前基线 (已采集)

```
llm_trace:
  [model] → tool_call: name="bash", args={"command": "cat skills/..."}  ← ERROR (参数名错误)
  [tool]  → "bash"  → Missing required parameter: bash
  [model] → tool_call: name="bash", args={"bash": "cat skills/..."}     ← OK
  [tool]  → "bash" → 0.2855s → skill 内容含 [Executable: ...]
  [model] → tool_call: name="bash", args={"bash": "getCityCoordinates_uuid --help"}  ← OK
  [tool]  → "bash" → 0.2849s → 工具帮助信息
  [model] → 回复文本
总耗时: 27.25s, tool 耗时: ~0.57s (2 次成功)
```

### 改后预期

```
llm_trace:
  [model] → tool_call: name="getCityCoordinates", args={"name":"深圳"}     ← 原生 function call
  [tool]  → "getCityCoordinates" → SandboxNativeToolWrapper
              → bash: "getCityCoordinates_uuid --name 深圳"
              → SandboxBashTool → 沙箱 dify-cli → ~0.28s → 坐标数据
  [model] → tool_call: name="query-nearby-stores", args={...}              ← 原生 function call
  [tool]  → "query-nearby-stores" → Wrapper → bash → dify-cli → 门店列表
  [model] → 回复含门店信息
预期收益: 无参数名错误、无 LLM 探索工具名的轮次、prompt 更精简
注: tool 执行延迟仍 ~0.28s（仍走沙箱 dify-cli）
```

## 6. 失败处理

| 场景 | 处理方式 |
|------|----------|
| E1 失败 (仍有 bash tool_call) | 检查 `_invoke_llm_with_sandbox` 是否正确传入了 native tools |
| E2 失败 (仍有 Executable hint) | 检查 `replacers.py` 修改是否生效，skill bundle 是否重建 |
| E3 失败 (command not found) | diff-cli 未彻底移除，检查 session.py 是否跳过 init |
| E4 失败 (参数错误) | FunctionCallStrategy JSON Schema 生成是否正确 |
| E5 失败 (结果错误) | 检查 ToolManager.get_tool_runtime 是否正确解析工具 |
| E6 失败 (延迟未降) | 可能其他因素占主导 (网络、模型推理)，不作为阻塞项 |
