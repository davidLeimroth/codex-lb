## ADDED Requirements

### Requirement: Explicit HTTP pins bypass the session bridge

The proxy MUST evaluate existing HTTP transport pins before sending an HTTP/SSE Responses request through the WebSocket session bridge. An explicit upstream HTTP selection MUST bypass the bridge. When upstream transport is automatic, the effective per-key or global `always_http` policy (including the global `pinned` alias) MUST bypass the bridge. An explicit upstream WebSocket selection MUST retain precedence over those policy values. The existing smart bridge behavior MUST remain unchanged.

#### Scenario: Native Codex key is pinned to HTTP

- **GIVEN** the bridge is enabled and upstream transport is automatic
- **AND** a Codex API key has `transport_policy_override = always_http`
- **WHEN** the client sends repeated full-history HTTP/SSE turns on the same thread
- **THEN** every turn uses ordinary upstream HTTP streaming
- **AND** the bridge cannot inject a stored continuation reference
- **AND** the client's complete input and any explicit continuation reference remain unchanged

#### Scenario: Explicit WebSocket selection wins

- **GIVEN** an HTTP-pinned key and explicit upstream WebSocket transport
- **WHEN** bridge admission is evaluated
- **THEN** the request remains eligible for the bridge
