## Why

JSON access logs currently contain null client, request and status fields for
native Uvicorn records because those values are supplied as positional arguments.
This prevents operators from checking actual HTTP request coverage.

## What Changes

- Decode the native Uvicorn five-argument access record.
- Preserve explicitly populated access fields and strip query strings from logged request targets.
- Leave malformed or missing native fields null without logging raw arguments.
- Apply the same exact-context patch to the preserved runtime image.
- Use focused logging checks for this exact change, then build and smoke-test the
  reviewed runtime merge once. Keep broader checks for other changes and reuse
  the unchanged worker's previously published image.

## Impact

Affected spec: proxy-runtime-observability. No routing, authentication, database,
or subscription billing changes.
