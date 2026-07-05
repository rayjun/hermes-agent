import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_slack_mock():
    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return

    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock

    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock

    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler", slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        sys.modules.setdefault(name, mod)


_ensure_slack_mock()

import plugins.platforms.slack.adapter as _slack_mod  # noqa: E402

_slack_mod.SLACK_AVAILABLE = True

from plugins.platforms.slack.adapter import SlackAdapter  # noqa: E402


async def _never_finishes():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_restart_waits_for_cancellation_resistant_close_before_replacing(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._running = True
    adapter._app = MagicMock()
    adapter._app_token = "xapp-test"
    handler = MagicMock()
    release_close = asyncio.Event()

    async def close_async():
        try:
            await release_close.wait()
        except asyncio.CancelledError:
            await release_close.wait()

    handler.close_async = AsyncMock(side_effect=close_async)
    socket_task = asyncio.create_task(_never_finishes())
    adapter._handler = handler
    adapter._socket_mode_task = socket_task
    start_socket_mode_handler = MagicMock()
    monkeypatch.setattr(adapter, "_start_socket_mode_handler", start_socket_mode_handler)
    monkeypatch.setattr(_slack_mod, "_SOCKET_MODE_CLOSE_TIMEOUT", 0.01, raising=False)

    restart_task = asyncio.create_task(adapter._restart_socket_mode("transport disconnected"))
    try:
        await asyncio.sleep(0.05)

        assert restart_task.done()
        start_socket_mode_handler.assert_not_called()
        assert adapter._handler is handler
        assert adapter._socket_mode_task is socket_task
        close_task = adapter._socket_mode_close_task
        assert close_task is not None and not close_task.done()

        release_close.set()
        await close_task
        await asyncio.wait_for(
            adapter._restart_socket_mode("transport disconnected"), timeout=0.2
        )

        start_socket_mode_handler.assert_called_once_with()
        handler.close_async.assert_awaited_once_with()
        assert socket_task.done()
        assert adapter._socket_mode_close_task is None
    finally:
        release_close.set()
        if not restart_task.done():
            await asyncio.wait_for(restart_task, timeout=0.2)
        if not socket_task.done():
            socket_task.cancel()
            await asyncio.gather(socket_task, return_exceptions=True)
        close_task = getattr(adapter, "_socket_mode_close_task", None)
        if close_task is not None and not close_task.done():
            await asyncio.wait_for(close_task, timeout=0.2)


@pytest.mark.asyncio
async def test_connect_preserves_prior_handler_when_teardown_fails(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    old_app = MagicMock()
    old_handler = MagicMock()
    adapter._app = old_app
    adapter._handler = old_handler
    monkeypatch.setattr(
        adapter, "_stop_socket_mode_handler", AsyncMock(return_value=False)
    )

    with (
        patch.dict(os.environ, {"SLACK_APP_TOKEN": "xapp-test"}),
        patch("gateway.status.acquire_scoped_lock", return_value=(True, None)),
        patch("gateway.status.release_scoped_lock") as release_lock,
        patch.object(_slack_mod, "AsyncApp") as async_app,
    ):
        assert await adapter.connect() is False

    async_app.assert_not_called()
    release_lock.assert_not_called()
    assert adapter._app is old_app
    assert adapter._handler is old_handler


@pytest.mark.asyncio
async def test_disconnect_preserves_state_and_lock_when_teardown_fails(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    old_app = MagicMock()
    old_handler = MagicMock()
    adapter._app = old_app
    adapter._app_token = "xapp-test"
    adapter._proxy_url = "http://proxy.test"
    adapter._handler = old_handler
    release_lock = MagicMock()
    monkeypatch.setattr(
        adapter, "_stop_socket_mode_handler", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(adapter, "_release_platform_lock", release_lock)

    await adapter.disconnect()

    assert adapter._app is old_app
    assert adapter._app_token == "xapp-test"
    assert adapter._proxy_url == "http://proxy.test"
    assert adapter._handler is old_handler
    release_lock.assert_not_called()


@pytest.mark.asyncio
async def test_restart_retries_failed_close_before_replacing(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._running = True
    adapter._app = MagicMock()
    adapter._app_token = "xapp-test"
    handler = MagicMock()
    handler.close_async = AsyncMock(side_effect=[RuntimeError("close failed"), None])
    socket_task = asyncio.create_task(_never_finishes())
    adapter._handler = handler
    adapter._socket_mode_task = socket_task
    start_socket_mode_handler = MagicMock()
    monkeypatch.setattr(adapter, "_start_socket_mode_handler", start_socket_mode_handler)

    await adapter._restart_socket_mode("transport disconnected")

    start_socket_mode_handler.assert_not_called()
    assert adapter._handler is handler
    assert adapter._socket_mode_close_task is None

    await adapter._restart_socket_mode("transport disconnected")

    start_socket_mode_handler.assert_called_once_with()
    assert handler.close_async.await_count == 2
    assert socket_task.done()
    assert adapter._socket_mode_close_task is None


@pytest.mark.asyncio
async def test_restart_waits_for_cancellation_resistant_socket_task(monkeypatch):
    adapter = SlackAdapter(PlatformConfig(enabled=True, token="xoxb-test"))
    adapter._running = True
    adapter._app = MagicMock()
    adapter._app_token = "xapp-test"
    handler = MagicMock()
    handler.close_async = AsyncMock(return_value=None)
    release_task = asyncio.Event()

    async def resistant_socket_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_task.wait()

    socket_task = asyncio.create_task(resistant_socket_task())
    adapter._handler = handler
    adapter._socket_mode_task = socket_task
    start_socket_mode_handler = MagicMock()
    monkeypatch.setattr(adapter, "_start_socket_mode_handler", start_socket_mode_handler)
    monkeypatch.setattr(_slack_mod, "_SOCKET_MODE_CLOSE_TIMEOUT", 0.01, raising=False)

    try:
        await asyncio.wait_for(
            adapter._restart_socket_mode("transport disconnected"), timeout=0.2
        )

        start_socket_mode_handler.assert_not_called()
        assert adapter._handler is handler
        assert adapter._socket_mode_task is socket_task
        assert adapter._socket_mode_close_task is not None

        release_task.set()
        await asyncio.sleep(0)
        await asyncio.wait_for(
            adapter._restart_socket_mode("transport disconnected"), timeout=0.2
        )

        start_socket_mode_handler.assert_called_once_with()
        handler.close_async.assert_awaited_once_with()
        assert socket_task.done()
        assert adapter._socket_mode_close_task is None
    finally:
        release_task.set()
        if not socket_task.done():
            socket_task.cancel()
            await asyncio.gather(socket_task, return_exceptions=True)
