## Implementation and verification

- [x] Reproduce exhaustion and upstream-error cleanup races before the fix.
- [x] Always observe the owned SSE reader task during cleanup.
- [x] Verify normal exceptions and downstream cancellation still propagate.
- [ ] Add exact-context runtime patch and run source and image regressions.
- [ ] Complete Opus CLI review, checks, merge and immutable rollout verification.
