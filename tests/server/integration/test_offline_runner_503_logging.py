"""Offline-runner resource 503s must not be logged as ERROR + traceback.

A runner being offline is a normal operational state (host reboot,
idle-reap, tunnel drop). The resource-proxy routes correctly answer
503 ``runner_unavailable``, but the ``OmnigentError`` handler must not
bury genuine errors by emitting a stack trace for each such hit.
"""

import logging

import httpx
import pytest

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime import _globals, set_runner_router
from tests.server.helpers import create_test_session


class _OfflineRunnerRouter:
    """Router whose pinned runner is gone from the registry."""

    def client_for_session_resources(self, session_id: str) -> object:
        raise OmnigentError(
            f"runner 'runner_token_offline' is offline for conversation {session_id!r}",
            code=ErrorCode.RUNNER_UNAVAILABLE,
        )


async def test_offline_runner_resource_503_is_not_logged_as_error(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An expected offline-runner 503 stays below ERROR and carries no traceback.

    :param client: HTTP client wired to the real app (real exception handler).
    :param caplog: Pytest log-capture fixture.
    """
    snapshot = await create_test_session(client)
    conv_id = snapshot["id"]

    prior = _globals._runner_router
    set_runner_router(_OfflineRunnerRouter())  # type: ignore[arg-type]
    try:
        with caplog.at_level(logging.DEBUG, logger="omnigent.server.app"):
            resp = await client.get(f"/v1/sessions/{conv_id}/resources/environments/env_default")
    finally:
        set_runner_router(prior)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "runner_unavailable"

    app_records = [r for r in caplog.records if r.name == "omnigent.server.app"]
    error_records = [r for r in app_records if r.levelno >= logging.ERROR]
    with_traceback = [r for r in app_records if r.exc_info]
    assert not error_records, "expected offline-runner 503 logged at ERROR: " + "; ".join(
        r.getMessage() for r in error_records
    )
    assert not with_traceback, "offline-runner 503 logged with a traceback"

    # The outage must stay visible in logs — just below ERROR.
    warnings = [r for r in app_records if r.levelno == logging.WARNING]
    assert any("offline" in r.getMessage() for r in warnings)
