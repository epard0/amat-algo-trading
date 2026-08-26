---
name: run-quality-gates
description: Run the AMAT bundle and repository verification gates without modifying files or installing dependencies.
argument-hint: "[fast|full]"
disable-model-invocation: true
context: fork
agent: quality-gate-runner
background: false
effort: medium
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash(python3 ${CLAUDE_PROJECT_DIR}/.claude/scripts/validate_bundle.py)
  - Bash(bash ${CLAUDE_PROJECT_DIR}/.claude/scripts/quality_gate.sh *)
---

Run verification mode `$ARGUMENTS`.

Use `fast` when no mode is supplied. Execute:

```bash
python3 "${CLAUDE_PROJECT_DIR}/.claude/scripts/validate_bundle.py"
bash "${CLAUDE_PROJECT_DIR}/.claude/scripts/quality_gate.sh" <mode>
```

Do not repair failures in this workflow. Report each check as passed, failed, skipped, or unavailable, with exact commands and the first actionable evidence. A missing dependency or tool is not a passing check.
