"""Tests for wolf_comm.create_session."""
from unittest.mock import AsyncMock

import httpx

from wolf_comm.create_session import create_session, update_session


async def test_create_session_returns_browser_session_id():
    http = AsyncMock()
    http.post = AsyncMock(
        return_value=httpx.Response(200, json={"BrowserSessionId": 17})
    )

    session_id = await create_session(http, "test-token")

    assert session_id == 17
    url = http.post.call_args.args[0]
    assert url.endswith("/api/portal/CreateSession2")
    kwargs = http.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    # Payload carries a "%Y-%m-%d %H:%M:%S" timestamp.
    assert "Timestamp" in kwargs["json"]
    assert len(kwargs["json"]["Timestamp"]) == 19


async def test_update_session_posts_session_id():
    http = AsyncMock()
    http.post = AsyncMock(return_value=httpx.Response(200, json={}))

    await update_session(http, "test-token", 17)

    url = http.post.call_args.args[0]
    assert url.endswith("/api/portal/UpdateSession")
    kwargs = http.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["json"] == {"SessionId": 17}
