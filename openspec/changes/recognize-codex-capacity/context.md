# Release evidence — 2026-10-25

[Source PR #2](https://github.com/davidLeimroth/codex-lb/pull/2) merged as
`c664bae529700d0b5cd0b5804f804a880cfad46a` after current-head review and
[CI](https://github.com/davidLeimroth/codex-lb/actions/runs/40220223460).
All 31 GitHub checks passed or were skipped by the existing change filters;
there were no unresolved review threads. This completes release task 2.1.

[Talos PR #747](https://github.com/davidLeimroth/talos/pull/747) published
`ghcr.io/davidleimroth/talos/codex-lb@sha256:f20de556d082c706c845b09780b21f7047649497ecf655ead0762c4afcef1c73`.
All 14 image tests passed against that immutable artifact on linux/amd64 with
network disabled: five capacity/context tests, four signed-forwarding tests,
and five durable-owner-routing tests. The capacity tests mock the replay
executor and do not establish end-to-end upstream recovery.

Release task 2.2 remains incomplete. The shared Talos upgrade registry has no
registered codex-lb staging recipe or execution path. Its stateful contract
requires isolated baseline/candidate HTTP, read, write, login, restoration,
migration, isolation and protocol checks, complete telemetry, a 1,800-second
observation and reviewed promotion. The protected hosting prerequisites and
live app/storage hold remain in effect. Image tests cannot replace those gates.
The production image pin has not changed, and live recovery of this candidate
has not been verified. Keep this change active until qualified rollout is proven.
