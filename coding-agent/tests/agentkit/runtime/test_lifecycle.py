import pytest

from agentkit import Lifecycle as PublicLifecycle
from agentkit.runtime import Lifecycle as RuntimeExportLifecycle
from agentkit.runtime.lifecycle import Lifecycle


class ConcreteLifecycle:
    def __init__(self) -> None:
        self.started: bool = False
        self.stopped: bool = False

    async def on_startup(self) -> None:
        self.started = True

    async def on_shutdown(self, timeout: float = 30.0) -> None:
        _ = timeout
        self.stopped = True

    async def health_check(self) -> dict[str, str]:
        return {"status": "ok"}

    async def readiness_check(self) -> bool:
        return self.started and not self.stopped


class TestLifecycleProtocol:
    def test_top_level_import_matches_runtime_protocol(self):
        assert PublicLifecycle is Lifecycle

    def test_runtime_package_import_matches_protocol(self):
        assert RuntimeExportLifecycle is Lifecycle

    def test_concrete_satisfies_protocol(self):
        lc = ConcreteLifecycle()
        assert isinstance(lc, Lifecycle)

    def test_object_does_not_satisfy_protocol(self):
        assert not isinstance(object(), Lifecycle)

    @pytest.mark.asyncio
    async def test_startup_shutdown_sequence(self):
        lc = ConcreteLifecycle()
        assert not lc.started
        await lc.on_startup()
        assert lc.started
        assert await lc.readiness_check() is True
        await lc.on_shutdown(timeout=5.0)
        assert lc.stopped
        assert await lc.readiness_check() is False

    @pytest.mark.asyncio
    async def test_health_check_returns_dict(self):
        lc = ConcreteLifecycle()
        result = await lc.health_check()
        assert isinstance(result, dict)
        assert "status" in result

    @pytest.mark.asyncio
    async def test_shutdown_default_timeout(self):
        lc = ConcreteLifecycle()
        await lc.on_shutdown()
        assert lc.stopped
