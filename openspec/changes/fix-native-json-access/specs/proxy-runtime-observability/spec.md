## ADDED Requirements

### Requirement: Native JSON access records contain observed HTTP fields
When JSON logging is enabled, the system MUST decode Uvicorn's native access
record into client, request line and numeric status fields. Explicit access
attributes MUST be preserved. Logged request targets MUST exclude query strings.
Missing or malformed positional arguments MUST NOT expose raw arguments or
prevent an access record from being formatted.

#### Scenario: Native HTTP request produces a complete JSON access record
- **WHEN** Uvicorn serves an HTTP request with JSON logging enabled
- **THEN** the access record contains the actual client, method, path, HTTP version and status
- **AND** the request query string is absent from the record

#### Scenario: Non-native record cannot be decoded
- **WHEN** an access record has missing or malformed positional arguments
- **THEN** unspecified access fields remain null
- **AND** the record does not include the raw arguments
