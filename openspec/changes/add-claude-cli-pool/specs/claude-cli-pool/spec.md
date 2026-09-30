## ADDED Requirements
### Requirement: Actual subscription CLI execution
The optional worker SHALL invoke the installed Claude Code CLI for every model generation, using its own authenticated subscription configuration directory. It SHALL NOT directly call Anthropic inference or silently fall back to API-key billing or paid extra usage.
#### Scenario: Subscription generation
- **WHEN** a Claude model is requested through the registered source
- **THEN** the worker launches Claude CLI with subscription authentication
#### Scenario: API-key authentication refused
- **WHEN** the CLI reports an API-key authentication source
- **THEN** the worker terminates the CLI, cools the account down and does not return its output
#### Scenario: Paid extra usage refused
- **WHEN** the CLI reports that the account is using paid extra usage (overage)
- **THEN** the worker terminates the CLI, cools the account down and fails over before output or returns 429

### Requirement: Compatible authenticated APIs
OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages SHALL preserve codex-lb API-key validation, model restrictions, usage settlement, and error propagation. Unsupported features SHALL return explicit errors.
#### Scenario: Native tool round trip
- **WHEN** a client supplies function tools and returns a tool result
- **THEN** the worker returns native tool calls for client execution and includes the result in the next request
#### Scenario: Client tools are never executed
- **WHEN** the model calls a client-supplied tool
- **THEN** the worker returns the tool call and terminates the CLI process group without executing or answering the tool
#### Scenario: Unsupported input rejected
- **WHEN** a request carries images, non-default sampling, stored-response state, provider-hosted tools or unknown fields
- **THEN** it is rejected with a 400 error before any CLI process starts
#### Scenario: Prompt too long
- **WHEN** the CLI reports that the prompt exceeds the model context
- **THEN** the request fails with 400 `context_length_exceeded`, without account cooldown or failover
#### Scenario: Failure after output
- **WHEN** a streamed generation fails after output was sent
- **THEN** the error is sent in-band, preceded by the usage the failed generation consumed, and the Messages adapter drains the source stream so codex-lb settles that usage
#### Scenario: In-band source failure is logged as an error
- **WHEN** a model-source stream ends after a Chat error frame, a Responses `error` event, `response.failed`, or a `response.incomplete` that carries an error object, for a limited or unlimited API key
- **THEN** codex-lb forwards the frames to the client, settles the reported usage exactly once, and records the request log as `error` with the upstream error code and message
- **AND** a `response.incomplete` without an error object is recorded as `success`
- **AND** a stream that fails after reporting usage settles that usage instead of releasing the reservation, while a client disconnect still releases it
#### Scenario: Client disconnect before a source response starts
- **WHEN** a client disconnects from a model-source Chat Completions or Responses request, streaming or not, for a limited or unlimited API key, while codex-lb is still waiting for the source's response headers, its non-streaming body, or a limited key's buffered stream
- **THEN** codex-lb cancels and awaits the upstream HTTP request so the source connection closes promptly, releases the unsettled reservation, and records the request log as `cancelled` with `client_disconnected`
- **AND** a reservation that was already settled is never released afterwards
#### Scenario: Sequential Messages content blocks
- **WHEN** a streamed Messages response contains text and tool calls, including interleaved parallel tool-call deltas
- **THEN** every content block is stopped before the next one starts and each tool call arrives as one complete block

### Requirement: Worker isolation and lifecycle
Each subscription SHALL have a separate writable credential directory. Worker capacity SHALL be bounded to one CLI process per account; disconnects, timeout and cancellation SHALL reap subprocesses and request files. Authentication status SHALL gate readiness.
#### Scenario: Interrupted request
- **WHEN** a request disconnects during CLI execution, including before the first output byte
- **THEN** owned CLI and MCP subprocesses are terminated and capacity is released
#### Scenario: Account-scoped subscription token
- **WHEN** an account directory contains a `.oauth-token` file
- **THEN** only that account's CLI processes receive it, and a malformed or non-subscription token makes the account unready and unused
#### Scenario: Health checks respect account exclusivity
- **WHEN** readiness is probed while an account runs a generation, or a generation starts during the account's status check
- **THEN** the busy account is not checked concurrently and the generation waits for the in-flight check

### Requirement: Verified deployment images
The runtime image build SHALL fail when the pinned codex-lb base no longer provides the interfaces the Messages adapter calls. The worker image SHALL install the Claude CLI only after verifying it against a sha256 pinned in the build definition.
#### Scenario: Drifted runtime base
- **WHEN** the pinned runtime image lacks a route, signature or request field the adapter depends on
- **THEN** the image build fails instead of the primary proxy's startup

### Requirement: Upstream updates remain available
The fork SHALL retain upstream Git history, record the pinned base and customization commit, and document rebasing the customization onto updated upstream releases. Upstream pushes SHALL be disabled locally.
#### Scenario: Upstream update
- **WHEN** an operator updates the pinned upstream base
- **THEN** the customization can be replayed and validated without sending changes upstream
