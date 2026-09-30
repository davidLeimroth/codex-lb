## Why
Route requests to Anthropic through authenticated Claude Code CLI processes in the cluster, using existing subscriptions and codex-lb access controls.

## What Changes
- Add an optional CLI-backed model source supporting OpenAI Chat Completions and Responses.
- Add an Anthropic Messages adapter through the existing authenticated proxy path.
- Isolate subscription credentials per CLI worker, bound capacity, cancel processes on disconnect, and keep client tools client-owned.
- Keep upstream lineage, a pinned base, and a single deployment customization commit in the personal fork.

## Impact
No schema migration, default route change, or upstream pull request. Cluster integration preserves the deployed native-thread fixes and model-catalog override.
