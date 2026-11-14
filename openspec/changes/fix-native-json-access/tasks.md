## Implementation and verification

- [x] Reproduce null access fields using a real native Uvicorn HTTP request.
- [x] Decode native access fields, preserve explicit fields and exclude request query strings.
- [x] Verify malformed and absent arguments do not expose raw data or break logging.
- [ ] Apply the exact runtime patch and verify the regression inside the image.
- [ ] Complete focused checks and urgent Opus review through the Claude CLI subscription route.
- [ ] Merge the personal-fork PR and publish the pinned replacement candidate.
