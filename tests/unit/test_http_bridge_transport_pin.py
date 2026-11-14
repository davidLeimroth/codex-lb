from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.modules.proxy._service.http_bridge import streaming

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("global_policy", "override", "transport", "expected"),
    [
        ("smart", None, "auto", "bridge"),
        ("always_http", None, "websocket", "bridge"),
        ("smart", "always_websocket", "auto", "bridge"),
        ("always_http", "smart", "auto", "bridge"),
        ("smart", "always_http", "auto", "http"),
        ("smart", None, "http", "http"),
    ],
)
async def test_http_pin_admission_preserves_transport_precedence_and_client_anchor(
    monkeypatch, global_policy, override, transport, expected
):
    service = proxy_module.ProxyService(cast(Any, nullcontext()))
    settings = Settings(http_responses_session_bridge_enabled=True)
    dashboard = SimpleNamespace(
        upstream_stream_transport=transport,
        http_downstream_transport_policy=global_policy,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    monkeypatch.setattr(streaming, "_service_get_settings", lambda: settings)
    monkeypatch.setattr(
        streaming, "_service_get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=dashboard))
    )
    monkeypatch.setattr(service, "_resolve_forwarded_file_account_for_responses", AsyncMock(return_value=None))
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "test",
            "input": [{"role": "user", "content": "continue"}],
            "previous_response_id": "resp_client",
        }
    )
    original = payload.to_payload()

    async def bridge(body, *_args, **_kwargs):
        assert body.to_payload() == original
        yield "bridge"

    async def http(body, *_args, **_kwargs):
        assert body.to_payload() == original
        yield "http"

    monkeypatch.setattr(service, "_stream_via_http_bridge", bridge)
    monkeypatch.setattr(service, "_stream_with_retry", http)
    result = [
        event
        async for event in service.stream_http_responses(
            payload, {}, api_key=cast(Any, SimpleNamespace(transport_policy_override=override))
        )
    ]
    assert result == [expected]
