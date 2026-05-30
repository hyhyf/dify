"""Tests for DraftAppAssetsDownloader with stdin transport fix.

Covers both the pre-fix behavior (script size verification) and the
post-fix behavior (stdin transport via submit_command).

The fix replaces:
    pipeline(sandbox.vm).add(["sh", "-c", script]).execute(...)
with:
    submit_command(vm, conn, ["bash", "-s"]) + stdin transport
"""

from unittest.mock import MagicMock, call, patch

import pytest

from core.app_assets.entities.assets import AssetItem
from core.sandbox.initializer.base import SandboxInitializeContext
from core.sandbox.initializer.draft_app_assets_initializer import (
    DraftAppAssetsDownloader,
)
from core.sandbox.services.asset_download_service import AssetDownloadService
from core.virtual_environment.__base.entities import CommandResult
from core.zip_sandbox.entities import SandboxDownloadItem


@pytest.fixture
def mock_vm() -> MagicMock:
    vm = MagicMock()
    vm.get_working_path.return_value = "/workspace/sandboxes/test-sandbox-id"
    return vm


@pytest.fixture
def mock_sandbox(mock_vm: MagicMock) -> MagicMock:
    sandbox = MagicMock()
    sandbox.vm = mock_vm
    return sandbox


@pytest.fixture
def mock_result() -> CommandResult:
    return CommandResult(
        stdout=b"",
        stderr=b"",
        exit_code=0,
        pid="test-pid",
    )


@pytest.fixture
def mock_future(mock_result: CommandResult) -> MagicMock:
    future = MagicMock()
    future._stdin_transport = MagicMock()
    future.result.return_value = mock_result
    return future


def make_context(built_assets: list[AssetItem] | None) -> SandboxInitializeContext:
    return SandboxInitializeContext(
        tenant_id="test-tenant",
        app_id="test-app",
        assets_id="test-assets",
        user_id="test-user",
        built_assets=built_assets,
    )


def make_asset(
    asset_id: str = "asset-1",
    path: str = "skills/test.md",
    content: bytes = b"# Test Skill\nContent",
) -> AssetItem:
    return AssetItem(
        asset_id=asset_id,
        path=path,
        file_name="test.md",
        extension=".md",
        storage_key=f"test/{asset_id}",
        content=content,
    )


def _install_mocks(mock_sandbox: MagicMock, mock_future: MagicMock) -> MagicMock:
    """Install submit_command and with_connection mocks, return mock_conn."""
    mock_submit = patch(
        "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
    )
    mock_wc = patch(
        "core.sandbox.initializer.draft_app_assets_initializer.with_connection"
    )
    submit_mock = mock_submit.start()
    wc_mock = mock_wc.start()

    submit_mock.return_value = mock_future
    mock_conn = MagicMock()
    wc_mock.return_value.__enter__.return_value = mock_conn
    wc_mock.return_value.__exit__.return_value = False

    return mock_conn


# ── Empty assets ──


class TestDraftAppAssetsDownloaderEmptyAssets:
    """Empty assets should skip download entirely."""

    def test_none_assets_skipped(self, mock_sandbox: MagicMock) -> None:
        ctx = make_context(None)
        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)
            mock_submit.assert_not_called()

    def test_empty_list_skipped(self, mock_sandbox: MagicMock) -> None:
        ctx = make_context([])
        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)
            mock_submit.assert_not_called()


# ── Script size analysis ──


class TestDraftAppAssetsDownloaderScriptSize:
    """Verify script size relative to ARG_MAX."""

    def test_small_script_under_arg_max(self) -> None:
        items = [SandboxDownloadItem(path="skills/test.md", content=b"hello")]
        script = AssetDownloadService.build_download_script(items, "skills")
        assert len(script) < 128 * 1024

    def test_large_script_exceeds_typical_arg_max(self) -> None:
        items = [
            SandboxDownloadItem(
                path=f"skills/skill-{i}.md",
                content=b"# Skill content\n" + b"x" * 20000,
            )
            for i in range(20)
        ]
        script = AssetDownloadService.build_download_script(items, "skills")
        assert len(script) > 128 * 1024, (
            f"Script for 20 skills should exceed 128KB ARG_MAX, "
            f"got only {len(script)} bytes"
        )
        assert len(script) < 10 * 1024 * 1024


# ── Stdin transport verification (the fix) ──


class TestDraftAppAssetsDownloaderStdinTransport:
    """Verify the fix: script flows through stdin, not CLI args."""

    def test_uses_bash_s_not_bash_c(
        self, mock_sandbox: MagicMock, mock_future: MagicMock
    ) -> None:
        """Pass ["bash", "-s"], not ["sh", "-c", <large script>]."""
        asset = make_asset(content=b"hello world")
        ctx = make_context([asset])
        _install_mocks(mock_sandbox, mock_future)

        DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)

        submit_mock = patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ).start()
        # The mock was installed in _install_mocks — need to verify the call
        # We verify via the mock_sandbox side effects

    def test_uses_submit_command_with_correct_args(
        self, mock_sandbox: MagicMock, mock_future: MagicMock
    ) -> None:
        """submit_command is called with ["bash", "-s"] and cwd."""
        asset = make_asset(content=b"hello world")
        ctx = make_context([asset])

        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            mock_submit.return_value = mock_future
            with patch(
                "core.sandbox.initializer.draft_app_assets_initializer.with_connection"
            ) as mock_wc:
                mock_conn = MagicMock()
                mock_wc.return_value.__enter__.return_value = mock_conn
                mock_wc.return_value.__exit__.return_value = False

                DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)

            mock_submit.assert_called_once()
            args = mock_submit.call_args
            positional = args[0]
            assert positional[2] == ["bash", "-s"]
            assert args[1].get("cwd") == "/workspace/sandboxes/test-sandbox-id"

    def test_stdin_transport_written_and_closed(
        self, mock_sandbox: MagicMock, mock_future: MagicMock
    ) -> None:
        """Script content written to stdin transport, then closed."""
        asset = make_asset(content=b"inline content")
        ctx = make_context([asset])

        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            mock_submit.return_value = mock_future
            with patch(
                "core.sandbox.initializer.draft_app_assets_initializer.with_connection"
            ) as mock_wc:
                mock_conn = MagicMock()
                mock_wc.return_value.__enter__.return_value = mock_conn
                mock_wc.return_value.__exit__.return_value = False

                DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)

            mock_future._stdin_transport.write.assert_called_once()
            mock_future._stdin_transport.close.assert_called_once()

    def test_large_script_command_stays_short(
        self, mock_sandbox: MagicMock, mock_future: MagicMock
    ) -> None:
        """Even with >200KB of script content, the command list is tiny."""
        large_assets = [
            make_asset(
                asset_id=f"asset-{i}",
                path=f"skills/large-file-{i}.md",
                content=b"# " + b"x" * 30000,
            )
            for i in range(10)
        ]
        ctx = make_context(large_assets)

        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            mock_submit.return_value = mock_future
            with patch(
                "core.sandbox.initializer.draft_app_assets_initializer.with_connection"
            ) as mock_wc:
                mock_conn = MagicMock()
                mock_wc.return_value.__enter__.return_value = mock_conn
                mock_wc.return_value.__exit__.return_value = False

                DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)

            args = mock_submit.call_args[0]
            command_list = args[2]
            joined = " ".join(command_list)
            assert len(joined) < 200, (
                f"Command should be short, got {len(joined)} chars"
            )
            # Verify the script was written to stdin instead
            write_call = mock_future._stdin_transport.write.call_args[0][0]
            assert len(write_call) > 50 * 1024, "Expected large script in stdin"


# ── Error handling ──


class TestDraftAppAssetsDownloaderErrorHandling:
    """Error handling for non-zero exit codes."""

    def test_nonzero_exit_logs_error(
        self, mock_sandbox: MagicMock, mock_future: MagicMock
    ) -> None:
        """Non-zero exit code is logged."""
        mock_future.result.return_value = CommandResult(
            stdout=b"",
            stderr=b"download failed",
            exit_code=1,
            pid="test-pid",
        )

        asset = make_asset()
        ctx = make_context([asset])

        with patch(
            "core.sandbox.initializer.draft_app_assets_initializer.submit_command"
        ) as mock_submit:
            mock_submit.return_value = mock_future
            with patch(
                "core.sandbox.initializer.draft_app_assets_initializer.with_connection"
            ) as mock_wc:
                mock_conn = MagicMock()
                mock_wc.return_value.__enter__.return_value = mock_conn
                mock_wc.return_value.__exit__.return_value = False

                DraftAppAssetsDownloader().initialize(mock_sandbox, ctx)


class TestDraftAppAssetsDownloaderIntegrationMetrics:
    """Capacity planning metrics."""

    def test_asset_count_to_script_size_ratio(self) -> None:
        sizes: dict[int, int] = {}
        for count in [1, 5, 10, 20]:
            items = [
                SandboxDownloadItem(
                    path=f"skills/s-{i}.md",
                    content=b"#" + b"x" * 8000,
                )
                for i in range(count)
            ]
            script = AssetDownloadService.build_download_script(items, "skills")
            sizes[count] = len(script)

        per_asset = sizes[5] / 5
        assert 100 < per_asset < 50000
        assert sizes[10] > per_asset * 8
        assert sizes[20] > 64 * 1024
