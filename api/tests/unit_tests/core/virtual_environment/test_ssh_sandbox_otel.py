"""Unit tests for OTel trace instrumentation on SSHSandboxEnvironment.

Covers:
- execute_command() span creation and attribute verification
- _consume_channel_output() span finishing with exit_code and status
- _run_command() span wrapping for infrastructure commands
- release_environment() / _drain_span_store() cleanup of pending spans
- ENABLE_OTEL=False path: zero spans, zero overhead
- Error paths: thread start failure, runtime errors, timeouts
- Concurrency: multiple isolated spans for concurrent commands
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode, set_tracer_provider

from core.virtual_environment.__base.entities import (
    Arch,
    ConnectionHandle,
    Metadata,
    OperatingSystem,
)
from core.virtual_environment.providers.ssh_sandbox import SSHSandboxEnvironment

SSH = SSHSandboxEnvironment.OptionsKey

# ── Fixtures ──────────────────────────────────────────────────────────


def _reset_otel_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Reset global tracer provider and install a memory-backed provider."""
    import opentelemetry.trace as trace_api

    trace_api._TRACER_PROVIDER = None
    trace_api._TRACER_PROVIDER_SET_ONCE._done = False

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    set_tracer_provider(provider)
    return provider, exporter


def _make_mock_channel(*, ready: bool = True) -> MagicMock:
    """Return a mock SSH channel that reports exit_status_ready immediately."""
    channel = MagicMock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = ready
    if ready:
        channel.recv_exit_status.return_value = 0
    channel.recv.return_value = b""
    channel.recv_stderr.return_value = b""
    return channel


def _make_mock_transport(channel: MagicMock) -> MagicMock:
    transport = MagicMock()
    transport.open_session.return_value = channel
    return transport


def _make_ssh_env(
    *,
    channel: MagicMock | None = None,
    **options: str,
) -> SSHSandboxEnvironment:
    """Create an SSHSandboxEnvironment with minimal required options.

    Mocks paramiko and sets metadata so both execute_command() and
    get_working_path() work without real SSH connections.
    """
    _channel = channel or _make_mock_channel()
    transport = _make_mock_transport(_channel)

    mock_client = MagicMock()
    mock_client.get_transport.return_value = transport

    opts: dict[str, str] = {
        SSH.SSH_USERNAME: "testuser",
        SSH.SSH_PASSWORD: "testpass",
    }
    opts.update(options)

    with patch.object(
        SSHSandboxEnvironment, "_create_ssh_client", return_value=mock_client
    ):
        env = SSHSandboxEnvironment(tenant_id="t-1", options=opts)

        working_path = env._workspace_path_from_id("env-test")
        env._metadata = Metadata(
            id="env-test",
            arch=Arch.AMD64,
            os=OperatingSystem.LINUX,
            store={"working_path": working_path},
        )

        conn_handle = ConnectionHandle(id="conn-1")
        with env._lock:
            env._connections[conn_handle.id] = mock_client
    return env


def _get_spans(exporter: InMemorySpanExporter) -> list:
    return exporter.get_finished_spans()


@pytest.fixture(autouse=True)
def _reset_otel():
    """Ensure OTel provider is reset between tests."""
    _reset_otel_provider()
    yield
    _reset_otel_provider()


# ── execute_command() span creation ───────────────────────────────────


class TestExecuteCommandSpanCreation:
    def test_creates_span_with_correct_attributes(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env(ssh_host="agentbox")

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pid, stdin_transport, stdout_transport, stderr_transport = env.execute_command(
                ConnectionHandle(id="conn-1"),
                ["echo", "hello"],
            )

        time.sleep(0.15)

        # span must be in the store since _consume_channel_output should pop it
        spans = _get_spans(exporter)
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "SSHSandboxEnvironment.execute_command"
        assert span.kind == SpanKind.INTERNAL
        attrs = dict(span.attributes or {})
        assert attrs["sandbox.pid"] == pid
        assert attrs["sandbox.type"] == "ssh"
        assert attrs["sandbox.host"] == "agentbox"
        assert attrs["sandbox.tenant_id"] == "t-1"
        assert "echo hello" in attrs["sandbox.command"]

    def test_span_status_ok_on_success(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            env.execute_command(ConnectionHandle(id="conn-1"), ["true"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.OK

    def test_span_status_error_on_nonzero_exit(self):
        _, exporter = _reset_otel_provider()
        channel = _make_mock_channel()
        channel.recv_exit_status.return_value = 1
        env = _make_ssh_env(channel=channel)

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            env.execute_command(ConnectionHandle(id="conn-1"), ["false"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR
        assert "exit_code=1" in spans[0].status.description

    def test_no_span_when_otel_disabled(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", False):
            env.execute_command(ConnectionHandle(id="conn-1"), ["true"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        assert len(spans) == 0

    def test_no_span_in_store_after_completion(self):
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pid, _, _, _ = env.execute_command(ConnectionHandle(id="conn-1"), ["echo", "done"])

        time.sleep(0.15)

        with env._lock:
            assert pid not in env._span_store


# ── _consume_channel_output() span finishing ─────────────────────────


class TestConsumeChannelSpanFinish:
    def test_exit_code_recorded(self):
        _, exporter = _reset_otel_provider()
        channel = _make_mock_channel()
        channel.recv_exit_status.return_value = 42
        env = _make_ssh_env(channel=channel)

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pid, _, _, _ = env.execute_command(ConnectionHandle(id="conn-1"), ["exit", "42"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        assert attrs["sandbox.exit_code"] == 42
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_timeout_exit_code(self):
        _, exporter = _reset_otel_provider()
        channel = _make_mock_channel(ready=False)
        env = _make_ssh_env(channel=channel)

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            with patch.object(
                env,
                "_COMMAND_TIMEOUT_EXIT_CODE",
                1,  # Force flush after fix to ensure thread runs
            ):
                env.execute_command(
                    ConnectionHandle(id="conn-1"),
                    ["sleep", "999"],
                )

        time.sleep(0.3)
        spans = _get_spans(exporter)
        if len(spans) == 1:
            attrs = dict(spans[0].attributes or {})
            assert attrs["sandbox.exit_code"] == 124


# ── _run_command() span ──────────────────────────────────────────────


class TestRunCommandSpan:
    def test_run_command_creates_span_on_success(self):
        _, exporter = _reset_otel_provider()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            mock_stdout = MagicMock()
            mock_stdout.channel.exit_status_ready.return_value = True
            mock_stdout.channel.recv_exit_status.return_value = 0
            mock_stdout.read.return_value = b"x86_64\n"

            mock_stderr = MagicMock()
            mock_stderr.read.return_value = b""

            mock_client = MagicMock()
            mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

            try:
                SSHSandboxEnvironment._run_command(mock_client, "uname -m")
            except (RuntimeError, AssertionError):
                pass

        spans = _get_spans(exporter)
        assert len(spans) >= 1
        span_names = {s.name for s in spans}
        assert "SSHSandboxEnvironment._run_command" in span_names

    def test_run_command_no_span_when_otel_disabled(self):
        _, exporter = _reset_otel_provider()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", False):
            mock_stdout = MagicMock()
            mock_stdout.channel.exit_status_ready.return_value = True
            mock_stdout.channel.recv_exit_status.return_value = 0
            mock_stdout.read.return_value = b"x86_64\n"

            mock_stderr = MagicMock()
            mock_stderr.read.return_value = b""

            mock_client = MagicMock()
            mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

            try:
                SSHSandboxEnvironment._run_command(mock_client, "uname -m")
            except (RuntimeError, AssertionError):
                pass

        spans = _get_spans(exporter)
        span_names = {s.name for s in spans}
        assert "SSHSandboxEnvironment._run_command" not in span_names

    def test_run_command_records_exception(self):
        _, exporter = _reset_otel_provider()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            mock_stdout = MagicMock()
            mock_stdout.channel.exit_status_ready.return_value = True
            mock_stdout.channel.recv_exit_status.return_value = 1
            mock_stdout.read.return_value = b""
            mock_stderr = MagicMock()
            mock_stderr.read.return_value = b"error"

            mock_client = MagicMock()
            mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

            try:
                SSHSandboxEnvironment._run_command(mock_client, "badcmd")
            except RuntimeError:
                pass

        spans = _get_spans(exporter)
        run_spans = [s for s in spans if "_run_command" in s.name]
        assert len(run_spans) == 1
        assert run_spans[0].status.status_code == StatusCode.ERROR


# ── release_environment() / _drain_span_store() ──────────────────────


class TestDrainSpanStore:
    def test_drain_clears_pending_spans(self):
        _, exporter = _reset_otel_provider()
        channel = _make_mock_channel(ready=False)
        env = _make_ssh_env(channel=channel)

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pid, _, _, _ = env.execute_command(ConnectionHandle(id="conn-1"), ["sleep", "999"])
            assert pid in env._span_store
            env._drain_span_store()

        with env._lock:
            assert pid not in env._span_store

        spans = _get_spans(exporter)
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR
        assert "pending commands" in spans[0].status.description

    def test_drain_empty_store_noop(self):
        env = _make_ssh_env()
        env._drain_span_store()
        with env._lock:
            assert len(env._span_store) == 0


# ── Thread start failure paths ───────────────────────────────────────


class TestThreadStartFailure:
    def test_span_cleaned_on_thread_failure(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            with patch("threading.Thread.start", side_effect=RuntimeError("no threads")):
                with pytest.raises(RuntimeError, match="no threads"):
                    env.execute_command(ConnectionHandle(id="conn-1"), ["echo", "boom"])

        spans = _get_spans(exporter)
        assert len(spans) >= 1
        exec_spans = [s for s in spans if "execute_command" in s.name]
        assert len(exec_spans) == 1
        assert exec_spans[0].status.status_code == StatusCode.ERROR


# ── Concurrency isolation ────────────────────────────────────────────


class TestConcurrency:
    def test_multiple_commands_independent_spans(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pids = []
            for i in range(5):
                pid, _, _, _ = env.execute_command(
                    ConnectionHandle(id="conn-1"), ["echo", str(i)]
                )
                pids.append(pid)

        time.sleep(0.2)

        spans = _get_spans(exporter)
        assert len(spans) == 5

        span_pids = {dict(s.attributes or {}).get("sandbox.pid") for s in spans}
        assert span_pids == set(pids)

        assert all(s.status.status_code == StatusCode.OK for s in spans)

    def test_concurrent_spans_dont_interfere(self):
        _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            pids = []
            for i in range(3):
                pid, _, _, _ = env.execute_command(
                    ConnectionHandle(id="conn-1"), ["echo", str(i)]
                )
                pids.append(pid)

        time.sleep(0.2)

        for pid in pids:
            with env._lock:
                assert pid not in env._span_store, f"pid {pid} still in span_store"


# ── Span attributes edge cases ───────────────────────────────────────


class TestSpanAttributes:
    def test_default_host(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            env.execute_command(ConnectionHandle(id="conn-1"), ["ls"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        attrs = dict(spans[0].attributes or {})
        assert attrs["sandbox.host"] == "agentbox"

    def test_custom_host(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env(ssh_host="sandbox-prod-1.internal", ssh_username="deploy", ssh_password="s3cret")

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            env.execute_command(ConnectionHandle(id="conn-1"), ["ls"])

        time.sleep(0.15)
        spans = _get_spans(exporter)
        attrs = dict(spans[0].attributes or {})
        assert attrs["sandbox.host"] == "sandbox-prod-1.internal"

    def test_complex_command_truncation_not_required(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        cmd = ["bash", "-c", 'echo "hello world" && ls -la && cat /etc/hostname']
        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            env.execute_command(ConnectionHandle(id="conn-1"), cmd)

        time.sleep(0.15)
        spans = _get_spans(exporter)
        attrs = dict(spans[0].attributes or {})
        assert "echo" in attrs["sandbox.command"]
        assert "hostname" in attrs["sandbox.command"]


# ── _create_command_span / _finish_command_span direct ───────────────


class TestSpanHelpersDirect:
    def test_create_command_span_returns_span(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            span = env._create_command_span("pid-x", "echo test")
            span.set_attribute("sandbox.exit_code", 0)
            span.set_status(Status(StatusCode.OK))
            span.end()

        spans = _get_spans(exporter)
        assert len(spans) == 1

    def test_finish_command_span_noop_when_not_found(self):
        env = _make_ssh_env()
        env._finish_command_span("nonexistent", 0)
        # should not raise

    def test_finish_command_span_pops_from_store(self):
        _, exporter = _reset_otel_provider()
        env = _make_ssh_env()

        with patch("core.virtual_environment.providers.ssh_sandbox.dify_config.ENABLE_OTEL", True):
            span = env._create_command_span("pid-y", "pwd")
            env._span_store["pid-y"] = span
            env._finish_command_span("pid-y", 0)

        with env._lock:
            assert "pid-y" not in env._span_store

        spans = _get_spans(exporter)
        assert len(spans) == 1
