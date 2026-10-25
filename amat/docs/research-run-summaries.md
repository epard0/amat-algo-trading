# Automatic LLM explanations after research experiments

For a single-command workflow with a browser comparison report, run `./research 5`.
See [simple research](simple-research.md).

The v5 CLI initializes new sessions with the summary provider chain from
`configs/autoresearch/llm-chain.example.toml`. A different file can be supplied
with `init --summary-chain PATH`; its provider/model targets are stored in the
frozen program. Credentials are read from the environment, never stored there.
The Python initialization API leaves summaries unconfigured unless explicitly
provided, so offline tests and embedded callers do not silently make model calls.

After the baseline and each completed research experiment, the controller asks
an LLM to explain the outcome. This covers built-in search, model-proposed recipes,
manual `run-one` recipes, patterns, weighted/ridge signals, and routers. One review
covers the experiment's folds, controls, and scenario cases; it does not make a
separate model call for each inner fit or control rerun.

The review contains:

- A good/bad/mixed/inconclusive assessment, or baseline classification.
- A headline and plain-language explanation.
- Strengths and weaknesses grounded in the supplied measurements.
- One proposed next experiment.
- Explicit evidence limitations.

The controller supplies the recorded decision, net/stressed performance, failed
gates, fold outcomes, comparison score, uncertainty, key controls, historical
regime results, and hypothetical-scenario failures. It verifies measurement
artifact hashes before sending evidence. The system prompt treats hypotheses
and other supplied prose as untrusted data, not instructions.

The model does not determine retention. `good` is rejected unless the experiment
was retained, and baseline labeling must match the actual record. A wording guard
also rejects selected proof/guarantee claims and assertions that every uncertainty
resample lost. The prompt asks for a new falsifiable experiment rather than a
control already supplied in the evidence. These checks
do not prove every sentence is correct: the output is explicitly marked as LLM
commentary, with provider and model identity, and has no selection authority.

## Availability and budgets

The existing provider failover and daily token ledger apply. Summary calls share
the program's model budget with proposal calls. A provider attempt has a 30-second
deadline and a 1,800-token output cap. Successful reviews are reused when the
session resumes, avoiding duplicate calls for the same experiment.

If keys, quota, budget, or a valid response are unavailable, the completed
experiment remains recorded and the output says **LLM summary unavailable** with
a reason. It never invents a review or labels a deterministic fallback as
LLM-generated. Failed experiments without valid measurements receive an explicit
unavailable record as well.

## Where to read and how to retry

Reviews print in readable text after each experiment. They are saved in:

- `report.md`, below the experiment table;
- `report.json`, in `llm_summaries`;
- `outputs/summaries/experiment-N.json`, with a ledger-recorded hash.

Summary artifacts are separate from the immutable backtest cache. Retrying a
review does not rerun or change a strategy measurement.

```bash
# Resume research; use the program's configured summary chain automatically.
"$RESEARCH_DIR/research.sh" loop --iterations 5

# Use an explicitly selected summary chain for this batch.
"$RESEARCH_DIR/research.sh" loop --iterations 5 --summary-chain /absolute/path/chain.toml

# Retry a missing review, or explicitly request a new one for experiment 2.
"$RESEARCH_DIR/research.sh" summarize --experiment 2 \
  --summary-chain /absolute/path/chain.toml
```

`--chain` controls strategy proposals; `--summary-chain` controls explanatory
reviews. They can use the same file, but supplying a proposal chain does not
silently override a separately configured review chain. Older frozen v4 sessions
do not acquire v5 functionality; create a new session with the current CLI.
