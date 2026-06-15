"""Unit tests for SandboxBashSession behavior.

Verifies conditional dify-cli initialization: when tools is None or empty,
CliApiSession creation and dify init are skipped; bash_tool remains accessible.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.sandbox.bash.session import SandboxBashSession
from core.sandbox.entities import DifyCli
from core.skill.entities.tool_dependencies import ToolDependencies


@pytest.fixture
def mock_sandbox() -> MagicMock:
    sandbox = MagicMock()
    sandbox.id = "sandbox-1"
    sandbox.vm = MagicMock()
    sandbox.vm.get_working_path.return_value = "/workspace/sandboxes/sandbox-1"
    sandbox.tenant_id = "test-tenant"
    sandbox.user_id = "test-user"
    sandbox.app_id = "test-app"
    sandbox.assets_id = "test-assets"
    sandbox.wait_ready = MagicMock()
    return sandbox


# ── Tool setup behavior ────────────────────────────────────────────


class TestToolSetup:
    """B1-B3: Conditional dify-cli initialization."""

    def test_skips_cli_when_tools_none(self, mock_sandbox: MagicMock) -> None:
        """B1: tools=None → uses global_tools_path, no CliApiSession."""
        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=None,
        )

        with (
            patch.object(session, "_setup_node_tools_directory") as mock_setup,
            patch(
                "core.sandbox.bash.session.CliApiSessionManager"
            ) as mock_manager_cls,
        ):
            mock_manager_cls.return_value.create.return_value = MagicMock()
            with session:
                tools_path = session.bash_tool._tools_path

        cli = DifyCli(mock_sandbox.id)
        assert tools_path == cli.global_tools_path
        mock_setup.assert_not_called()

    def test_skips_cli_when_tools_empty(self, mock_sandbox: MagicMock) -> None:
        """B2: tools.is_empty() → uses global_tools_path."""
        empty_deps = ToolDependencies(dependencies=[], references=[])
        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=empty_deps,
        )

        with (
            patch.object(session, "_setup_node_tools_directory") as mock_setup,
            patch(
                "core.sandbox.bash.session.CliApiSessionManager"
            ) as mock_manager_cls,
        ):
            mock_manager_cls.return_value.create.return_value = MagicMock()
            with session:
                tools_path = session.bash_tool._tools_path

        cli = DifyCli(mock_sandbox.id)
        assert tools_path == cli.global_tools_path
        mock_setup.assert_not_called()


# ── bash_tool property ─────────────────────────────────────────────

    def test_sets_up_when_tools_present(self, mock_sandbox: MagicMock) -> None:
        """B3: tools with dependencies → _setup_node_tools_directory is called."""
        from core.skill.entities.skill_metadata import ToolReference
        from core.skill.entities.tool_dependencies import ToolDependencies, ToolDependency
        from core.tools.entities.tool_entities import ToolProviderType

        ref = ToolReference(
            uuid="uuid-1",
            type=ToolProviderType.MCP,
            provider="test-prov",
            tool_name="test_tool",
        )
        dep = ToolDependency(
            type=ToolProviderType.MCP,
            provider="test-prov",
            tool_name="test_tool",
        )
        deps = ToolDependencies(dependencies=[dep], references=[ref])

        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=deps,
        )

        with (
            patch.object(session, "_setup_node_tools_directory") as mock_setup,
            patch(
                "core.sandbox.bash.session.CliApiSessionManager"
            ) as mock_manager_cls,
        ):
            mock_setup.return_value = "/tmp/.dify/sandbox-1/tools/llm/node-1"
            mock_manager_cls.return_value.create.return_value = MagicMock()
            with session:
                tools_path = session.bash_tool._tools_path

        assert tools_path == "/tmp/.dify/sandbox-1/tools/llm/node-1"
        mock_setup.assert_called_once()


class TestBashToolAccess:
    """B6: bash_tool is accessible after session entry."""

    def test_bash_tool_accessible(self, mock_sandbox: MagicMock) -> None:
        """B6: session.bash_tool returns a valid SandboxBashTool."""
        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=None,
        )

        with patch(
            "core.sandbox.bash.session.CliApiSessionManager"
        ) as mock_manager_cls:
            mock_manager_cls.return_value.create.return_value = MagicMock()
            with session:
                bash_tool = session.bash_tool

        assert bash_tool is not None
        assert bash_tool.entity.identity.name == "bash"

    def test_bash_tool_raises_before_enter(self, mock_sandbox: MagicMock) -> None:
        """Accessing bash_tool before __enter__ raises RuntimeError."""
        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=None,
        )

        with pytest.raises(RuntimeError, match="not initialized"):
            _ = session.bash_tool


# ── Session cleanup ────────────────────────────────────────────────


class TestSessionCleanup:
    """B5: CliApiSession is cleaned up on exit."""

    def test_cli_api_session_cleanup(self, mock_sandbox: MagicMock) -> None:
        """B5: __exit__ calls CliApiSessionManager.delete."""
        session = SandboxBashSession(
            sandbox=mock_sandbox,
            node_id="node-1",
            tools=None,
        )

        mock_session_instance = MagicMock()
        mock_session_instance.id = "cli-session-1"
        with patch(
            "core.sandbox.bash.session.CliApiSessionManager"
        ) as mock_manager_cls:
            mock_manager = MagicMock()
            mock_manager.create.return_value = mock_session_instance
            mock_manager_cls.return_value = mock_manager

            with session:
                pass

            mock_manager.delete.assert_called_once_with("cli-session-1")
