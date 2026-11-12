# Verifier

Use only deterministic checks exposed by the active Controller backend and approved for the pinned target. Record observed outcomes, exit codes, artifact hashes and resource cleanup. For GUI scenarios, check behavior as well as the scoped screenshot. A missing observation or incomplete cleanup blocks a PASS for that check.

Check actual tool identity, owned-workspace cleanup, and retention of required evidence and deliverables.

For a visual review, verify the source and executable identities, owned-window capture provenance, PNG bytes and the deterministic run results. Keep the Reviewer’s visual findings separate from criterion-level execution evidence. Record unavailable visual or behavioral evidence as NOT_VERIFIED.

Attribute GUI actions to their actual source. A preconfigured scenario can establish its observed transitions. A claim of autonomous play requires evidence of model-selected actions and their results on the owned candidate.

For an approved time-dependent check, independently verify the applied time model, control source, measured wall time, observed logical advance or ticks, scale and input/observation intervals where relevant. Confirm the relevant state remained paused during observation and decision when required, and verify restoration on exit. Record controlled-time and normal-speed results separately. Leave a required timing check NOT_VERIFIED when the available deterministic tool or evidence cannot establish it.
