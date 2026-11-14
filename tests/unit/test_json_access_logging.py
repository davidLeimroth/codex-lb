from __future__ import annotations

import asyncio
import io
import json
import logging
import socket
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from starlette.types import Receive, Scope, Send

from app.core.runtime_logging import JsonAccessFormatter

pytestmark = pytest.mark.unit


@pytest.fixture
def access_logger(monkeypatch) -> Iterator[logging.Logger]:
    logger = logging.getLogger("uvicorn.access")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    monkeypatch.setattr(logger, "disabled", False)
    try:
        yield logger
    finally:
        logger.setLevel(old_level)


@pytest.mark.asyncio
async def test_native_uvicorn_http_request_emits_json_access_fields(monkeypatch, access_logger):
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonAccessFormatter())
    monkeypatch.setattr(access_logger, "handlers", [handler])
    monkeypatch.setattr(access_logger, "propagate", False)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, access_log=True))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    pytest.fail("Uvicorn stopped before startup")
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(
                    f"http://127.0.0.1:{sock.getsockname()[1]}/health/ready?api_key=private-test-value"
                )
            assert response.status_code == 200
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            sock.close()

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(records) == 1
    assert records[0]["client"].startswith("127.0.0.1:")
    assert records[0]["request"] == "GET /health/ready HTTP/1.1"
    assert records[0]["status"] == 200
    assert "private-test-value" not in output.getvalue()


@pytest.mark.parametrize(
    "args",
    [(), ("private-value",), ("client", "GET", "/", "1.1", "private-value"), ("client", "GET", "/", "1.1", True)],
)
def test_malformed_native_arguments_remain_null_without_raw_data(args):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "private-message", args, None)
    raw = JsonAccessFormatter().format(record)
    result = json.loads(raw)
    assert [result[field] for field in ("client", "request", "status")] == [None, None, None]
    assert "private" not in raw


def test_explicit_access_fields_take_precedence_over_native_args():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "", ("other", "GET", "/other", "1.1", 404), None)
    record.client_addr = "explicit-client"
    record.request_line = "POST /explicit?token=private-value HTTP/1.1"
    record.status_code = 201
    result = json.loads(JsonAccessFormatter().format(record))
    assert result["client"] == "explicit-client"
    assert result["request"] == "POST /explicit HTTP/1.1"
    assert result["status"] == 201


def test_partial_explicit_override_preserves_other_native_fields():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "",
        ("127.0.0.1:1", "GET", "/health/ready?token=private-value", "1.1", 200),
        None,
    )
    record.status_code = 503
    result = json.loads(JsonAccessFormatter().format(record))
    assert (result["client"], result["request"], result["status"]) == ("127.0.0.1:1", "GET /health/ready HTTP/1.1", 503)
