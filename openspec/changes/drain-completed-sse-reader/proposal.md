## Why

Closing an SSE stream while a heartbeat is yielded can leave its completed
upstream reader task's exception unobserved. Production logs report an asyncio
"Task exception was never retrieved" error for normal upstream exhaustion.

## What Changes

- Drain the owned reader task during cleanup even when it has already finished.
- Preserve heartbeat frames, normal exception propagation and cancellation.
- Apply the same exact-context change to the preserved production runtime.

## Impact

- Affected spec: responses-api-compat.
- Affected code: app/core/utils/sse.py and preserved-runtime image build.
- No routing, authentication, billing, storage or database changes.
