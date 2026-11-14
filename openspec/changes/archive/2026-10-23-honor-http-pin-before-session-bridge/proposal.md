## Why

Native Codex HTTP requests enter the session bridge before the existing per-key `always_http` override is considered. The bridge can repeatedly inject a rejected continuation anchor, surfacing as an upstream WebSocket disconnect despite the operator's HTTP pin.

## What Changes

- Bypass the HTTP-to-WebSocket session bridge when the existing transport precedence selects an explicit HTTP pin.
- Preserve explicit upstream WebSocket precedence and existing smart bridge behavior.
- Exercise native and compatible Responses routes with repeated full-history turns, and apply the same source delta to the preserved production runtime.

## Impact

- Affects `responses-api-compat` and the personal fork's preserved-runtime image build.
- Adds no settings or migrations. HTTP-pinned requests retain their full input and use ordinary streaming settlement and account ownership rules.
