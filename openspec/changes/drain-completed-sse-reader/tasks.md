## Implementation and verification

- [x] Reproduce exhaustion and upstream-error cleanup races before the fix.
- [x] Always observe the owned SSE reader task during cleanup.
- [x] Verify normal exceptions and downstream cancellation still propagate.
- [x] Add exact-context runtime patch and run source and image regressions.
- [x] Complete Opus CLI review of the cleanup and Python compatibility changes.
- [ ] Complete final dependency review, required checks, merge and publish the immutable candidate.
- [ ] Promote and verify production after the shared stateful qualification blocker is resolved.
