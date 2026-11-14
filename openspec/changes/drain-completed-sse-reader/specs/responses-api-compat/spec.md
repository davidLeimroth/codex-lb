## ADDED Requirements

### Requirement: SSE heartbeat reader cleanup

The proxy MUST observe the result or exception of its owned SSE reader task
when a downstream stream closes, including when the reader finishes while a
heartbeat is yielded. Cleanup MUST preserve normal upstream error propagation
and cancellation of a downstream consumer.

#### Scenario: Upstream finishes while heartbeat is yielded
- **WHEN** the upstream reader finishes or raises after a heartbeat is yielded
- **AND** the downstream closes before requesting the next frame
- **THEN** cleanup observes the reader's completion without an unhandled task exception

#### Scenario: Consumer continues after an upstream failure
- **WHEN** a consumer requests the next frame after the upstream raises
- **THEN** the upstream exception propagates to the consumer

#### Scenario: Consumer is cancelled while upstream is idle
- **WHEN** the downstream consumer is cancelled with an upstream read pending
- **THEN** the pending read is cancelled and drained
- **AND** cancellation propagates to the consumer
