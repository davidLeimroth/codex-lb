## Why

OpenAI returns `turn_rate_limit_exceeded` with “Codex is at capacity, please try again later”. The proxy currently recognizes only “Selected model is at capacity”, so otherwise replayable startup failures bypass its bounded capacity wait and retry.

## What Changes

- Recognize the observed Codex capacity wording through the existing model capacity classifier.
- Keep deadline, response ownership, file pinning and visible-output replay guards unchanged.
- Verify the exact upstream envelope through the HTTP bridge and preserve the source delta in the deployed image.

## Impact

- Capability: responses-api-compat.
- No schema, settings, model or account changes.
