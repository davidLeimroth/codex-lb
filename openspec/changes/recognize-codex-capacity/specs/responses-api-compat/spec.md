## ADDED Requirements

### Requirement: Codex capacity refusals use existing bounded recovery

The proxy MUST recognize the upstream message “Codex is at capacity, please try again later” as a model capacity refusal, including the `turn_rate_limit_exceeded` code. It MUST apply existing bounded wait and replay rules only when the request is already eligible for replay. It MUST preserve the original deadline, account/file ownership and visible-output guards.

#### Scenario: Codex startup refusal waits before retry

- **WHEN** a replayable HTTP bridge request receives `turn_rate_limit_exceeded` with the Codex capacity message before upstream acceptance or visible output
- **THEN** the proxy applies the existing model capacity wait before its bounded retry

#### Scenario: Accepted or anchored work is preserved

- **WHEN** the same refusal belongs to a request whose existing ownership or replay guards prohibit replay
- **THEN** the proxy surfaces the failure without replaying the accepted work

#### Scenario: Unrelated capacity wording remains unclassified

- **WHEN** an error describes fixed context capacity instead of upstream model availability
- **THEN** the new Codex message marker does not classify it as a model capacity refusal
