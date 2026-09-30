from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _Request(BaseModel):
    """Unknown fields are rejected so unsupported features fail explicitly.

    user/metadata/prompt_cache_key/service_tier/safety_identifier are accepted and ignored:
    they carry no generation semantics for a stateless bridge. Responses additionally
    accepts Codex client_metadata without including it in generation.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    stream: bool = False
    tools: list[dict[str, JsonValue]] | None = None
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    temperature: float | None = None
    top_p: float | None = None
    user: str | None = None
    metadata: dict[str, JsonValue] | None = None
    prompt_cache_key: str | None = None
    service_tier: str | None = None
    safety_identifier: str | None = None
    store: bool | None = None


class ChatCompletionRequest(_Request):
    messages: list[dict[str, JsonValue]]
    stream_options: dict[str, JsonValue] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=128000)
    reasoning_effort: str | None = None
    n: int | None = None
    response_format: dict[str, JsonValue] | None = None


class ResponsesRequest(_Request):
    input: str | list[dict[str, JsonValue]]
    instructions: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=128000)
    reasoning: dict[str, JsonValue] | None = None
    text: dict[str, JsonValue] | None = None
    include: list[str] | None = None
    truncation: Literal["auto", "disabled"] | None = None
    previous_response_id: str | None = None
    background: bool | None = None
    conversation: JsonValue = None
    # Codex client identifiers have no generation semantics and never enter the CLI prompt.
    client_metadata: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class ClientTool:
    name: str
    description: str
    parameters: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Turn:
    role: Literal["user", "assistant", "developer", "tool_call", "tool_result"]
    text: str
    call_id: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Protocol-neutral request handed to the CLI runner."""

    model: str
    system: str
    turns: list[Turn]
    tools: list[ClientTool]
    require_tool: bool = False
    single_tool_call: bool = False
    max_output_tokens: int | None = None
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


class BridgeError(Exception):
    """``usage`` carries subscription tokens already consumed when a generation fails part way."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        cooldown: bool = False,
        retry_after: int | None = None,
        error_type: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.cooldown = cooldown
        self.retry_after = retry_after
        self.error_type = error_type or code
        self.usage = usage
