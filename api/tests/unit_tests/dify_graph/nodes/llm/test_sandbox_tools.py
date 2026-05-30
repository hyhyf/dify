"""Unit tests for SandboxNativeToolWrapper and _build_sandbox_native_wrappers.

Verifies:
- Wrapper exposes real tool schema to LLM via native function calling
- Wrapper._invoke translates JSON parameters to CLI args
- Wrapper delegates execution through sandbox bash→dify-cli
"""

from unittest.mock import MagicMock, patch

import pytest

from core.skill.entities.skill_metadata import ToolReference
from core.skill.entities.tool_dependencies import ToolDependencies, ToolDependency
from core.tools.entities.tool_entities import (
    I18nObject,
    ToolDescription,
    ToolEntity,
    ToolIdentity,
    ToolInvokeMessage,
    ToolParameter,
    ToolProviderType,
)
from dify_graph.nodes.llm.node import SandboxNativeToolWrapper

# ── Helpers ────────────────────────────────────────────────────────


def _make_tool_entity(name: str, description: str = "A test tool") -> ToolEntity:
    return ToolEntity(
        identity=ToolIdentity(
            author="test",
            name=name,
            label=I18nObject(en_US=name),
            provider="sandbox",
        ),
        parameters=[
            ToolParameter.get_simple_instance(
                name="query",
                llm_description="Search query",
                typ=ToolParameter.ToolParameterType.STRING,
                required=True,
            ),
            ToolParameter.get_simple_instance(
                name="limit",
                llm_description="Result limit",
                typ=ToolParameter.ToolParameterType.NUMBER,
                required=False,
            ),
        ],
        description=ToolDescription(
            human=I18nObject(en_US=description),
            llm=description,
        ),
    )


@pytest.fixture
def mock_bash_tool() -> MagicMock:
    bash = MagicMock()
    bash._invoke.return_value = iter([MagicMock(spec=ToolInvokeMessage)])
    bash.entity = MagicMock()
    bash.entity.identity.name = "bash"
    bash._sandbox = MagicMock()
    bash._sandbox.get_working_path.return_value = "/workspace/sandboxes/test-sandbox"
    return bash


@pytest.fixture
def mock_self() -> MagicMock:
    self_mock = MagicMock()
    self_mock.tenant_id = "test-tenant"
    self_mock.app_id = "test-app"
    self_mock.invoke_from = MagicMock()
    return self_mock


# ── SandboxNativeToolWrapper tests ─────────────────────────────────


class TestWrapperIdentity:
    """Wrapper exposes real tool's identity as native function call schema."""

    def test_prompt_message_tool_uses_real_identity(self, mock_bash_tool: MagicMock) -> None:
        """Wrapper.to_prompt_message_tool() returns the real tool's name and description."""
        entity = _make_tool_entity("query_stores", "Query nearby stores")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="abc-123",
            type=ToolProviderType.MCP,
            provider="mcp-srv",
            tool_name="query_stores",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        prompt_tool = wrapper.to_prompt_message_tool()
        assert prompt_tool.name == "query_stores"
        assert prompt_tool.description == "Query nearby stores"
        assert "query" in prompt_tool.parameters.get("properties", {})


class TestWrapperInvoke:
    """Wrapper._invoke routes through sandbox bash with correct CLI commands."""

    def test_invoke_string_params(self, mock_bash_tool: MagicMock) -> None:
        """String parameters become '--key value' CLI args."""
        entity = _make_tool_entity("search")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="abc-123",
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="search",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("user-1", {"query": "深圳", "limit": 5}))

        mock_bash_tool._invoke.assert_called_once()
        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "search_abc-123" in command
        assert "--query" in command
        assert "深圳" in command
        assert "--limit 5" in command

    def test_invoke_boolean_param(self, mock_bash_tool: MagicMock) -> None:
        """True boolean → --flag; False → omitted."""
        entity = _make_tool_entity("tool_x")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="uuid-1",
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="tool_x",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"verbose": True, "quiet": False}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "--verbose" in command
        assert "--quiet" not in command

    def test_invoke_list_param(self, mock_bash_tool: MagicMock) -> None:
        """List params become repeated --key value."""
        entity = _make_tool_entity("multi")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="uuid-1",
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="multi",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"tags": ["a", "b", "c"]}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert command.count("--tags") == 3

    def test_invoke_none_params_skipped(self, mock_bash_tool: MagicMock) -> None:
        """None values are omitted from CLI args."""
        entity = _make_tool_entity("s")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="u-1",
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="s",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"query": "test", "optional": None}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "--optional" not in command
        assert "--query" in command

    def test_invoke_empty_uuid_fallback(self, mock_bash_tool: MagicMock) -> None:
        """When UUID is empty, uses provider.tool_name as command suffix."""
        entity = _make_tool_entity("my_tool")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="",  # Empty UUID
            type=ToolProviderType.MCP,
            provider="my_provider",
            tool_name="my_tool",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"query": "test"}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "my_tool_my_provider.my_tool" in command

    def test_invoke_file_ref_stripped(self, mock_bash_tool: MagicMock) -> None:
        """[File: ...] notation is stripped to bare path for CLI arg."""
        entity = _make_tool_entity("watermark")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="wf-1",
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="watermark",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        file_ref = "[File: /workspace/sandboxes/abc/true.jpg]"
        list(wrapper._invoke("u", {"image_file": file_ref, "text": "hello"}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "[File:" not in command
        # Absolute path should be preserved as-is (not joined with working dir)
        assert command.count("/workspace/sandboxes/abc/true.jpg") == 1
        assert "watermark_wf-1" in command

    def test_invoke_file_ref_with_spaces(self, mock_bash_tool: MagicMock) -> None:
        """[File: path with spaces] is handled correctly."""
        entity = _make_tool_entity("wm")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="wm",
            type=ToolProviderType.MCP,
            provider="s",
            tool_name="wm",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        file_ref = "[File: /workspace/my file.png]"
        list(wrapper._invoke("u", {"image_file": file_ref}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "[File:" not in command
        assert "/workspace/my file.png" in command

    def test_invoke_file_ref_relative_resolved(self, mock_bash_tool: MagicMock) -> None:
        """Relative path [File: true.jpg] → /workspace/sandboxes/test-sandbox/true.jpg."""
        entity = _make_tool_entity("wm")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="wm",
            type=ToolProviderType.MCP,
            provider="s",
            tool_name="wm",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"image_file": "[File: true.jpg]", "text": "123"}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "[File:" not in command
        assert "/workspace/sandboxes/test-sandbox/true.jpg" in command

    def test_invoke_plain_path_preserved(self, mock_bash_tool: MagicMock) -> None:
        """Plain file path (no [File:] wrapper) is passed through unchanged."""
        entity = _make_tool_entity("wm")
        real_tool = MagicMock()
        real_tool.entity = entity
        real_tool.runtime = MagicMock()
        real_tool.runtime.runtime_parameters = {}

        ref = ToolReference(
            uuid="wm",
            type=ToolProviderType.MCP,
            provider="s",
            tool_name="wm",
        )

        wrapper = SandboxNativeToolWrapper(
            tool_ref=ref,
            real_tool=real_tool,
            bash_tool=mock_bash_tool,
        )

        list(wrapper._invoke("u", {"image_file": "/workspace/photo.jpg"}))

        command = mock_bash_tool._invoke.call_args.kwargs["tool_parameters"]["bash"]
        assert "/workspace/photo.jpg" in command


# ── _build_sandbox_native_wrappers tests ───────────────────────────


class TestBuildWrappers:
    """Tests for LLMNode._build_sandbox_native_wrappers()."""

    def test_empty_dependencies(self, mock_self: MagicMock, mock_bash_tool: MagicMock) -> None:
        """Empty ToolDependencies → []."""
        from dify_graph.nodes.llm.node import LLMNode

        deps = ToolDependencies(dependencies=[], references=[])
        result = LLMNode._build_sandbox_native_wrappers(mock_self, deps, mock_bash_tool)
        assert result == []

    def test_none_dependencies(self, mock_self: MagicMock, mock_bash_tool: MagicMock) -> None:
        """None → []."""
        from dify_graph.nodes.llm.node import LLMNode

        result = LLMNode._build_sandbox_native_wrappers(mock_self, None, mock_bash_tool)
        assert result == []

    def test_disabled_tools_skipped(
        self, mock_self: MagicMock, mock_bash_tool: MagicMock
    ) -> None:
        """Disabled tools are not wrapped."""
        from dify_graph.nodes.llm.node import LLMNode

        dep = ToolDependency(
            type=ToolProviderType.MCP,
            provider="srv",
            tool_name="disabled_tool",
            enabled=False,
        )
        deps = ToolDependencies(dependencies=[dep], references=[])

        result = LLMNode._build_sandbox_native_wrappers(mock_self, deps, mock_bash_tool)
        assert result == []

    def test_builds_wrappers_for_mcp_tool(
        self, mock_self: MagicMock, mock_bash_tool: MagicMock
    ) -> None:
        """MCP tool → 1 wrapper with correct ToolManager call."""
        from dify_graph.nodes.llm.node import LLMNode

        ref = ToolReference(
            uuid="uuid-1",
            type=ToolProviderType.MCP,
            provider="mcp-srv",
            tool_name="query_stores",
        )
        dep = ToolDependency(
            type=ToolProviderType.MCP,
            provider="mcp-srv",
            tool_name="query_stores",
        )
        deps = ToolDependencies(dependencies=[dep], references=[ref])

        with patch("core.tools.tool_manager.ToolManager.get_tool_runtime") as mock_get_tool:
            real_tool = MagicMock()
            real_tool.entity = _make_tool_entity("query_stores")
            real_tool.runtime = MagicMock()
            real_tool.runtime.runtime_parameters = {}
            mock_get_tool.return_value = real_tool

            result = LLMNode._build_sandbox_native_wrappers(mock_self, deps, mock_bash_tool)

        assert len(result) == 1
        assert isinstance(result[0], SandboxNativeToolWrapper)
        assert result[0].entity.identity.name == "query_stores"
        mock_get_tool.assert_called_once()
        call_kwargs = mock_get_tool.call_args.kwargs
        assert call_kwargs["provider_type"] == ToolProviderType.MCP
        assert call_kwargs["provider_id"] == "mcp-srv"
        assert call_kwargs["tool_name"] == "query_stores"

    def test_toolmanager_error_is_skipped(
        self, mock_self: MagicMock, mock_bash_tool: MagicMock
    ) -> None:
        """ToolManager exception → wrapper skipped, not raised."""
        from dify_graph.nodes.llm.node import LLMNode

        dep = ToolDependency(
            type=ToolProviderType.MCP,
            provider="bad-srv",
            tool_name="ghost",
        )
        deps = ToolDependencies(dependencies=[dep], references=[])

        with patch("core.tools.tool_manager.ToolManager.get_tool_runtime") as mock_get_tool:
            mock_get_tool.side_effect = ValueError("gone")
            result = LLMNode._build_sandbox_native_wrappers(mock_self, deps, mock_bash_tool)

        assert result == []
