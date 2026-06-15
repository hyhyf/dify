from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, cast

from flask import Flask, current_app, has_app_context

from core.entities.provider_entities import BasicProviderConfig
from core.virtual_environment.__base.virtual_environment import VirtualEnvironment

from .entities.sandbox_type import SandboxType
from .initializer import AsyncSandboxInitializer, SandboxInitializeContext, SandboxInitializer, SyncSandboxInitializer
from .sandbox import Sandbox

if TYPE_CHECKING:
    from .storage.sandbox_storage import SandboxStorage

logger = logging.getLogger(__name__)


def _get_sandbox_class(sandbox_type: SandboxType) -> type[VirtualEnvironment]:
    match sandbox_type:
        case SandboxType.DOCKER:
            from core.virtual_environment.providers.docker_daemon_sandbox import DockerDaemonEnvironment

            return DockerDaemonEnvironment
        case SandboxType.E2B:
            from core.virtual_environment.providers.e2b_sandbox import E2BEnvironment

            return E2BEnvironment
        case SandboxType.LOCAL:
            from core.virtual_environment.providers.local_without_isolation import LocalVirtualEnvironment

            return LocalVirtualEnvironment
        case SandboxType.SSH:
            from core.virtual_environment.providers.ssh_sandbox import SSHSandboxEnvironment

            return SSHSandboxEnvironment
        case SandboxType.AWS_CODE_INTERPRETER:
            from core.virtual_environment.providers.aws_code_interpreter_sandbox import (
                AWSCodeInterpreterEnvironment,
            )

            return AWSCodeInterpreterEnvironment
        case _:
            raise ValueError(f"Unsupported sandbox type: {sandbox_type}")


class SandboxBuilder:
    _tenant_id: str
    _sandbox_type: SandboxType
    _user_id: str | None
    _app_id: str | None
    _options: dict[str, Any]
    _environments: dict[str, str]
    _initializers: list[SandboxInitializer]
    _storage: SandboxStorage | None
    _assets_id: str | None

    def __init__(self, tenant_id: str, sandbox_type: SandboxType) -> None:
        self._tenant_id = tenant_id
        self._sandbox_type = sandbox_type
        self._user_id = None
        self._app_id = None
        self._options = {}
        self._environments = {}
        self._initializers = []
        self._storage = None
        self._assets_id = None

    def user(self, user_id: str) -> SandboxBuilder:
        self._user_id = user_id
        return self

    def app(self, app_id: str) -> SandboxBuilder:
        self._app_id = app_id
        return self

    def options(self, options: Mapping[str, Any]) -> SandboxBuilder:
        self._options = dict(options)
        return self

    def environments(self, environments: Mapping[str, str]) -> SandboxBuilder:
        self._environments = dict(environments)
        return self

    def initializer(self, initializer: SandboxInitializer) -> SandboxBuilder:
        self._initializers.append(initializer)
        return self

    def initializers(self, initializers: Sequence[SandboxInitializer]) -> SandboxBuilder:
        self._initializers.extend(initializers)
        return self

    def storage(self, storage: SandboxStorage, assets_id: str) -> SandboxBuilder:
        self._storage = storage
        self._assets_id = assets_id
        return self

    def build(self) -> Sandbox:
        """Create a sandbox and start background initialization.

        The builder is responsible for cleaning up any VM or sandbox that was
        successfully created if a later setup step fails.
        """
        if self._storage is None:
            raise ValueError("storage is required, call .storage() before .build()")
        if self._assets_id is None:
            raise ValueError("assets_id is required, call .storage() before .build()")
        if self._user_id is None:
            raise ValueError("user_id is required, call .user() before .build()")
        if self._app_id is None:
            raise ValueError("app_id is required, call .app() before .build()")

        t_build_start = time.monotonic()

        ctx = SandboxInitializeContext(
            tenant_id=self._tenant_id,
            app_id=self._app_id,
            assets_id=self._assets_id,
            user_id=self._user_id,
        )
        vm: VirtualEnvironment | None = None
        sandbox: Sandbox | None = None
        try:
            t0 = time.monotonic()
            vm_class = _get_sandbox_class(self._sandbox_type)
            vm = vm_class(
                tenant_id=self._tenant_id,
                options=self._options,
                environments=self._environments,
                user_id=self._user_id,
            )
            vm.open_enviroment()
            t_vm_open = time.monotonic() - t0
            logger.debug(
                "[BENCHMARK] sandbox_builder vm open_enviroment took %.3fs (type=%s)",
                t_vm_open,
                self._sandbox_type,
            )

            sandbox = Sandbox(
                vm=vm,
                storage=self._storage,
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                app_id=self._app_id,
                assets_id=self._assets_id,
            )

            t0 = time.monotonic()
            for init in self._initializers:
                if isinstance(init, SyncSandboxInitializer):
                    init_class = init.__class__.__name__
                    init.initialize(sandbox, ctx)
                    logger.debug(
                        "[BENCHMARK] sandbox_builder sync init %s completed", init_class
                    )
            t_sync_init = time.monotonic() - t0
            logger.debug(
                "[BENCHMARK] sandbox_builder sync init total took %.3fs", t_sync_init
            )
        except Exception as exc:
            logger.exception(
                "Failed to initialize sandbox synchronously: tenant_id=%s, app_id=%s", self._tenant_id, self._app_id
            )
            if sandbox is not None:
                sandbox.release()
            elif vm is not None:
                try:
                    vm.release_environment()
                except Exception:
                    logger.exception("Failed to release sandbox VM during builder cleanup")
            raise RuntimeError("Sandbox initialization failed") from exc

        # Run sandbox setup asynchronously so workflow execution can proceed.
        # Capture the Flask app before starting the thread for database access.
        flask_app: Flask | None = cast(Any, current_app)._get_current_object() if has_app_context() else None

        _sandbox: Sandbox = sandbox

        def initialize() -> None:
            t_async_start = time.monotonic()
            try:
                app_context = flask_app.app_context() if flask_app is not None else nullcontext()
                with app_context:
                    for init in self._initializers:
                        if not isinstance(init, AsyncSandboxInitializer):
                            continue

                        if _sandbox.is_cancelled():
                            return
                        init_class = init.__class__.__name__
                        t_init0 = time.monotonic()
                        init.initialize(_sandbox, ctx)
                        t_init_elapsed = time.monotonic() - t_init0
                        logger.debug(
                            "[BENCHMARK] sandbox_builder async init %s took %.3fs",
                            init_class,
                            t_init_elapsed,
                        )

                    if _sandbox.is_cancelled():
                        return
                    t0 = time.monotonic()
                    _sandbox.mount()
                    t_mount = time.monotonic() - t0
                    logger.debug("[BENCHMARK] sandbox_builder mount took %.3fs", t_mount)
                    _sandbox.mark_ready()
                    t_async_total = time.monotonic() - t_async_start
                    logger.debug(
                        "[BENCHMARK] sandbox_builder async init TOTAL took %.3fs", t_async_total
                    )
            except Exception as exc:
                try:
                    logger.exception(
                        "Failed to initialize sandbox: tenant_id=%s, app_id=%s", self._tenant_id, self._app_id
                    )
                    _sandbox.release()
                    _sandbox.mark_failed(exc)
                except Exception:
                    logger.exception(
                        "Failed to mark sandbox initialization failure: tenant_id=%s, app_id=%s",
                        self._tenant_id,
                        self._app_id,
                    )

        # Background init completes or signals failure via sandbox state.
        try:
            threading.Thread(target=initialize, daemon=True).start()
        except Exception:
            logger.exception(
                "Failed to start sandbox initialization thread: tenant_id=%s, app_id=%s",
                self._tenant_id,
                self._app_id,
            )
            sandbox.release()
            raise RuntimeError("Sandbox initialization failed")

        t_build_total = time.monotonic() - t_build_start
        logger.debug(
            "[BENCHMARK] sandbox_builder build() sync phase took %.3fs (vm_open=%.3fs, sync_init=%.3fs)",
            t_build_total,
            t_vm_open,
            t_sync_init,
        )
        return sandbox

    @staticmethod
    def validate(vm_type: SandboxType, options: Mapping[str, Any]) -> None:
        vm_class = _get_sandbox_class(vm_type)
        vm_class.validate(options)

    @classmethod
    def draft_id(cls, user_id: str) -> str:
        return user_id


class VMConfig:
    @staticmethod
    def get_schema(vm_type: SandboxType) -> list[BasicProviderConfig]:
        return _get_sandbox_class(vm_type).get_config_schema()
