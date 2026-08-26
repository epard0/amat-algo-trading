# Threat model for the backtesting repository

## Protected properties

- credentials never enter Claude-visible or version-controlled files;
- raw and validated evidence cannot be silently rewritten;
- no code path can submit a broker order during this phase;
- external content cannot execute merely because it was downloaded;
- reports cannot misrepresent stale, partial, or cherry-picked results;
- commands cannot escape the project sandbox or destroy repository state.

## Primary threats

1. **Prompt or skill supply-chain injection:** a downloaded skill grants broad tools, runs shell preprocessing, or overrides scientific rules.
2. **Data poisoning or schema drift:** provider changes, partial pagination, bad adjustments, stale bars, or malicious artifacts corrupt results.
3. **Credential leakage:** keys appear in settings, notebooks, traces, fixtures, shell output, or reports.
4. **Unsafe deserialization:** pickle/joblib/notebook content executes code.
5. **Live-trading scope creep:** an “adapter test” starts submitting paper or real orders.
6. **Destructive automation:** force-push, hard reset, recursive deletion, or raw-data overwrite destroys evidence.
7. **Scientific misrepresentation:** holdout peeking, benchmark switching, cost omission, or experiment suppression creates false confidence.

## Controls in this bundle

- deny/ask rules plus OS-level Bash sandboxing;
- `PreToolUse` checks for destructive commands, protected paths, and order endpoints;
- `PostToolUse` syntax/frontmatter/secret checks;
- `Stop` verification for malformed configuration and changed Python syntax;
- immutable-data and experiment-governance rules;
- isolated worktrees for implementation agents;
- external-skill intake workflow with source, license, script, hook, tool, and assumption review;
- explicit backtest-only environment flags and status line.

## Residual risk

Permission patterns and hooks are defense in depth, not a formal security boundary. A trusted user can alter project settings, an unsupported platform may run without sandboxing, and dependency code can be malicious. Review workspace trust prompts, dependency changes, and external skills manually.
