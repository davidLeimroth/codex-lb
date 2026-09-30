from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from aiohttp import web
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.claude_messages.api import MessagesRequest, from_chat_stream, router, to_chat
from app.modules.claude_messages.compat import check as runtime_compat_check

pytestmark = pytest.mark.integration

MODEL = "claude-opus-5-5"
TOKEN_LIMIT = [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000}]
# Shaped like the Claude CLI worker's chat stream: text, one whole tool call, finish, usage.
WORKER_CHUNKS: list[dict[str, Any]] = [
    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": "Checking"}, "finish_reason": None}]},
    {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call1",
                            "type": "function",
                            "function": {"name": "weather", "arguments": '{"city": "Paris"}'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    },
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 3,
            "total_tokens": 23,
            "prompt_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 2},
        },
    },
]

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture(autouse=True)
def messages_router(app_instance):
    app_instance.include_router(router)


@pytest.fixture
async def claude_source(async_client) -> AsyncIterator[Callable[[Handler], Awaitable[None]]]:
    """Registers an aiohttp fake of the worker as a codex-lb model source serving MODEL."""
    runners: list[web.AppRunner] = []

    async def start(handler: Handler) -> None:
        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(upstream)
        await runner.setup()
        runners.append(runner)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        response = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "Claude CLI test",
                "baseUrl": f"http://127.0.0.1:{port}/v1",
                "apiKey": "bridge-test",
                "supportsChatCompletions": True,
                "models": [{"model": MODEL, "supportsTools": True, "supportsStreaming": True}],
            },
        )
        assert response.status_code == 200, response.text

    yield start
    for runner in runners:
        await runner.cleanup()


def sse_handler(chunks: list[dict[str, Any]], *, done: bool = True) -> Handler:
    async def handler(request: web.Request) -> web.StreamResponse:
        assert request.headers["authorization"] == "Bearer bridge-test"
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for chunk in chunks:
            await response.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        if done:
            await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    return handler


async def _key(client, **fields) -> tuple[str, str]:
    response = await client.post("/api/api-keys/", json={"name": "claude-test", **fields})
    assert response.status_code == 200, response.text
    return response.json()["key"], response.json()["id"]


async def _token_counter(key_id: str) -> int:
    async with SessionLocal() as session:
        [limit] = await ApiKeysRepository(session).get_limits_by_key(key_id)
        return limit.current_value


async def _logs(key_id: str) -> list[RequestLog]:
    async with SessionLocal() as session:
        return list((await session.execute(select(RequestLog).where(RequestLog.api_key_id == key_id))).scalars())


def sse_events(body: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1]) for frame in body.strip().split("\n\n")]


def assert_blocks_sequential(events: list[dict[str, Any]]) -> None:
    """The real API closes block N before starting block N+1, and indexes count up from zero."""
    open_index: int | None = None
    started = 0
    for event in events:
        if event["type"] == "content_block_start":
            assert open_index is None and event["index"] == started
            open_index, started = started, started + 1
        elif event["type"] == "content_block_delta":
            assert event["index"] == open_index
        elif event["type"] == "content_block_stop":
            assert event["index"] == open_index
            open_index = None
    assert open_index is None


def test_runtime_compat_check_passes_for_this_tree():
    runtime_compat_check()  # the same check Dockerfile.claude runs inside the pinned runtime image


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/"])
async def test_messages_requires_valid_key_even_when_global_auth_disabled(async_client, path):
    response = await async_client.post(path, json={"model": MODEL})
    assert response.status_code == 401
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "authentication_error"
    response = await async_client.post(path, headers={"x-api-key": "invalid"}, json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_messages_rejects_conflicting_keys_and_unsupported_features(async_client):
    key, _ = await _key(async_client)
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]}
    response = await async_client.post(
        "/v1/messages", headers={"x-api-key": key, "authorization": "Bearer different"}, json=body
    )
    assert response.status_code == 401
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json={**body, "temperature": 0.5})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    image = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "data": "abc"}}]}]
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json={**body, "messages": image})
    assert response.status_code == 400
    server_tool = [{"type": "web_search_20250305", "name": "web_search"}]
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json={**body, "tools": server_tool})
    assert response.status_code == 400


def test_messages_tool_history_and_schema_survive_translation():
    payload = MessagesRequest.model_validate(
        {
            "model": MODEL,
            "max_tokens": 128,
            "system": [{"type": "text", "text": "Test system"}],
            "messages": [
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Checking"},
                        {"type": "tool_use", "id": "call1", "name": "weather", "input": {"city": "Athens"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call1", "content": "Sunny"},
                        {"type": "text", "text": "Summarize"},
                    ],
                },
            ],
            "tools": [
                {
                    "name": "weather",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        }
    )
    chat = to_chat(payload).model_dump()
    assert chat["messages"][0] == {"role": "system", "content": "Test system"}
    assert chat["messages"][2]["tool_calls"][0]["id"] == "call1"
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call1", "content": "Sunny"}
    assert chat["messages"][4]["content"] == "Summarize"
    assert chat["tools"][0]["function"]["parameters"]["properties"]["city"]["type"] == "string"
    assert chat["tool_choice"] == "auto" and chat["parallel_tool_calls"] is False


@pytest.mark.asyncio
async def test_messages_routes_through_source_and_settles_limited_key_usage(async_client, claude_source):
    recorded = []

    async def handler(request):
        recorded.append(await request.json())
        assert request.headers["authorization"] == "Bearer bridge-test"
        return web.json_response(
            {
                "id": "chat-test",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 3,
                    "total_tokens": 23,
                    "prompt_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 2},
                },
            }
        )

    await claude_source(handler)
    key, key_id = await _key(async_client, allowedModels=[MODEL], limits=TOKEN_LIMIT)
    response = await async_client.post(
        "/v1/messages",
        headers={"x-api-key": key},
        json={"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == [{"type": "text", "text": "Hello"}]
    assert response.json()["usage"] == {
        "input_tokens": 13,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 5,
        "output_tokens": 3,
    }
    assert len(recorded) == 1
    assert await _token_counter(key_id) == 23
    response = await async_client.post(
        "/v1/messages",
        headers={"authorization": "Bearer " + key},
        json={"model": "disallowed-model", "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"
    assert len(recorded) == 1
    assert [log.status for log in await _logs(key_id) if log.model == MODEL] == ["success"]
    async with SessionLocal() as session:
        reserved = select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        assert not (await session.execute(reserved)).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [False, True], ids=["passthrough", "limited-buffered"])
async def test_messages_stream_through_source_is_sequential_and_settles(async_client, claude_source, limited):
    await claude_source(sse_handler(WORKER_CHUNKS))
    key, key_id = await _key(async_client, **({"limits": TOKEN_LIMIT} if limited else {}))
    body = {"model": MODEL, "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "Hi"}]}
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json=body)
    assert response.status_code == 200, response.text
    events = sse_events(response.text)
    assert [event["type"] for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert_blocks_sequential(events)
    assert events[4]["content_block"] == {"type": "tool_use", "id": "call1", "name": "weather", "input": {}}
    assert events[5]["delta"] == {"type": "input_json_delta", "partial_json": '{"city": "Paris"}'}
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
    assert events[-2]["usage"] == {
        "input_tokens": 13,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 5,
        "output_tokens": 3,
    }
    [log] = await _logs(key_id)
    assert (log.status, log.output_tokens) == ("success", 3)
    if limited:
        assert await _token_counter(key_id) == 23


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [False, True], ids=["passthrough", "limited-buffered"])
async def test_messages_stream_mid_stream_error_settles_partial_usage(async_client, claude_source, limited):
    # The worker's in-band failure after output: a usage chunk, then the error frame, then EOF.
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [], "usage": {"prompt_tokens": 18, "completion_tokens": 2, "total_tokens": 20}},
        {"error": {"message": "boom", "type": "claude_cli_error", "code": "claude_cli_error"}},
    ]
    await claude_source(sse_handler(chunks, done=False))
    key, key_id = await _key(async_client, **({"limits": TOKEN_LIMIT} if limited else {}))
    body = {"model": MODEL, "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "Hi"}]}
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json=body)
    events = sse_events(response.text)
    assert events[-1] == {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    # The adapter drains the source stream, so codex-lb settles the partial usage instead of releasing it,
    # and records the in-band failure as an error rather than a success.
    [log] = await _logs(key_id)
    assert (log.status, log.error_code, log.error_message) == ("error", "claude_cli_error", "boom")
    assert (log.input_tokens, log.output_tokens) == (18, 2)
    if limited:
        assert await _token_counter(key_id) == 20
    async with SessionLocal() as session:
        reserved = select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.status == "reserved")
        assert not (await session.execute(reserved)).scalars().all()


@pytest.mark.asyncio
async def test_messages_maps_source_errors_to_anthropic_envelopes(async_client, claude_source):
    async def handler(request):
        await request.read()
        error = {"message": "Prompt is too long", "type": "invalid_request_error", "code": "context_length_exceeded"}
        return web.json_response({"error": error}, status=400)

    await claude_source(handler)
    key, _ = await _key(async_client)
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]}
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json=body)
    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Prompt is too long"},
    }


@pytest.mark.asyncio
async def test_messages_malformed_source_body_is_an_anthropic_error(async_client, claude_source):
    async def handler(request):
        await request.read()
        return web.json_response({"id": "chat-test", "choices": []})

    await claude_source(handler)
    key, _ = await _key(async_client)
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]}
    response = await async_client.post("/v1/messages", headers={"x-api-key": key}, json=body)
    assert response.status_code == 502
    assert response.json()["type"] == "error" and response.json()["error"]["type"] == "api_error"


def _wire(chunks: list[dict[str, Any]]) -> bytes:
    frames = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\r\n\r\n" for c in chunks)
    return (frames + "data: [DONE]\r\n\r\n").encode()


async def _translate(wire: bytes, closed: list[bool]) -> list[dict[str, Any]]:
    async def upstream() -> AsyncIterator[bytes]:
        try:
            for offset in range(0, len(wire), 3):
                yield wire[offset : offset + 3]
        finally:
            closed.append(True)

    result = b"".join([part async for part in from_chat_stream(StreamingResponse(upstream()), MODEL)]).decode()
    return sse_events(result)


def _tool_delta(index: int, arguments: str, *, call_id: str = "", name: str = "") -> dict[str, Any]:
    call: dict[str, Any] = {"index": index, "function": {"arguments": arguments}}
    if call_id:
        call["id"] = call_id
        call["function"]["name"] = name
    return {"choices": [{"delta": {"tool_calls": [call]}, "finish_reason": None}]}


def _json_deltas(events: list[dict[str, Any]]) -> list[str]:
    return [e["delta"]["partial_json"] for e in events if e.get("delta", {}).get("type") == "input_json_delta"]


@pytest.mark.asyncio
async def test_messages_stream_fragmented_unicode_tool_arguments_and_usage():
    chunks = [
        {"choices": [{"delta": {"content": "Γεια"}, "finish_reason": None}]},
        _tool_delta(0, '{"city":', call_id="call1", name="weather"),
        _tool_delta(0, '"Athens"}'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    closed: list[bool] = []
    events = await _translate(_wire(chunks), closed)
    assert events[0]["type"] == "message_start"
    assert any(event.get("delta", {}).get("text") == "Γεια" for event in events)
    assert_blocks_sequential(events)
    [arguments] = _json_deltas(events)
    assert json.loads(arguments) == {"city": "Athens"}
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
    assert events[-2]["usage"]["output_tokens"] == 5
    assert events[-1]["type"] == "message_stop"
    assert closed == [True]


@pytest.mark.asyncio
async def test_messages_stream_serializes_interleaved_parallel_tool_calls():
    chunks = [
        _tool_delta(0, '{"city":', call_id="call_a", name="weather"),
        _tool_delta(1, '{"q":', call_id="call_b", name="search"),
        _tool_delta(0, ' "Paris"}'),
        {"choices": [{"delta": {"content": "late text"}, "finish_reason": None}]},
        _tool_delta(1, ' "news"}'),
        {"choices": [{"delta": {}, "finish_reason": "content_filter"}]},
    ]
    events = await _translate(_wire(chunks), [])
    assert_blocks_sequential(events)
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    assert [(block["type"], block.get("id")) for block in starts] == [
        ("text", None),
        ("tool_use", "call_a"),
        ("tool_use", "call_b"),
    ]
    assert _json_deltas(events) == ['{"city": "Paris"}', '{"q": "news"}']
    assert events[-2]["delta"]["stop_reason"] == "refusal"


@pytest.mark.asyncio
async def test_messages_stream_malformed_frame_becomes_error_event():
    wire = b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: {not json}\n\ndata: [DONE]\n\n'
    closed: list[bool] = []
    events = await _translate(wire, closed)
    assert events[-1] == {"type": "error", "error": {"type": "api_error", "message": "Malformed upstream stream"}}
    assert closed == [True]


@pytest.mark.asyncio
async def test_messages_truncated_upstream_stream_returns_error_without_success():
    async def upstream() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'

    result = b"".join([part async for part in from_chat_stream(StreamingResponse(upstream()), "claude-opus-5-5")])
    assert b'"type": "error"' in result
    assert b'"type": "message_stop"' not in result
