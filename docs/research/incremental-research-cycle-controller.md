# Bounded incremental research cycle controller

`amat/scripts/run_incremental_research_cycle.py` runs a **previously frozen, explicitly configured** study or engineering verification. It does not generate hypotheses, launch alpha research on its own, schedule hourly wakeups, modify genuine strategy defaults, submit broker orders or deploy anything. Codex owns proposal/review and campaign completion.

```sh
amat/.venv/bin/python amat/scripts/run_incremental_research_cycle.py --manifest /absolute/path/cycle.json
```

Create `protocol.json` and `state.json` in a new campaign directory. Protocol and cycle manifest require canonical seals: SHA256 of `json.dumps(value, sort_keys=True, allow_nan=False).encode()`, saved to the same path with suffix `.sha256`. Frozen inputs use ordinary file-byte SHA256. State is durable mutable JSON and has no immutable seal.

A protocol must contain:

```json
{
  "deadline_utc": "2026-10-24T04:40:10Z",
  "max_cycle_seconds": 2400,
  "min_start_remaining_seconds": 1200,
  "promotion_policy_id": "new-user-approved-incremental-policy-v1",
  "enable_receipt_baseline_updates": false
}
```

A starting state has `status: "ready"`, `active_run: null` and `baseline: {"profile_path": "/absolute/profile.toml", "profile_sha256": "<file-byte SHA256>"}`. Keep the existing baseline and existing benchmark evidence when creating a subsequent cycle. Do not clear an unfinished run without checking its durable process identity and evidence.

A cycle manifest looks like:

```json
{
  "schema": "incremental-research-cycle/1",
  "campaign_root": "/absolute/new-campaign-directory",
  "cycle_id": "001-control-engineering",
  "protocol_sha256": "<canonical protocol SHA256>",
  "inputs": [
    {"path": "/absolute/frozen-study-driver.py", "sha256": "<file-byte SHA256>"}
  ],
  "commands": [
    {
      "argv": ["/absolute/python", "/absolute/frozen-study-driver.py", "--fixed-argument"],
      "cwd": "/absolute/study-directory",
      "env": {"PYTHONPATH": "/absolute/frozen/src"},
      "timeout_seconds": 2400
    }
  ]
}
```

Pin every decision-relevant source/config/data artifact in `inputs`, or pin manifests whose driver verifies the entire transitive frozen set. Use absolute executables and working directories. Command arguments are passed as an argv list with `shell=False`; model prose is never evaluated as shell. Captured command output stays in per-cycle logs. Do not configure commands that expose credentials. A configured executable is trusted campaign code, not a sandboxed security boundary.

All commands share a **single monotonic cycle budget**, capped at 40 minutes; each command can have a shorter timeout. Actual UTC remaining before the campaign deadline is also checked before every command. Two seconds are reserved from child execution for process-group cleanup and durable receipt/state publication. The controller does not keep a short successful test artificially running for 20 minutes. Typical research cycles can use the requested 20–40-minute range, while near-deadline or short engineering work should finish sooner. If less than the declared start minimum remains, no experiment starts: state becomes `finalization_required` and Codex must publish the final report and pause the saved automation.

A nonblocking file lock serializes writers. Child processes inherit the lock. The durable run identity includes PID, Linux process-start ticks, boot ID and a random run token. An active matching process or descendant blocks a new writer; a stale interrupted run also blocks new work until reviewed. Timeouts and controller SIGTERM/SIGINT terminate all discovered descendants preserving the inherited `AMAT_RESEARCH_RUN_ID`, including workers that detach into another process group or session. Discovery verifies PID/start-ticks/boot identity before signalling, rescans during cleanup, and refuses to declare success if a parent leaves background descendants. Any surviving matching worker retains `active_run` with `cleanup_review_required` so later cycles remain blocked. SIGKILL cannot run cleanup; the inherited lock and durable identity help prevent a later run from overlapping surviving token-preserving measurement.

**Supported workload boundary:** every subprocess must retain `AMAT_RESEARCH_RUN_ID`; commands may not scrub that variable, submit jobs to external services, switch identities or spawn inaccessible workers. This controller is not a cgroup/container security boundary and does not guarantee a hard arbitrary-process wall-clock limit against hostile or environment-scrubbing descendants. Such workloads require independently verified service/cgroup containment before use. The 40-minute budget applies to the trusted, token-preserving local measurement workload, with bounded termination/receipt grace reserved from child time. Process identity is rechecked immediately before each signal; this runtime lacks pidfd APIs, so a hostile PID-reuse race is outside the supported trust model.

Each output directory is immutable by cycle ID. Logs are `cycles/<cycle_id>/command-NN.log`. `receipt.json` plus canonical seal, `state.json` and append-only `events.jsonl` record actual completion/failure. No successful result is invented for an unobserved orphan. Exit codes: 0 completed or finalization required; 1 failed command/validation; 2 refused malformed or changed input; 75 live writer/measurement; 76 stale run requiring review; 124 time budget exhausted.

## Research baseline promotion

**The current controller never advances a baseline from receipt booleans.** The earlier opt-in prototype is superseded by the October23 source hardening; archived prototype sources and receipts remain historical evidence. `validate_promotion` rejects the legacy path even when `enable_receipt_baseline_updates` is set true, and there is no baseline assignment in this controller.

`amat/scripts/review_incremental_bundle.py` provides a separately reviewed **read-only** consumer. It binds current profile, source, data, calendar, run/study and all-cost native records to actual full/forward/2023/drawdown/QQQ calculations. It accepts no external all-true nonnumeric assertions. Numerical losers are rejected; even a numerical winner remains pending validation. It cannot install a profile, update a default, acquire execution authority or waive a missing gate.

The active campaign retains `promotion_enabled=false`. A future apply path needs actual source-bound nonnumeric verifier producers, a frozen versioned native candidate, independent reproduction, native planning/execution-contract evidence, coherent default updates and a prospectively reviewed protocol amendment. The current numerical bridge and generic lifecycle wrapper do not implement that apply contract. Hashes establish recorded identity, not scientific truth.

The human-authorized incremental policy permits progress against the latest verified baseline without the old all-frontier veto, while preserving every prior comparator and original-anchor risk safeguards. It does not remove costs, forward/2023, drawdown, stress, causality, accounting, uncertainty or native parity requirements. Research promotion remains separate from remote paper rollout, and neither enables live trading.

## Verification

`amat/tests/unit/test_incremental_research_cycle.py` exercises immutable successful completion, baseline preservation, near/expired deadline, malformed shell-text refusal, hard duration maximum, frozen source tampering before/after commands, nonzero failure, process-tree timeout cleanup, shared multi-command budget, normal and detached background descendants, detached timeout/SIGTERM cleanup, PID identity mismatch, live/stale identities, file locking, disabled-by-default receipt promotion and unconditional refusal of legacy boolean receipts even when an old opt-in flag is present. The read-only consumer has separate real-record and adversarial tests; none grants default or execution authority.
