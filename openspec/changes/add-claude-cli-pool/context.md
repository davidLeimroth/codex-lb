# Change context: add-claude-cli-pool

Stable design notes live in [the capability context](../../specs/claude-cli-pool/context.md). This file records change-level decisions and the upstream update procedure required by "Upstream updates remain available".

## Upstream pin and update procedure

- The original repository stays the GitHub fork parent. `main` tracks upstream unchanged. Deployment uses a separate branch holding exactly one customization commit on the recorded upstream base (currently v1.24.0, `84fde5a1`).
- Locally, `origin` is the personal fork and the upstream remote's push URL is disabled (for example `git remote set-url --push upstream DISABLED`). Nothing is proposed or pushed to Soju06/codex-lb.
- To update: fetch the next upstream release, create a new update branch from that pinned commit, and cherry-pick the current customization commit. Resolve conflicts, then update the runtime base digest in `Dockerfile.claude` to an image built from the same upstream commit (plus any cluster-only fixes it must keep).
- Validate: targeted unit and integration tests, lint and types, a `Dockerfile.claude` build of both targets (the runtime stage runs the compatibility check), `tests/integration/test_claude_messages.py` against the new base, Claude CLI review, and cluster end-to-end checks. Squash fixes into the single customization commit.
- When bumping `CLAUDE_CODE_VERSION`, copy both `platforms.linux-x64.checksum` and `platforms.linux-arm64.checksum` from the official release manifest into the sha256 build arguments.
- Publish new image tags, pin their digests in Talos GitOps, and keep the previous pins for rollback. The deployment record lists source/base commits, image digests and verification evidence.

## Review follow-up (2026-09-30)

A Claude CLI Opus 5.5 review raised 17 findings. The fixes cover the Sonnet model ID, refusal of paid extra usage, streaming disconnect before first output, the build-time runtime compatibility check, in-band partial usage, sequential Messages blocks, transcript tag injection, 400 mapping for client errors, limited-key and streamed Messages settlement tests, `response.incomplete`, Anthropic field compatibility, cache-creation and refusal mapping, Messages error envelopes, health checks that respect account exclusivity, pinned CLI checksums without git, and the OpenSpec gaps. A follow-up core model-source fix, scoped to source stream error recognition and settlement, now logs in-band failures as `error` with nonzero settled usage. Limited-key and unlimited-key Chat and Responses streams have route-level coverage.

A second core model-source fix cancels upstream Chat and Responses requests when the client disconnects before any response starts; see the capability context. It stays inside the same two source-patch files (`app/modules/model_sources/forwarding.py`, `app/modules/proxy/api.py`) and leaves native OpenAI account routing, embeddings and audio untouched. The runtime patch must be regenerated to include it.

The `sonnet` alias now maps to `claude-sonnet-5-5` provisionally; the served model IDs must be confirmed against the real CLI before deployment.
