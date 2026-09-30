from __future__ import annotations

import codecs
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
from app.core.exceptions import AppError
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.api import v1_chat_completions

router = APIRouter(prefix="/v1", tags=["anthropic"])


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class Tool(BaseModel):
    """Client tools only; server tools (web search etc.) have no input_schema and are rejected."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["custom"] | None = None
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any]
    cache_control: dict[str, Any] | None = None  # accepted and ignored, as on content blocks


class MessagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    system: str | list[dict[str, Any]] = ""
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: dict[str, Any] | None = None
    stream: bool = False
    metadata: dict[str, Any] | None = None


_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    529: "overloaded_error",
}


def error_response(status: int, message: str, error_type: str | None = None) -> JSONResponse:
    kind = error_type or _ERROR_TYPES.get(status, "api_error")
    return JSONResponse({"type": "error", "error": {"type": kind, "message": message}}, status_code=status)


def _upstream_error(response: Response) -> JSONResponse:
    message = "Upstream error"
    with suppress(AttributeError, TypeError, ValueError):  # non-JSON, non-object or streaming bodies
        error = json.loads(bytes(response.body)).get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
    native = error_response(response.status_code, message)
    if retry_after := response.headers.get("retry-after"):
        native.headers["Retry-After"] = retry_after
    return native


def _text(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    if any(block.get("type") != "text" for block in content):
        raise ValueError("Only text blocks are supported in system and tool results")
    return "\n".join(block["text"] for block in content)


def to_chat(payload: MessagesRequest) -> ChatCompletionsRequest:
    messages: list[dict[str, Any]] = []
    system = _text(payload.system)
    if system:
        messages.append({"role": "system", "content": system})
    for message in payload.messages:
        if isinstance(message.content, str):
            messages.append({"role": message.role, "content": message.content})
            continue
        text: list[str] = []
        calls: list[dict[str, Any]] = []

        def flush() -> None:
            if text or calls:
                entry: dict[str, Any] = {"role": message.role, "content": "\n".join(text) or None}
                if calls:
                    entry["tool_calls"] = list(calls)
                messages.append(entry)
                text.clear()
                calls.clear()

        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                text.append(block["text"])
            elif kind == "tool_use" and message.role == "assistant":
                calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    }
                )
            elif kind == "tool_result" and message.role == "user":
                flush()
                content = _text(block.get("content", ""))
                if block.get("is_error"):
                    content = "Tool error: " + content
                messages.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": content})
            else:
                raise ValueError(f"Unsupported {message.role} content block: {kind}")
        flush()
    choice: str | dict[str, Any] | None = None
    parallel: dict[str, Any] = {}
    if payload.tool_choice:
        if payload.tool_choice.get("disable_parallel_tool_use") is True:
            parallel["parallel_tool_calls"] = False
        kind = payload.tool_choice.get("type")
        if kind == "auto":
            choice = "auto"
        elif kind == "none":
            choice = "none"
        elif kind == "any":
            choice = "required"
        elif kind == "tool" and payload.tool_choice.get("name"):
            choice = {"type": "function", "function": {"name": payload.tool_choice["name"]}}
        else:
            raise ValueError("Unsupported tool_choice")
    return ChatCompletionsRequest.model_validate(
        {
            "model": payload.model,
            "messages": messages,
            "stream": payload.stream,
            "max_tokens": payload.max_tokens,
            "tool_choice": choice,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in payload.tools
            ],
            "stream_options": {"include_usage": True},
            **parallel,
        }
    )


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") or 0
    written = details.get("cache_write_tokens") or 0  # the Claude CLI worker's extension field
    return {
        "input_tokens": max(0, (usage.get("prompt_tokens") or 0) - cached - written),
        "cache_creation_input_tokens": written,
        "cache_read_input_tokens": cached,
        "output_tokens": usage.get("completion_tokens") or 0,
    }


def from_chat(data: dict[str, Any], model: str) -> dict[str, Any]:
    choice = data["choices"][0]
    message = choice["message"]
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        content.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": call["function"]["name"],
                "input": json.loads(call["function"]["arguments"]),
            }
        )
    return {
        "id": "msg_" + uuid4().hex,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _stop(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": _usage(data.get("usage") or {}),
    }


def _stop(reason: str | None) -> str:
    return {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(
        reason or "stop", "end_turn"
    )


def _sse(kind: str, **data: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode()


class _ChatStreamState:
    """Accumulates one chat completion stream. Text streams live as block 0; tool calls are kept whole."""

    def __init__(self) -> None:
        self.text_open = False
        self.tools: dict[int, tuple[list[str], list[str]]] = {}  # source index -> (id and name, argument parts)
        self.usage: dict[str, Any] = {}
        self.reason: str | None = None
        self.error: str | None = None

    def feed(self, data: str) -> list[bytes]:
        chunk = json.loads(data)
        if "error" in chunk:
            error = chunk["error"]
            message = error.get("message") if isinstance(error, dict) else None
            self.error = message if isinstance(message, str) else "Upstream error"
            return []
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        events: list[bytes] = []
        for choice in chunk.get("choices") or []:
            self.reason = choice.get("finish_reason") or self.reason
            delta = choice.get("delta") or {}
            if delta.get("content"):
                if not self.text_open:
                    self.text_open = True
                    events.append(_sse("content_block_start", index=0, content_block={"type": "text", "text": ""}))
                events.append(
                    _sse("content_block_delta", index=0, delta={"type": "text_delta", "text": delta["content"]})
                )
            for call in delta.get("tool_calls") or []:
                function = call.get("function") or {}
                header, arguments = self.tools.setdefault(int(call["index"]), ([], []))
                if not header and call.get("id") and function.get("name"):
                    header.extend((call["id"], function["name"]))
                if function.get("arguments"):
                    arguments.append(function["arguments"])
        return events

    def finish(self) -> list[bytes]:
        """Close text, then emit each tool as a complete block, so no block opens before the last closed."""
        events = [_sse("content_block_stop", index=0)] if self.text_open else []
        for index, (_, (header, arguments)) in enumerate(sorted(self.tools.items()), start=len(events)):
            if not header:
                raise ValueError("Upstream tool call without id or name")
            block = {"type": "tool_use", "id": header[0], "name": header[1], "input": {}}
            events.append(_sse("content_block_start", index=index, content_block=block))
            if arguments:
                delta = {"type": "input_json_delta", "partial_json": "".join(arguments)}
                events.append(_sse("content_block_delta", index=index, delta=delta))
            events.append(_sse("content_block_stop", index=index))
        delta = {"stop_reason": _stop(self.reason), "stop_sequence": None}
        return [*events, _sse("message_delta", delta=delta, usage=_usage(self.usage)), _sse("message_stop")]


async def from_chat_stream(response: StreamingResponse, model: str) -> AsyncIterator[bytes]:
    buffer = ""
    state = _ChatStreamState()
    decoder = codecs.getincrementaldecoder("utf-8")()
    yield _sse(
        "message_start",
        message={
            "id": "msg_" + uuid4().hex,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": _usage({}),
        },
    )
    try:
        async for raw in response.body_iterator:
            if state.error is not None:
                continue  # drain to the end so codex-lb settles the usage reported before the error
            buffer += raw if isinstance(raw, str) else decoder.decode(bytes(raw))
            buffer = buffer.replace("\r\n", "\n")
            while "\n\n" in buffer and state.error is None:
                frame, buffer = buffer.split("\n\n", 1)
                data = "\n".join(line[6:] for line in frame.splitlines() if line.startswith("data: "))
                if not data or data == "[DONE]":
                    continue
                try:
                    events = state.feed(data)
                except (AttributeError, KeyError, TypeError, ValueError):
                    state.error = "Malformed upstream stream"
                    break
                for event in events:
                    yield event
        final: list[bytes] = []
        if state.error is None and state.reason is None:
            state.error = "Upstream stream ended without a finish reason"
        if state.error is None:
            try:
                final = state.finish()
            except ValueError:
                state.error = "Malformed upstream stream"
        if state.error is not None:
            yield _sse("error", error={"type": "api_error", "message": state.error})
            return
        for event in final:
            yield event
    finally:
        closer = getattr(response.body_iterator, "aclose", None)
        if closer is not None:
            await closer()


@router.post("/messages/", include_in_schema=False)
@router.post("/messages")
async def messages(request: Request, context: ProxyContext = Depends(get_proxy_context)) -> Response:
    # Authenticate before parsing input; an Anthropic x-api-key is the same codex-lb client key.
    authorization = request.headers.get("authorization")
    native_key = request.headers.get("x-api-key")
    if native_key:
        if authorization and authorization != "Bearer " + native_key:
            return error_response(401, "Conflicting API keys", "authentication_error")
        authorization = "Bearer " + native_key
    try:
        api_key: ApiKeyData = await validate_required_proxy_api_key_authorization(authorization)
        payload = MessagesRequest.model_validate(await request.json())
        chat = to_chat(payload)
        response = await v1_chat_completions(request, chat, context, api_key)
    except AppError as exc:
        return error_response(exc.status_code, str(exc))
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        return error_response(400, str(exc))
    if response.status_code >= 400:
        return _upstream_error(response)
    if isinstance(response, StreamingResponse):
        return StreamingResponse(
            from_chat_stream(response, payload.model), media_type="text/event-stream", headers=dict(response.headers)
        )
    try:
        message = from_chat(json.loads(bytes(response.body)), payload.model)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return error_response(502, "Malformed upstream response", "api_error")
    return JSONResponse(
        message,
        headers={
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        },
    )
