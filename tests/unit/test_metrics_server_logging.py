from __future__ import annotations

import asyncio
import io
import json
import logging
import socket

import httpx
import pytest
import uvicorn

from app.core.config.settings import get_settings
from app.core.runtime_logging import JsonAccessFormatter
from app.main import create_app

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_native_metrics_startup_preserves_main_json_access_logging(app_instance, monkeypatch):
    del app_instance  # Reuse the isolated database and native lifespan fixture.
    main_socket = socket.socket()
    main_socket.bind(("127.0.0.1", 0))
    with socket.socket() as metrics_socket:
        metrics_socket.bind(("127.0.0.1", 0))
        metrics_port = metrics_socket.getsockname()[1]
    monkeypatch.setenv("CODEX_LB_METRICS_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_METRICS_PORT", str(metrics_port))
    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", "json")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN", "synthetic-metrics-logging-test")
    get_settings.cache_clear()
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonAccessFormatter())
    names = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
    saved = {
        name: (logger.level, logger.handlers[:], logger.propagate, logger.disabled)
        for name in names
        if (logger := logging.getLogger(name))
    }
    access = logging.getLogger("uvicorn.access")
    access.handlers = [handler]
    access.setLevel(logging.INFO)
    access.propagate = False
    access.disabled = False
    server = uvicorn.Server(uvicorn.Config(create_app(), log_config=None))
    task = asyncio.create_task(server.serve(sockets=[main_socket]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    task.result()
                    pytest.fail("Native server exited before startup")
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(trust_env=False) as client:
                while True:
                    try:
                        metrics = await client.get(f"http://127.0.0.1:{metrics_port}/metrics?token=private-metrics")
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.01)
                main = await client.get(
                    f"http://127.0.0.1:{main_socket.getsockname()[1]}/health/live?api_key=private-main"
                )
            assert metrics.status_code == 200 and "# TYPE" in metrics.text
            assert main.status_code == 200
            assert access.handlers == [handler], "Metrics startup replaced the primary access handler"
            assert access.level == logging.INFO and not access.propagate and not access.disabled
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            assert {(r["request"], r["status"]) for r in records} == {
                ("GET /metrics HTTP/1.1", 200),
                ("GET /health/live HTTP/1.1", 200),
            }
            assert all(r["client"].startswith("127.0.0.1:") for r in records)
            assert "private-" not in output.getvalue()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=15)
        finally:
            main_socket.close()
            get_settings.cache_clear()
            for name, (level, handlers, propagate, disabled) in saved.items():
                logger = logging.getLogger(name)
                logger.setLevel(level)
                logger.handlers = handlers
                logger.propagate = propagate
                logger.disabled = disabled
