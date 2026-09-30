"""Build-time check that the pinned codex-lb runtime still provides what the Messages adapter calls.

``python -m app.modules.claude_messages.compat`` runs in the ``runtime`` image stage, so a base image
whose internals drifted fails the image build instead of the primary proxy's startup.
"""

from __future__ import annotations

import inspect


def check() -> None:
    from app.claude_entrypoint import app
    from app.core.auth.dependencies import validate_required_proxy_api_key_authorization
    from app.modules.claude_messages.api import MessagesRequest, to_chat
    from app.modules.proxy import api as proxy_api

    chat_params = list(inspect.signature(proxy_api.v1_chat_completions).parameters)
    if chat_params[:4] != ["request", "payload", "context", "api_key"]:
        raise RuntimeError(f"v1_chat_completions signature changed: {chat_params}")
    auth_params = list(inspect.signature(validate_required_proxy_api_key_authorization).parameters)
    if auth_params != ["authorization"]:
        raise RuntimeError(f"validate_required_proxy_api_key_authorization signature changed: {auth_params}")
    if not callable(getattr(proxy_api, "_select_chat_model_source", None)):
        raise RuntimeError("codex-lb no longer routes chat completions through _select_chat_model_source")
    paths = {getattr(route, "path", None) for route in app.routes}
    missing = {"/v1/messages", "/v1/messages/", "/v1/chat/completions"} - paths
    if missing:
        raise RuntimeError(f"routes missing from the runtime app: {sorted(missing)}")
    sample = {
        "model": "claude-opus-5-5",
        "max_tokens": 16,
        "stream": True,
        "system": "s",
        "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
        "tools": [{"name": "t", "input_schema": {"type": "object", "properties": {}}}],
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "t", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "r"}]},
        ],
    }
    chat = to_chat(MessagesRequest.model_validate(sample))  # the runtime ChatCompletionsRequest must accept it
    if chat.parallel_tool_calls is not False or chat.messages is None or len(chat.messages) != 4:
        raise RuntimeError("ChatCompletionsRequest no longer preserves the translated Messages request")


if __name__ == "__main__":
    check()
    print("claude_messages runtime compatibility: ok")
