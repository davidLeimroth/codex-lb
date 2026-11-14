# Preserve native client tool names during history replay

Production DSH Sonnet completes a client file write but then emits bare `read` calls after reading serialized history. The CLI advertises only `mcp__client__read`, so the strict bridge guard correctly rejects the call with 502.

Render historical function names and forced-tool instructions in the CLI's native MCP namespace. Preserve external client names, exact tool results, and the guard against non-client tools. Validate the actual Chat/Responses request paths and one focused DSH Sonnet file round trip. No changes to account credentials, billing, runtime source, storage, or tool execution.
