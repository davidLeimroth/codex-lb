"""Standalone OpenAI-compatible bridge: ``uvicorn app.modules.claude_cli.bridge:app --port 2456``."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable
from contextlib import aclosing, asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import JsonValue, ValidationError

from app.modules.claude_cli.schemas import (
    BridgeError,
    ChatCompletionRequest,
    Completion,
    ResponsesRequest,
    TextDelta,
    Usage,
)
from app.modules.claude_cli.service import (
    BridgeConfig,
    ClaudeCliService,
    chat_to_generation,
    responses_to_generation,
)

PORT = 2456
_MAX_BODY_BYTES = 8 * 1024 * 1024
_CHAT_FINISH = {"tool_use": "tool_calls", "max_tokens": "length", "refusal": "content_filter"}

type Events = AsyncGenerator[TextDelta | Completion, None]


def _error_body(exc: BridgeError) -> dict[str, JsonValue]:
    return {"error": {"message": exc.message, "type": exc.error_type, "code": exc.code}}


def _sse(payload: dict[str, JsonValue], event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload)}\n\n"


def _chat_usage(usage: Usage) -> dict[str, JsonValue]:
    # cache_write_tokens is an extension (prompt_tokens includes it) so Messages can report cache creation.
    details = {"cached_tokens": usage.cache_read_tokens, "cache_write_tokens": usage.cache_creation_tokens}
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.prompt_tokens + usage.output_tokens,
        "prompt_tokens_details": details,
    }


def _responses_usage(usage: Usage) -> dict[str, JsonValue]:
    details = {"cached_tokens": usage.cache_read_tokens, "cache_write_tokens": usage.cache_creation_tokens}
    return {
        "input_tokens": usage.prompt_tokens,
        "input_tokens_details": details,
        "output_tokens": usage.output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": usage.prompt_tokens + usage.output_tokens,
    }


class _ChatEncoder:
    def __init__(self, model: str) -> None:
        self.id, self.created, self.model = f"chatcmpl-{uuid.uuid4().hex}", int(time.time()), model

    def completion(self, done: Completion) -> dict[str, JsonValue]:
        message: dict[str, JsonValue] = {"role": "assistant", "content": done.text or None}
        if done.tool_calls:
            message["tool_calls"] = self._tool_calls(done, indexed=False)
        choice = {"index": 0, "message": message, "finish_reason": _CHAT_FINISH.get(done.stop_reason, "stop")}
        return self._base("chat.completion", [choice]) | {"usage": _chat_usage(done.usage)}

    def start(self) -> list[str]:
        return [self._chunk({"role": "assistant", "content": ""})]

    def feed(self, event: TextDelta | Completion) -> list[str]:
        if isinstance(event, TextDelta):
            return [self._chunk({"content": event.text})]
        frames = [self._chunk({"tool_calls": self._tool_calls(event, indexed=True)})] if event.tool_calls else []
        frames.append(self._chunk({}, _CHAT_FINISH.get(event.stop_reason, "stop")))
        return [*frames, self._usage(event.usage), "data: [DONE]\n\n"]

    def error(self, exc: BridgeError) -> list[str]:
        # The usage chunk first: codex-lb's source stream parser settles whatever usage it last saw.
        return [*([self._usage(exc.usage)] if exc.usage else []), _sse(_error_body(exc))]

    def _usage(self, usage: Usage) -> str:
        return _sse(self._base("chat.completion.chunk", []) | {"usage": _chat_usage(usage)})

    def _base(self, kind: str, choices: list[JsonValue]) -> dict[str, JsonValue]:
        return {"id": self.id, "object": kind, "created": self.created, "model": self.model, "choices": choices}

    def _chunk(self, delta: dict[str, JsonValue], finish: str | None = None) -> str:
        return _sse(self._base("chat.completion.chunk", [{"index": 0, "delta": delta, "finish_reason": finish}]))

    @staticmethod
    def _tool_calls(done: Completion, *, indexed: bool) -> list[JsonValue]:
        return [
            ({"index": index} if indexed else {})
            | {"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for index, call in enumerate(done.tool_calls)
        ]


class _ResponsesEncoder:
    """Emits the ordered Responses lifecycle; ``final`` holds the completed response object."""

    def __init__(self, model: str) -> None:
        self.id, self.created, self.model = f"resp_{uuid.uuid4().hex}", int(time.time()), model
        self.sequence = 0
        self.output: list[JsonValue] = []
        self.text: list[str] = []
        self.message_id = f"msg_{uuid.uuid4().hex}"
        self.final: dict[str, JsonValue] = {}

    def start(self) -> list[str]:
        return [
            self._event("response.created", response=self._response("in_progress")),
            self._event("response.in_progress", response=self._response("in_progress")),
        ]

    def feed(self, event: TextDelta | Completion) -> list[str]:
        if isinstance(event, TextDelta):
            frames = [] if self.text else self._open_message()
            self.text.append(event.text)
            ids = {"item_id": self.message_id, "output_index": len(self.output), "content_index": 0}
            return [*frames, self._event("response.output_text.delta", **ids, delta=event.text)]
        truncated = event.stop_reason == "max_tokens"
        frames = self._close_message("incomplete" if truncated else "completed")
        for call in event.tool_calls:
            item: dict[str, JsonValue] = {"type": "function_call", "id": f"fc_{uuid.uuid4().hex}"}
            item |= {"status": "completed", "call_id": call.call_id, "name": call.name, "arguments": call.arguments}
            ids = {"item_id": item["id"], "output_index": len(self.output)}
            frames += [
                self._event("response.output_item.added", output_index=len(self.output), item=item | {"arguments": ""}),
                self._event("response.function_call_arguments.delta", **ids, delta=call.arguments),
                self._event("response.function_call_arguments.done", **ids, arguments=call.arguments),
                self._event("response.output_item.done", output_index=len(self.output), item=item),
            ]
            self.output.append(item)
        status = "incomplete" if truncated else "completed"
        self.final = self._response(status, event.usage)
        if truncated:
            self.final["incomplete_details"] = {"reason": "max_output_tokens"}
        return [*frames, self._event(f"response.{status}", response=self.final)]

    def error(self, exc: BridgeError) -> list[str]:
        failed = self._response("failed", exc.usage) | {"error": {"code": exc.code, "message": exc.message}}
        return [self._event("response.failed", response=failed)]

    def _open_message(self) -> list[str]:
        item = {"type": "message", "id": self.message_id, "status": "in_progress", "role": "assistant", "content": []}
        part = {"type": "output_text", "text": "", "annotations": []}
        ids = {"item_id": self.message_id, "output_index": len(self.output), "content_index": 0}
        return [
            self._event("response.output_item.added", output_index=len(self.output), item=item),
            self._event("response.content_part.added", **ids, part=part),
        ]

    def _close_message(self, status: str) -> list[str]:
        if not self.text:
            return []
        text = "".join(self.text)
        part: dict[str, JsonValue] = {"type": "output_text", "text": text, "annotations": []}
        item = {"type": "message", "id": self.message_id, "status": status, "role": "assistant", "content": [part]}
        ids = {"item_id": self.message_id, "output_index": len(self.output), "content_index": 0}
        frames = [
            self._event("response.output_text.done", **ids, text=text),
            self._event("response.content_part.done", **ids, part=part),
            self._event("response.output_item.done", output_index=len(self.output), item=item),
        ]
        self.output.append(item)
        return frames

    def _response(self, status: str, usage: Usage | None = None) -> dict[str, JsonValue]:
        return {
            "id": self.id, "object": "response", "created_at": self.created, "status": status, "model": self.model,
            "output": list(self.output), "usage": _responses_usage(usage) if usage else None,
            "error": None, "incomplete_details": None,
        }  # fmt: skip

    def _event(self, kind: str, **fields: JsonValue) -> str:
        self.sequence += 1
        return _sse({"type": kind, "sequence_number": self.sequence - 1, **fields}, kind)


async def _read_json(request: Request) -> JsonValue:
    if int(request.headers.get("content-length") or 0) > _MAX_BODY_BYTES:
        raise BridgeError(413, "request_too_large", "Request body exceeds the bridge limit")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            raise BridgeError(413, "request_too_large", "Request body exceeds the bridge limit")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise BridgeError(400, "invalid_request_error", "Request body must be JSON") from exc


async def _completion(events: Events) -> Completion:
    async with aclosing(events) as stream:
        async for event in stream:
            if isinstance(event, Completion):
                return event
    raise BridgeError(502, "claude_cli_failed", "Claude CLI produced no completion")


async def _until_disconnect[T](work: Awaitable[T], request: Request) -> T:
    """Handlers are not cancelled on disconnect before a response starts: poll for it and cancel the work."""
    task = asyncio.ensure_future(work)
    try:
        while not (await asyncio.wait({task}, timeout=1.0))[0]:
            if await request.is_disconnected():
                raise BridgeError(499, "client_disconnected", "Client disconnected")
        return task.result()
    finally:
        if not task.done():
            task.cancel()  # unwinds the generator, which reaps the CLI and releases the account
            with suppress(asyncio.CancelledError):
                await task


async def _stream(events: Events, encoder: _ChatEncoder | _ResponsesEncoder, request: Request) -> Response:
    try:
        # Errors before any output become real HTTP errors; required-tool text is buffered until then.
        first = await _until_disconnect(anext(events), request)
    except BaseException:
        await events.aclose()
        raise

    async def body() -> AsyncIterator[str]:
        async with aclosing(events) as stream:
            event = first
            try:
                for frame in encoder.start():
                    yield frame
                while True:
                    for frame in encoder.feed(event):
                        yield frame
                    if isinstance(event, Completion):
                        return
                    event = await anext(stream)
            except BridgeError as exc:
                for frame in encoder.error(exc):
                    yield frame

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def create_app(config: BridgeConfig | None = None) -> FastAPI:
    """With no explicit config, settings load from the environment at startup (fail fast)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config is None:
            env_config = BridgeConfig.from_env()
            app.state.config, app.state.service = env_config, ClaudeCliService(env_config)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if config is not None:
        app.state.config, app.state.service = config, ClaudeCliService(config)

    def authorized(request: Request) -> ClaudeCliService:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        expected: str = request.app.state.config.api_key
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
            raise BridgeError(401, "invalid_api_key", "Missing or invalid bridge bearer token")
        return request.app.state.service

    @app.exception_handler(BridgeError)
    async def bridge_error(_: Request, exc: BridgeError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(_error_body(exc), status_code=exc.status, headers=headers)

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False))
        return JSONResponse(_error_body(BridgeError(400, "invalid_request_error", detail)), status_code=400)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        accounts = await request.app.state.service.health()
        ready = sum(accounts.values())
        payload = {"status": "ok" if ready else "unavailable", "ready_accounts": ready, "accounts": len(accounts)}
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        data = [{"id": model, "object": "model", "created": 0, "owned_by": "anthropic"}
                for model in authorized(request).model_ids()]  # fmt: skip
        return JSONResponse({"object": "list", "data": data})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        service = authorized(request)
        payload = ChatCompletionRequest.model_validate(await _read_json(request))
        model = service.resolve_model(payload.model)
        events, encoder = service.generate(chat_to_generation(payload, model)), _ChatEncoder(model)
        if payload.stream:
            return await _stream(events, encoder, request)
        return JSONResponse(encoder.completion(await _until_disconnect(_completion(events), request)))

    @app.post("/v1/responses")
    async def responses(request: Request) -> Response:
        service = authorized(request)
        payload = ResponsesRequest.model_validate(await _read_json(request))
        model = service.resolve_model(payload.model)
        events, encoder = service.generate(responses_to_generation(payload, model)), _ResponsesEncoder(model)
        if payload.stream:
            return await _stream(events, encoder, request)
        done = await _until_disconnect(_completion(events), request)
        if done.text:
            encoder.feed(TextDelta(done.text))
        encoder.feed(done)
        return JSONResponse(encoder.final)

    return app


app = create_app()
