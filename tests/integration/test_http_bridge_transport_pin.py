from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings
from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("path", "stream"),
    [
        ("/backend-api/codex/responses", True),
        ("/backend-api/codex/responses/", True),
        ("/v1/responses", True),
        ("/v1/responses/", True),
        ("/v1/responses", False),
    ],
)
@pytest.mark.parametrize(
    ("override", "global_policy", "configured_transport"),
    [
        ("always_http", "smart", "auto"),
        (None, "always_http", "auto"),
        (None, "pinned", "auto"),
        ("always_websocket", "smart", "http"),
    ],
)
async def test_http_pin_bypasses_enabled_bridge_without_injecting_stale_anchor(
    async_client, monkeypatch, path, stream, override, global_policy, configured_transport
):
    claims = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "email": "http-pin@example.com",
                    "chatgpt_account_id": "http-pin",
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    auth = {"tokens": {"idToken": f"header.{claims}.sig", "accessToken": "test-access", "refreshToken": "test-refresh"}}
    response = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
    )
    assert response.status_code == 200
    response = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert response.status_code == 200
    response = await async_client.post("/api/api-keys/", json={"name": "http-pin", "transportPolicyOverride": override})
    assert response.status_code == 200
    key = response.json()
    dashboard = await proxy_module.get_settings_cache().get()
    dashboard.upstream_stream_transport = configured_transport
    dashboard.http_downstream_transport_policy = global_policy
    monkeypatch.setattr(proxy_module, "get_settings", lambda: Settings(http_responses_session_bridge_enabled=True))
    monkeypatch.setattr(
        proxy_module, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=dashboard))
    )
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda a, **_: a))
    captured = []

    async def forbidden_bridge(*args, **kwargs):
        raise AssertionError("HTTP pin entered the WebSocket bridge and could reuse its rejected anchor")
        yield ""

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        captured.append((payload.to_payload(), kwargs.get("upstream_stream_transport_override")))
        response_id = f"resp_http_pin_{len(captured)}"
        for event in [
            {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}},
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            },
        ]:
            yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(proxy_module.ProxyService, "_stream_via_http_bridge", forbidden_bridge)
    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    history = [{"role": "user", "content": "hello"}]
    headers = {"authorization": f"Bearer {key['key']}", "session_id": "http-pin-thread"}
    for turn in range(2):
        response = await async_client.post(
            path,
            json={"model": "gpt-5.4", "instructions": "test", "input": history, "stream": stream},
            headers=headers,
            follow_redirects=True,
        )
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert any(event.get("type") == "response.completed" for event in events), response.text
        else:
            assert response.json()["status"] == "completed"
        payload, transport = captured[turn]
        assert transport == "http"
        assert payload["input"] == history
        assert not payload.get("previous_response_id")
        history = [
            *history,
            {"role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
            {"role": "user", "content": "continue"},
        ]
    async with SessionLocal() as session:
        logs = (await session.execute(select(RequestLog).where(RequestLog.api_key_id == key["id"]))).scalars().all()
    assert len(logs) == 2
    assert all(log.status == "success" and log.upstream_transport == "http" for log in logs)
