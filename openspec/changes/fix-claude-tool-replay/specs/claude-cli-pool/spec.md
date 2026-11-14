## ADDED Requirements

### Requirement: Replayed tool names match native CLI tools
The worker SHALL render historical client function names and forced-tool instructions in the same native MCP namespace advertised to the CLI. External requests and responses SHALL retain the caller's original function names. The worker SHALL continue rejecting unprefixed tool calls and SHALL never execute client tools.

#### Scenario: A client returns a file tool result
- **WHEN** a Chat or Responses request includes a historical function call and its result
- **THEN** the CLI prompt names that historical call with the private client MCP prefix
- **AND** returned native calls use the original client function names in the response

#### Scenario: A function name already contains the MCP prefix
- **WHEN** a caller's original function name starts with the private MCP prefix
- **THEN** the worker adds the native namespace once and removes it once on output, preserving the original name

#### Scenario: The model calls a bare function
- **WHEN** the CLI emits a function without the private client MCP prefix
- **THEN** the worker rejects the request with `claude_cli_unexpected_tool`
