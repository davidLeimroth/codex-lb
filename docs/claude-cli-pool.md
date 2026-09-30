# Claude Code subscriptions through codex-lb

[Owning specification](../openspec/specs/claude-cli-pool/spec.md) · [Design, limitations and research](../openspec/specs/claude-cli-pool/context.md)

Requests flow from an OpenAI or Anthropic client through codex-lb's authenticated API to an internal CLI worker, then through the real Claude Code CLI to Anthropic. Account credentials live in the cluster and are never sent to clients. The worker is an ordinary codex-lb model source, preserving key restrictions, quotas and request logs.

## Cluster accounts

The Talos workload is `claude-cli` in namespace `codex-lb`. One process per account runs at a time. Each subscription has its own directory in the worker's persistent volume. To add another subscription:

1. Run `claude setup-token` and authorize that subscription in the browser. This produces a separate one-year inference token without sharing local refresh credentials.
2. Add the token to the encrypted `claude-cli-subscriptions` Secret under a distinct account key, such as `work-max`. Keep `bridge-api-key` unchanged.
3. Reconcile the encrypted secret and restart the singleton worker. Its init container creates the independent account directory. `/health` reports ready account count without exposing names or credentials.
4. Turn off paid extra usage for that subscription in its Claude account settings. The worker refuses requests once the CLI reports extra usage, but it only learns this after a request has started, so a few paid tokens can still be spent.
5. Verify a real request and record the token's rotation deadline. Replace the encrypted token before expiry and restart the worker.

The Secret is encrypted with the operator and cluster SOPS keys. The internal worker is reachable only from codex-lb on port 2456. No dashboard or public route exposes it. Multiple directories for the same login share the same subscription quota; adding process replicas does not add quota. Capacity is one request per account with no queue: extra concurrent requests get 429 with `Retry-After: 1`.

Streams send nothing, not even headers, until the first output token (or, with `tool_choice: required`, until the tool call is verified). Keep the model source timeout in codex-lb and every ingress idle timeout above the worker timeout (`CLAUDE_BRIDGE_TIMEOUT_SECONDS`, 600 by default).

## Clients

Use your existing codex-lb API key:

- OpenAI SDKs: base URL `https://codex.lmrth.xyz/v1`, model `claude-opus-5-5` (also `claude-sonnet-5-5`, `claude-haiku-4-5`), Chat Completions or HTTP Responses.
- Anthropic SDKs: base URL `https://codex.lmrth.xyz`, `/v1/messages`, API key header `x-api-key` or Bearer authorization, same model ID.
- Streaming uses each protocol's SSE events. Caller-defined function tools run in the caller; the cluster worker does not execute them.

Send full conversation history on every turn. History is serialized for Claude CLI, so this is not byte-for-byte native Anthropic request forwarding. Unsupported request features return explicit 400 errors; see the design page. Responses storage/previous_response_id and native Codex WebSockets are unsupported for Claude models. The existing OpenAI account pool continues using its current routes.

## Updating upstream

The original repository remains the GitHub fork parent. The deployment branch contains one customization commit on the recorded upstream base, with no upstream PR. Keep `main` tracking upstream; use a separate deployment branch. Locally, upstream push is disabled and origin is the personal fork.

For each update, fetch the next upstream release, create a new update branch from that pinned commit and cherry-pick the current customization commit. Regenerate the tracked runtime patch with `git diff --full-index UPSTREAM_BASE HEAD -- app/modules/model_sources/forwarding.py app/modules/proxy/api.py > patches/claude-source-stream-errors.patch`; the image build refuses any context mismatch. Resolve any conflicts, update the immutable runtime base image in `Dockerfile.claude`, then rerun tests (including `tests/integration/test_claude_messages.py` against the new base), Opus CLI review and cluster end-to-end checks. Squash any fixes into one customization commit. Publish new image tags and pin their digests in Talos GitOps before promotion. Keep old deployment pins for rollback. The full procedure is in the [change context](../openspec/changes/add-claude-cli-pool/context.md).

The `runtime` build runs `python -m app.modules.claude_messages.compat` and fails if the pinned base no longer provides what the Messages adapter calls. The worker build verifies the Claude CLI binary against the `CLAUDE_CODE_SHA256_LINUX_X64` / `CLAUDE_CODE_SHA256_LINUX_ARM64` build arguments. When bumping `CLAUDE_CODE_VERSION`, copy both checksums from `https://downloads.claude.ai/claude-code-releases/<version>/manifest.json`.

Build only the additive targets:

```sh
docker buildx build --platform linux/amd64 -f Dockerfile.claude --target runtime -t YOUR_RUNTIME_IMAGE --push .
docker buildx build --platform linux/amd64 -f Dockerfile.claude --target worker -t YOUR_WORKER_IMAGE --push .
```

Do not publish or merge these changes to Soju06/codex-lb. The deployment record includes source/base commits, image digests, and verification evidence. The fork also carries a model-source fix that records in-band stream failures, settles reported partial usage, and closes upstream work when clients disconnect before response headers or during limited-key buffering.

The authenticated cluster worker has served all three models through public codex-lb with paid Usage credits disabled. OpenAI Chat/Responses and Anthropic Messages passed JSON, SSE and native function round trips; Responses function-result replay and parallel calls passed too. The cancellation change passes 263 focused tests. The deployment record tracks immutable image promotion and dsh verification.
